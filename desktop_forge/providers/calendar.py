"""Upcoming events from Evolution Data Server and Thunderbird.

EDS is the calendar store GNOME Calendar and Nautilus already use, so this
picks up the local "Personal" and "Birthdays & Anniversaries" calendars with
no configuration, and a Google or CalDAV account the moment one is added
through Online Accounts.

Thunderbird owns a separate calendar store, so its enabled local and cached
network calendars are merged in from its native or Flatpak profiles.  The
extension still receives one source-neutral list of plain JSON events.
"""
from __future__ import annotations

import datetime
from typing import Any, Callable

import gi

gi.require_version("EDataServer", "1.2")
gi.require_version("ECal", "2.0")
gi.require_version("ICalGLib", "3.0")
from gi.repository import ECal, EDataServer, Gio, GLib, ICalGLib  # noqa: E402

from .base import Provider, ProviderError
from . import thunderbird_calendar

ICAL_UTC = "%Y%m%dT%H%M%SZ"
CONNECT_TIMEOUT = 10


class CalendarProvider(Provider):
    name = "calendar"

    def __init__(self) -> None:
        self._on_change: Callable[[], None] | None = None
        self._clients: dict[str, ECal.Client] = {}
        self._views: dict[str, ECal.ClientView] = {}
        self._thunderbird_monitors: dict[str, Gio.FileMonitor] = {}

    # -- live updates ------------------------------------------------------

    def start(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change

    def stop(self) -> None:
        for view in self._views.values():
            try:
                view.stop()
            except GLib.Error:
                pass
        self._views.clear()
        self._clients.clear()
        for monitor in self._thunderbird_monitors.values():
            monitor.cancel()
        self._thunderbird_monitors.clear()
        self._on_change = None

    def _watch(self, uid: str, client: ECal.Client) -> None:
        """Ask EDS to tell us when this calendar changes.

        Without a view the widget only refreshes on the poll interval, so an
        event added in GNOME Calendar could sit invisible for five minutes.

        The view matches everything rather than the same time range as the
        query: the range moves with the clock and with the days-ahead setting,
        and a view is not cheap to reopen. Over-notifying costs nothing here
        because the daemon collapses a burst of callbacks into a single poll.
        """
        if uid in self._views or self._on_change is None:
            return
        try:
            ok, view = client.get_view_sync("#t", None)
        except GLib.Error:
            return  # Polling still covers it.
        if not ok or view is None:
            return

        for signal in ("objects-added", "objects-modified", "objects-removed"):
            view.connect(signal, lambda *_args: self._changed())
        view.start()
        self._views[uid] = view

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    # -- fetching ----------------------------------------------------------

    def _client_for(self, source: EDataServer.Source) -> ECal.Client:
        """Connect once and keep it: a view has to outlive a single fetch."""
        uid = source.get_uid()
        client = self._clients.get(uid)
        if client is None:
            client = ECal.Client.connect_sync(
                source, ECal.ClientSourceType.EVENTS, CONNECT_TIMEOUT, None
            )
            self._clients[uid] = client
        return client

    def _forget(self, uid: str) -> None:
        view = self._views.pop(uid, None)
        if view is not None:
            try:
                view.stop()
            except GLib.Error:
                pass
        self._clients.pop(uid, None)

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        days_ahead = int(options.get("days_ahead", 14))
        max_events = int(options.get("max_events", 8))

        now = datetime.datetime.now(datetime.timezone.utc)
        end = now + datetime.timedelta(days=days_ahead)

        events: list[dict[str, Any]] = []
        problems: list[str] = []
        calendars: list[str] = []
        answered = False

        try:
            eds = self._fetch_eds(now, end)
            answered = True
            for event in eds["events"]:
                event["source"] = "gnome"
            events.extend(eds["events"])
            problems.extend(f"GNOME — {problem}" for problem in eds["problems"])
            calendars.extend(f"GNOME — {name}" for name in eds["calendars"])
        except ProviderError as exc:
            problems.append(f"GNOME — {exc}")

        try:
            tb_events, tb_calendars, tb_problems, watch_dirs = (
                thunderbird_calendar.fetch_events(now, end)
            )
            self._sync_thunderbird_watches(watch_dirs)
            if tb_calendars:
                answered = True
            events.extend(tb_events)
            calendars.extend(tb_calendars)
            problems.extend(tb_problems)
        except (OSError, ValueError) as exc:
            problems.append(f"Thunderbird — {exc}")

        if not answered and problems:
            raise ProviderError("; ".join(problems))

        events = _deduplicate(events)
        events.sort(key=lambda event: (event["start"] or "", event["summary"]))
        calendars = list(dict.fromkeys(calendars))

        return {
            "events": events[:max_events],
            "total": len(events),
            "days_ahead": days_ahead,
            "problems": problems,
            "calendars": calendars,
            "today": datetime.date.today().isoformat(),
        }

    def _fetch_eds(
        self, now: datetime.datetime, end: datetime.datetime
    ) -> dict[str, Any]:
        # The S-expression is EDS's own query language; occur-in-time-range?
        # makes the server expand recurring events for us, which is the whole
        # reason for querying through EDS rather than parsing the .ics files.
        sexp = (
            f'(occur-in-time-range? (make-time "{now.strftime(ICAL_UTC)}") '
            f'(make-time "{end.strftime(ICAL_UTC)}"))'
        )

        try:
            registry = EDataServer.SourceRegistry.new_sync(None)
        except GLib.Error as exc:
            raise ProviderError(f"Cannot reach the calendar service: {exc.message}") from exc

        events: list[dict[str, Any]] = []
        problems: list[str] = []
        calendars: list[str] = []
        seen: set[str] = set()

        for source in registry.list_sources(EDataServer.SOURCE_EXTENSION_CALENDAR):
            if not source.get_enabled():
                continue
            uid = source.get_uid()
            seen.add(uid)
            calendar_name = source.get_display_name() or "Calendar"
            calendars.append(calendar_name)

            try:
                client = self._client_for(source)
                ok, components = client.get_object_list_as_comps_sync(sexp, None)
            except GLib.Error as exc:
                # One unreachable calendar (a network source that is offline)
                # must not hide the events from the ones that did answer. Drop
                # the cached client so the next poll reconnects rather than
                # reusing a broken one.
                self._forget(uid)
                problems.append(f"{calendar_name}: {exc.message}")
                continue

            self._watch(uid, client)

            if not ok or not components:
                continue
            for component in components:
                event = component_to_event(component, calendar_name, client)
                if event is not None:
                    events.append(event)

        for uid in set(self._clients) - seen:
            self._forget(uid)

        return {
            "events": events,
            "problems": problems,
            "calendars": calendars,
        }

    def _sync_thunderbird_watches(self, directories) -> None:
        """Watch profile and calendar-data directories across atomic writes."""
        wanted = {str(path) for path in directories}
        for path in set(self._thunderbird_monitors) - wanted:
            self._thunderbird_monitors.pop(path).cancel()

        if self._on_change is None:
            return
        for path in wanted - set(self._thunderbird_monitors):
            try:
                monitor = Gio.File.new_for_path(path).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None
                )
            except GLib.Error:
                continue
            monitor.connect("changed", lambda *_args: self._changed())
            self._thunderbird_monitors[path] = monitor


def component_to_event(component, calendar_name: str, client) -> dict[str, Any] | None:
    """Flatten an ECal.Component into plain JSON for the extension.

    The extension never sees an EDS type -- it gets ISO strings and booleans, which
    is all the JavaScript side could consume anyway.
    """
    summary_text = component.get_summary()
    summary = summary_text.get_value() if summary_text else None
    if not summary:
        summary = "(untitled)"

    start_dt = component.get_dtstart()
    if start_dt is None or start_dt.get_value() is None:
        return None

    # An all-day event is stored as a DATE rather than a DATE-TIME. The
    # distinction has to survive to the extension, which shows "Sep 4" instead
    # of "Sep 4, 14:00".
    all_day = bool(start_dt.get_value().is_date())

    start = _iso(start_dt, client)
    if start is None:
        return None

    end_dt = component.get_dtend()
    location_text = component.get_location()

    return {
        "summary": summary,
        "start": start,
        "end": _iso(end_dt, client) if end_dt is not None else None,
        "all_day": all_day,
        "location": location_text or "",
        "calendar": calendar_name,
    }


def _zone_for(component_dt, client) -> ICalGLib.Timezone:
    """The timezone the numbers in this DATE-TIME are expressed in.

    A VEVENT carries its time as wall-clock digits plus a separate TZID; the
    digits alone are meaningless. Resolving the zone through the client first
    matters because a Google or CalDAV calendar ships its own VTIMEZONE with a
    TZID libical has never heard of, which the builtin lookup cannot resolve.
    """
    value = component_dt.get_value()
    if value.is_utc():
        return ICalGLib.Timezone.get_utc_timezone()

    zone = value.get_timezone()
    if zone is not None:
        return zone

    tzid = component_dt.get_tzid()
    if tzid:
        try:
            ok, zone = client.get_timezone_sync(tzid, None)
        except GLib.Error:
            ok, zone = False, None
        if ok and zone is not None:
            return zone
        zone = (
            ICalGLib.Timezone.get_builtin_timezone_from_tzid(tzid)
            or ICalGLib.Timezone.get_builtin_timezone(tzid)
        )
        if zone is not None:
            return zone

    # A floating time means "whatever the clock says wherever you are".
    return client.get_default_timezone() or ICalGLib.Timezone.get_utc_timezone()


def _iso(component_dt, client) -> str | None:
    """ECal.ComponentDateTime -> ISO 8601.

    Timed events come back with an explicit UTC offset. They used to be
    emitted as bare local-looking digits, which the extension then parsed as
    local time -- so an event stored in UTC displayed several hours off.
    """
    value = component_dt.get_value() if component_dt is not None else None
    if value is None:
        return None

    try:
        if value.is_date():
            return datetime.date(
                value.get_year(), value.get_month(), value.get_day()
            ).isoformat()

        stamp = value.as_timet_with_zone(_zone_for(component_dt, client))
        return datetime.datetime.fromtimestamp(
            stamp, datetime.timezone.utc
        ).isoformat()
    except (ValueError, OverflowError, OSError, AttributeError, GLib.Error):
        return None


def _deduplicate(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the same event exposed by both EDS and Thunderbird.

    The source-specific record identifiers are not shared, so the stable
    cross-source identity is the user-visible event content and occurrence
    time.  EDS events are appended first and therefore win an exact tie.
    """
    result = []
    seen = set()
    for event in events:
        key = (
            str(event.get("summary") or "").strip().casefold(),
            event.get("start"),
            event.get("end"),
            bool(event.get("all_day")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result
