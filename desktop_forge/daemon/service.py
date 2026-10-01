"""The polling daemon.

Owns everything the providers deliberately don't: how often to fetch, where
results go, what happens when a fetch fails, and firing reminder
notifications. Runs headless under `systemd --user`.

The extension reads only the state files this writes, so it is never blocked
on the network and never needs an API key of its own.
"""
from __future__ import annotations

import datetime
import functools
import os
import time
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .. import config
from ..providers import reminders as reminders_store
from ..providers.base import Provider, ProviderError
from ..providers.news import NewsProvider, request_signature as news_request_signature
from ..providers.reminders import RemindersProvider
from ..providers.stocks import StocksProvider
from ..providers.system import SystemProvider
from ..providers.todos import STORE_PATH as TODOS_STORE_PATH
from ..providers.todos import TodosProvider
from ..providers.weather import WeatherProvider

PROVIDERS: dict[str, type[Provider]] = {
    "weather": WeatherProvider,
    "stocks": StocksProvider,
    "reminders": RemindersProvider,
    "todos": TodosProvider,
    "news": NewsProvider,
    "system": SystemProvider,
}

# Every provider the config can ask for, including the one that is only
# importable on some machines. _seed_last_good() walks this rather than
# PROVIDERS so a calendar payload written before an upgrade is still restored.
PROVIDER_NAMES = (*PROVIDERS, "calendar")


@functools.lru_cache(maxsize=1)
def _calendar_provider() -> type[Provider] | None:
    """Import the calendar provider on demand.

    providers/calendar.py calls gi.require_version() for the Evolution
    typelibs at import time, and that raises when evolution-data-server is not
    installed. Importing it at module scope took the whole daemon down with it
    and stopped every other widget updating -- where the installer only
    promises that the calendar widget will be unavailable.
    """
    try:
        from ..providers.calendar import CalendarProvider
    except (ImportError, ValueError) as exc:
        log(f"calendar widget unavailable: {exc}")
        return None
    return CalendarProvider


def provider_class(name: str) -> type[Provider] | None:
    return _calendar_provider() if name == "calendar" else PROVIDERS.get(name)

TICK_SECONDS = 1
MIN_INTERVAL = 2

# After a failed fetch, retry sooner than the configured interval. A provider
# can be configured to poll as slowly as an hour, and a transient upstream blip
# (observed: one malformed response from Open-Meteo between successful ones)
# should not leave the widget in an error state until then. Backs off so a
# genuinely dead endpoint isn't hammered.
RETRY_SECONDS = (30, 60, 120, 300)


class Daemon:
    def __init__(self) -> None:
        self._loop = GLib.MainLoop()
        self._config = config.load()
        self._instances: dict[str, Provider] = {}
        self._next_run: dict[str, float] = {}
        self._last_good: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, int] = {}
        self._config_monitor: Gio.FileMonitor | None = None
        self._reminders_monitor: Gio.FileMonitor | None = None
        self._todos_monitor: Gio.FileMonitor | None = None
        self._bus: Gio.DBusConnection | None = None
        self._slideshow = None

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> int:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        self._seed_last_good()
        self._connect_bus()
        self._watch_config()
        self._watch_reminders()
        self._watch_todos()
        self._sync_providers()

        GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, self._quit)  # SIGTERM
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, self._quit)   # SIGINT

        log("started")
        self._loop.run()
        log("stopped")
        return 0

    def _seed_last_good(self) -> None:
        """Recover each provider's last good payload from disk at startup.

        Without this the stale-data fallback only survives as long as the
        process does: the service restarts on failure and at every login, and
        the first fetch after a restart with no network would publish a null
        payload and blank the widget -- even though the previous reading is
        sitting right there in the state file.
        """
        for name in PROVIDER_NAMES:
            state = config.read_json(config.state_path(name))
            if state and state.get("data"):
                self._last_good[name] = state["data"]

    def _quit(self) -> bool:
        self._loop.quit()
        return GLib.SOURCE_REMOVE

    def _connect_bus(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            # Notifications are a nice-to-have; polling must still run without
            # a session bus (e.g. started outside a graphical session).
            log(f"no session bus, notifications disabled: {exc.message}")
            self._bus = None

    def _watch_config(self) -> None:
        """Reload when the GUI rewrites config.json.

        Without this, changing a stock symbol would need a daemon restart --
        the plan's requirement is that it takes effect without one.
        """
        os.makedirs(config.CONFIG_DIR, exist_ok=True)
        gfile = Gio.File.new_for_path(config.CONFIG_PATH)
        try:
            self._config_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error as exc:
            log(f"cannot watch config: {exc.message}")
            return
        self._config_monitor.connect("changed", self._on_config_changed)

    def _watch_reminders(self) -> None:
        """Reload when anything else edits the reminder store.

        Both the settings app and the desktop widget add and complete
        reminders by writing this file. Without a watch the widget would only
        catch up on the next poll -- and the settings app used to paper over
        that by restarting this whole service on every single edit.
        """
        self._reminders_monitor = self._watch_store(
            reminders_store.STORE_PATH, "reminders"
        )

    def _watch_todos(self) -> None:
        self._todos_monitor = self._watch_store(TODOS_STORE_PATH, "todos")

    def _watch_store(self, path: str, provider: str) -> Gio.FileMonitor | None:
        """Watch the containing directory so atomic replacements are not lost."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        gfile = Gio.File.new_for_path(os.path.dirname(path))
        try:
            monitor = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error as exc:
            log(f"cannot watch {provider}: {exc.message}")
            return None

        basename = os.path.basename(path)

        def changed(_monitor, changed_file, other_file, _event) -> None:
            names = {
                candidate.get_basename()
                for candidate in (changed_file, other_file)
                if candidate is not None
            }
            if basename in names:
                self._poll_now(provider)

        monitor.connect("changed", changed)
        return monitor

    def _on_config_changed(self, _monitor, _f, _other, event) -> None:
        if event not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
        ):
            return
        self._config = config.load()
        self._sync_providers()
        # Provider options (including News topics) should take effect on the
        # next tick, not after the old refresh interval expires.
        for name in self._instances:
            self._next_run[name] = 0.0
        log("config reloaded")

    def _sync_providers(self) -> None:
        """Instantiate what the config now needs; drop what it doesn't.

        Reminders are always polled, even with no reminders widget on screen,
        because notifications must still fire for a reminder the user set from
        the GUI.
        """
        wanted = self._config.active_providers() | {"reminders"}

        for name in wanted:
            if name in self._instances:
                continue
            factory = provider_class(name)
            if factory is None:
                continue
            provider = factory()
            self._instances[name] = provider
            self._next_run[name] = 0.0  # fetch immediately
            # A provider that can push changes (the calendar can) gets a way
            # to say so, instead of the widget waiting out the interval.
            provider.start(lambda captured=name: self._poll_now(captured))

        for name in list(self._instances):
            if name not in wanted:
                self._instances.pop(name).stop()
                self._next_run.pop(name, None)

    def _poll_now(self, name: str) -> None:
        """Bring a provider's next poll forward to the next tick.

        Going through _next_run rather than fetching on the spot means a burst
        of notifications -- EDS sends one per object when a view first fills --
        collapses into a single fetch, and that the fetch still happens on the
        main loop rather than inside a signal handler.
        """
        if name in self._instances:
            self._next_run[name] = 0.0

    # -- polling -----------------------------------------------------------

    def _tick(self) -> bool:
        now = time.monotonic()
        for name, provider in list(self._instances.items()):
            if now >= self._next_run.get(name, 0.0):
                options = self._config.provider_options(name)
                interval = max(MIN_INTERVAL, int(options.get("interval", 300)))
                self._poll(name, provider, options)

                failures = self._failures.get(name, 0)
                if failures:
                    retry = RETRY_SECONDS[min(failures - 1, len(RETRY_SECONDS) - 1)]
                    # Never retry slower than the provider would have polled
                    # anyway -- for a 5-second system monitor the backoff is
                    # not an improvement.
                    interval = min(interval, retry)
                self._next_run[name] = time.monotonic() + interval

        self._fire_due_reminders()
        self._advance_slideshow()
        return GLib.SOURCE_CONTINUE

    def _advance_slideshow(self) -> None:
        """Customize → Wallpaper's slideshow; it checks every few seconds
        whether the picture is due and does nothing when switched off."""
        try:
            if self._slideshow is None:
                from ..customize.slideshow import Slideshow
                self._slideshow = Slideshow()
            changed = self._slideshow.tick()
        except Exception as exc:  # noqa: BLE001 - the slideshow must never stop the service
            log(f"wallpaper slideshow: {exc}")
            return
        if changed:
            log(f"wallpaper slideshow: {changed}")

    def _poll(self, name: str, provider: Provider, options: dict[str, Any]) -> None:
        if name == "news" and self._last_good.get(name, {}).get("request_signature") != news_request_signature(options):
            # A failed request must never republish stories that the new
            # settings exclude, including caches created before the upgrade.
            self._last_good.pop(name, None)
        try:
            data = provider.fetch(options)
        except ProviderError as exc:
            self._failures[name] = self._failures.get(name, 0) + 1
            self._write_state(name, error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            # A provider bug must not take the whole daemon down and stop
            # every other widget updating.
            log(f"{name}: unexpected {type(exc).__name__}: {exc}")
            self._failures[name] = self._failures.get(name, 0) + 1
            self._write_state(name, error=f"Internal error: {exc}")
            return

        if name == "news" and data.get("problems"):
            # A partial success still needs a timely retry of missing feeds.
            self._failures[name] = self._failures.get(name, 0) + 1
        else:
            self._failures.pop(name, None)
        self._last_good[name] = data
        self._write_state(name, data=data)

    def _write_state(
        self, name: str, *, data: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        """Persist a provider result.

        On failure the last good payload is re-published with stale=true rather
        than an empty one, so a widget shows yesterday's weather greyed out
        instead of going blank the moment the wifi drops.
        """
        if data is None:
            data = self._last_good.get(name)

        config.write_json(
            config.state_path(name),
            {
                "provider": name,
                "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                "ok": error is None,
                "error": error,
                "stale": error is not None and data is not None,
                "data": data,
            },
        )

    # -- reminders ---------------------------------------------------------

    def _fire_due_reminders(self) -> None:
        due = reminders_store.due_now()
        if not due:
            return

        store = reminders_store.load()
        by_id = {r.get("id"): r for r in store}

        for reminder in due:
            self._notify("Reminder", reminder.get("text", ""))
            stored = by_id.get(reminder.get("id"))
            if stored is None:
                continue
            # advance() re-arms a repeating reminder and clears notified; a
            # one-off just gets marked so it doesn't fire again every tick.
            if not reminders_store.advance(stored):
                stored["notified"] = True

        reminders_store.save(store)

        # Re-poll immediately so the reminders widget reflects the change now,
        # rather than at the end of its normal interval.
        provider = self._instances.get("reminders")
        if provider is not None:
            self._poll("reminders", provider, self._config.provider_options("reminders"))

    def _notify(self, summary: str, body: str) -> None:
        if self._bus is None:
            return
        try:
            self._bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)",
                    (
                        "Desktop Forge", 0, "alarm-symbolic", summary, body,
                        [], {"urgency": GLib.Variant("y", 2)}, -1,
                    ),
                ),
                None, Gio.DBusCallFlags.NONE, 5000, None,
            )
        except GLib.Error as exc:
            log(f"notification failed: {exc.message}")


def log(message: str) -> None:
    # systemd captures stdout into the journal, so plain prints are the right
    # logging mechanism for a --user service.
    print(f"desktop-forged: {message}", flush=True)


def main(argv=None) -> int:
    return Daemon().run()
