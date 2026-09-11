"""Configure the desktop widgets and the GNOME extension that renders them.

This is the only editor for config.json. The Shell extension's own prefs page
deliberately just points here, so there is never a second writer racing this
one for the same file.
"""
from __future__ import annotations

import copy
import threading
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from .. import config
from ..backend import markup
from ..backend import shell_extension as ext
from ..providers.weather import geocode

SAVE_DELAY_MS = 500

# Index order of the "Forecast detail" combo, mapped to the value the weather
# provider and widget agree on.
FORECAST_MODES = ["hourly", "daily", "both"]
MONITOR_LABELS = ["Primary", "Second", "Third", "Fourth"]


class WidgetsPage(Adw.PreferencesPage):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__()
        self._toasts = toast_overlay
        self._config = config.load()
        self._save_source = 0
        self._widget_rows: list[Adw.ExpanderRow] = []

        self._status_group = Adw.PreferencesGroup(title="Setup")
        self.add(self._status_group)

        self._widgets_group = Adw.PreferencesGroup(
            title="Widgets",
            description="Use Edit layout to move and resize widgets on the desktop. "
                        "Positions and sizes are saved as you go.",
        )
        self._widgets_group.set_header_suffix(self._build_header_buttons())
        self.add(self._widgets_group)

        self._appearance_group = Adw.PreferencesGroup(title="Appearance")
        self.add(self._appearance_group)

        self._build_appearance()
        self.refresh_status()
        self._rebuild_widget_list()

    # -- setup / status ----------------------------------------------------

    def refresh_status(self) -> None:
        for child in list(self._status_rows()):
            self._status_group.remove(child)

        state = ext.status()
        self._status_children = []

        if not state.installed:
            self._add_status_row(
                "Widgets are not installed yet",
                f"Adds a GNOME Shell extension for Shell {state.shell_version or '?'} "
                "and starts the background data service.",
                "Install", self._on_install, "suggested-action",
            )
        elif state.needs_relogin:
            self._add_status_row(
                "Log out to finish installing",
                "GNOME Shell on Wayland cannot load a new extension until the "
                "session restarts. Your widgets will appear after you log back "
                "in — everything else is already set up.",
                None, None, None,
            )
        elif state.enabled:
            # Switched on, not loading. That is an error or an out-of-date
            # marker, and ext.status() has put the reason in `detail`. Enable
            # is not the fix: GNOME Shell imports an extension's code once per
            # session and caches it, so an error it has already recorded
            # cannot clear until the session restarts -- which is also why a
            # fix to the extension needs a log out to take effect.
            self._add_status_row(
                "Widgets could not start",
                f"{state.detail}\n\nGNOME Shell loads an extension's code once "
                "per session, so log out and back in after this is fixed."
                if state.detail else
                "Log out and back in to start them.",
                None, None, None,
            )
        elif not state.running:
            self._add_status_row(
                "Widgets are installed but switched off",
                state.detail or "Enable them to show them on the desktop.",
                "Enable" if state.can_change else None,
                self._on_enable if state.can_change else None,
                "suggested-action" if state.can_change else None,
            )
        else:
            self._add_status_row(
                "Widgets are running",
                "Showing on the desktop.", "Reinstall", self._on_install, None,
            )

        if not state.daemon_active:
            self._add_status_row(
                "Data service is not running",
                "Weather, stocks, calendars, reminders, tasks, news and system readings use it.",
                "Start", self._on_start_daemon, "suggested-action",
            )
        else:
            self._add_status_row(
                "Data service is running",
                "Updating widget data in the background.",
                "Restart", self._on_restart_daemon, None,
            )

    def _status_rows(self):
        return getattr(self, "_status_children", [])

    def _add_status_row(self, title, subtitle, button_label, handler, css) -> None:
        row = Adw.ActionRow(title=markup(title), subtitle=markup(subtitle))
        if button_label:
            button = Gtk.Button(label=button_label, valign=Gtk.Align.CENTER)
            if css:
                button.add_css_class(css)
            button.connect("clicked", lambda _b: handler())
            row.add_suffix(button)
        self._status_group.add(row)
        self._status_children.append(row)

    def _on_install(self) -> None:
        was_running = ext.status().running
        try:
            ext.install()
        except (OSError, FileNotFoundError) as exc:
            self._toast(f"Could not install: {exc}")
            return

        ok, message = ext.enable()
        started, start_message = ext.start_daemon()
        if not started:
            self._toast(f"Extension installed, but the data service failed: {start_message}")
        elif was_running:
            self._toast("Reinstalled — log out and back in to load the update")
        elif ok:
            self._toast("Widgets installed and enabled")
        else:
            self._toast("Installed — log out and back in to see your widgets")
        self.refresh_status()

    def _on_enable(self) -> None:
        ok, message = ext.enable()
        if ok:
            self._toast(message)
        else:
            # A re-login is the answer only when the shell has not scanned the
            # extension yet. Saying it after every failure hid the shell's own
            # reason behind advice that did not apply.
            after = ext.status()
            self._toast(
                f"{message} — log out and back in" if after.needs_relogin else message
            )
        self.refresh_status()

    def _on_start_daemon(self) -> None:
        ok, message = ext.start_daemon()
        self._toast(message)
        self.refresh_status()

    def _on_restart_daemon(self) -> None:
        ext.restart_daemon()
        self._toast("Data service restarted")
        self.refresh_status()

    # -- widget list -------------------------------------------------------

    def _build_header_buttons(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(self._build_edit_layout_button())
        box.append(self._build_add_button())
        return box

    def _build_edit_layout_button(self) -> Gtk.Button:
        button = Gtk.Button(
            icon_name="view-grid-symbolic", valign=Gtk.Align.CENTER
        )
        button.add_css_class("flat")
        button.set_tooltip_text("Edit layout on the desktop")
        button.connect("clicked", self._on_edit_layout)
        return button

    def _on_edit_layout(self, _button) -> None:
        """Ask the extension to go into layout-editing mode.

        The flag is written, not held: the extension clears it in the file when
        the user finishes, so this copy is put straight back to False. Leaving
        it True would make the next unrelated save -- a colour, a stock symbol
        -- drop the desktop back into edit mode.
        """
        self._config.edit_layout = True
        self._save_now()
        self._config.edit_layout = False

        if not ext.status().running:
            self._toast("Widgets are not running — start them above first")
            return
        self._toast("Editing layout on the desktop — press Esc when done")

    def _build_add_button(self) -> Gtk.MenuButton:
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover(child=menu)
        button = Gtk.MenuButton(
            icon_name="list-add-symbolic", popover=popover, valign=Gtk.Align.CENTER
        )
        button.add_css_class("flat")
        button.set_tooltip_text("Add a widget")

        for type_name, spec in config.WIDGET_TYPES.items():
            item = Gtk.Button(label=spec["title"])
            item.add_css_class("flat")
            item.connect("clicked", self._on_add_widget, type_name, popover)
            menu.append(item)
        return button

    def _on_add_widget(self, _button, type_name: str, popover: Gtk.Popover) -> None:
        popover.popdown()
        # Offset each new widget so a second one is not created exactly on top
        # of the first, invisible until dragged.
        offset = 40 + 30 * len(self._config.widgets)
        self._config.widgets.append(config.Widget(type=type_name, x=offset, y=offset))
        self._save_now()
        self._rebuild_widget_list()

    def _rebuild_widget_list(self) -> None:
        for row in self._widget_rows:
            self._widgets_group.remove(row)
        self._widget_rows.clear()

        if not self._config.widgets:
            row = Adw.ActionRow(
                title="No widgets yet",
                subtitle="Use + to add a clock, weather, stocks, calendar, reminders, To-Do, news or system monitor.",
            )
            self._widgets_group.add(row)
            self._widget_rows.append(row)
            return

        for widget in self._config.widgets:
            row = self._build_widget_row(widget)
            self._widgets_group.add(row)
            self._widget_rows.append(row)

    def _build_widget_row(self, widget: config.Widget) -> Adw.ExpanderRow:
        spec = config.WIDGET_TYPES[widget.type]
        row = Adw.ExpanderRow(title=spec["title"], subtitle=f"at {widget.x}, {widget.y}")

        enabled = Gtk.Switch(active=widget.enabled, valign=Gtk.Align.CENTER)
        enabled.connect("notify::active", self._on_widget_enabled, widget)
        row.add_suffix(enabled)

        monitor = Adw.ComboRow(
            title="Monitor", model=Gtk.StringList.new(MONITOR_LABELS)
        )
        monitor.set_selected(min(widget.monitor, len(MONITOR_LABELS) - 1))
        monitor.connect("notify::selected", self._on_monitor_changed, widget)
        row.add_row(monitor)

        for option_row in self._provider_rows(widget):
            row.add_row(option_row)

        remove = Gtk.Button(label="Remove widget", halign=Gtk.Align.CENTER)
        remove.add_css_class("destructive-action")
        remove.set_margin_top(8)
        remove.set_margin_bottom(8)
        remove.connect("clicked", self._on_remove_widget, widget)
        holder = Gtk.ListBoxRow(activatable=False, selectable=False)
        holder.set_child(remove)
        row.add_row(holder)
        return row

    def _on_widget_enabled(self, switch, _pspec, widget: config.Widget) -> None:
        widget.enabled = switch.get_active()
        self._schedule_save()

    def _on_monitor_changed(self, combo, _pspec, widget: config.Widget) -> None:
        widget.monitor = combo.get_selected()
        self._schedule_save()

    def _on_remove_widget(self, _button, widget: config.Widget) -> None:
        self._config.widgets = [w for w in self._config.widgets if w.id != widget.id]
        self._save_now()
        self._rebuild_widget_list()

    # -- per-provider options ---------------------------------------------

    def _provider_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        builder = {
            "clock": self._clock_rows,
            "weather": self._weather_rows,
            "stocks": self._stocks_rows,
            "calendar": self._calendar_rows,
            "news": self._news_rows,
            "system": self._system_rows,
        }.get(widget.type)
        return builder(widget) if builder else []

    def _clock_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        twelve = Adw.SwitchRow(
            title="12-hour clock", active=widget.options.get("twelve_hour", True)
        )
        twelve.connect("notify::active", self._on_widget_option, widget, "twelve_hour")
        seconds = Adw.SwitchRow(
            title="Show seconds", active=widget.options.get("show_seconds", False)
        )
        seconds.connect("notify::active", self._on_widget_option, widget, "show_seconds")
        return [twelve, seconds]

    def _on_widget_option(self, row, _pspec, widget: config.Widget, key: str) -> None:
        widget.options[key] = row.get_active()
        self._schedule_save()

    def _weather_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        options = self._config.provider_options("weather")

        location = Adw.EntryRow(title="Location", text=options.get("place") or "")
        search = Gtk.Button(icon_name="system-search-symbolic", valign=Gtk.Align.CENTER)
        search.add_css_class("flat")
        search.set_tooltip_text("Look up this place")
        search.connect("clicked", lambda _b: self._search_place(location.get_text()))
        location.add_suffix(search)
        location.connect("entry-activated", lambda row: self._search_place(row.get_text()))

        coords = Adw.ActionRow(title="Coordinates")
        latitude, longitude = options.get("latitude"), options.get("longitude")
        coords.set_subtitle(
            f"{latitude:.4f}, {longitude:.4f}" if latitude is not None and longitude is not None
            else "Not set — search for a place above"
        )
        self._weather_coords_row = coords

        units = Adw.ComboRow(
            title="Units", model=Gtk.StringList.new(["Celsius", "Fahrenheit"])
        )
        units.set_selected(1 if options.get("units") == "fahrenheit" else 0)
        units.connect("notify::selected", self._on_units_changed)

        detail = Adw.ComboRow(
            title="Forecast detail",
            subtitle="Hours ahead, days ahead, or both",
            model=Gtk.StringList.new(["Hourly", "Daily", "Hourly and daily"]),
        )
        current_mode = options.get("forecast_mode", "hourly")
        detail.set_selected(
            FORECAST_MODES.index(current_mode) if current_mode in FORECAST_MODES else 0
        )
        detail.connect("notify::selected", self._on_forecast_mode_changed)

        refresh = Adw.SpinRow.new_with_range(1, 60, 1)
        refresh.set_title("Refresh interval")
        refresh.set_subtitle("Minutes between weather updates")
        refresh.set_value(max(1, min(60, int(options.get("interval", 300)) // 60)))
        refresh.connect("notify::value", self._on_weather_interval)

        return [location, coords, units, detail, refresh]

    def _search_place(self, place: str) -> None:
        if not place.strip():
            return

        def _worker() -> None:
            try:
                results = geocode(place)
                error = None
            except Exception as exc:  # noqa: BLE001
                results, error = [], str(exc)
            GLib.idle_add(self._show_place_results, results, error)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_place_results(self, results: list[dict], error: str | None) -> bool:
        if error:
            self._toast(f"Location lookup failed: {error}")
            return GLib.SOURCE_REMOVE
        if not results:
            self._toast("No matching place found")
            return GLib.SOURCE_REMOVE

        dialog = Adw.AlertDialog(heading="Choose a location")
        dialog.add_response("cancel", "Cancel")
        for index, result in enumerate(results):
            dialog.add_response(str(index), result["label"])
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_place_chosen, results)
        dialog.present(self.get_root())
        return GLib.SOURCE_REMOVE

    def _on_place_chosen(self, _dialog, response: str, results: list[dict]) -> None:
        if not response.isdigit():
            return
        chosen = results[int(response)]
        weather = self._config.providers.setdefault("weather", {})
        weather.update(
            {
                "place": chosen["label"],
                "latitude": chosen["latitude"],
                "longitude": chosen["longitude"],
            }
        )
        self._save_now()
        self._toast(f"Weather location set to {chosen['label']}")
        self._rebuild_widget_list()

    def _on_units_changed(self, combo, _pspec) -> None:
        weather = self._config.providers.setdefault("weather", {})
        weather["units"] = "fahrenheit" if combo.get_selected() == 1 else "celsius"
        self._schedule_save()

    def _on_forecast_mode_changed(self, combo, _pspec) -> None:
        index = combo.get_selected()
        weather = self._config.providers.setdefault("weather", {})
        weather["forecast_mode"] = FORECAST_MODES[
            index if 0 <= index < len(FORECAST_MODES) else 0
        ]
        self._schedule_save()

    def _on_weather_interval(self, row, _pspec) -> None:
        self._config.providers.setdefault("weather", {})["interval"] = (
            int(row.get_value()) * 60
        )
        self._schedule_save()

    def _stocks_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        options = self._config.provider_options("stocks")
        row = Adw.EntryRow(
            title="Symbols (comma separated)",
            text=", ".join(options.get("symbols") or []),
        )
        row.connect("changed", self._on_symbols_changed)
        return [row]

    def _on_symbols_changed(self, row) -> None:
        symbols = [s.strip().upper() for s in row.get_text().split(",") if s.strip()]
        self._config.providers.setdefault("stocks", {})["symbols"] = symbols
        self._schedule_save()

    def _calendar_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        options = self._config.provider_options("calendar")
        days = Adw.SpinRow.new_with_range(1, 90, 1)
        days.set_title("Days ahead")
        days.set_value(options.get("days_ahead", 14))
        days.connect("notify::value", self._on_calendar_days)
        return [days, self._calendar_sources_row()]

    def _calendar_sources_row(self) -> Adw.ActionRow:
        """Say which GNOME and Thunderbird calendars the widget can see."""
        state = config.read_json(config.state_path("calendar")) or {}
        data = state.get("data") or {}
        calendars = data.get("calendars")
        problems = data.get("problems") or []

        if problems:
            subtitle = problems[0]
        elif calendars:
            subtitle = ", ".join(calendars)
        elif calendars is not None:
            subtitle = "None. Add an account in Online Accounts or Thunderbird."
        else:
            subtitle = "Not checked yet — the data service reports this on its next run."

        row = Adw.ActionRow(title="Calendars found", subtitle=markup(subtitle))
        row.set_subtitle_lines(0)

        button = Gtk.Button(label="Online Accounts", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self._open_online_accounts())
        row.add_suffix(button)
        return row

    def _open_online_accounts(self) -> None:
        try:
            GLib.spawn_async(
                ["gnome-control-center", "online-accounts"],
                flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
            )
        except GLib.Error as exc:
            self._toast(f"Could not open Settings: {exc.message}")

    def _on_calendar_days(self, row, _pspec) -> None:
        self._config.providers.setdefault("calendar", {})["days_ahead"] = int(row.get_value())
        self._schedule_save()

    def _news_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        del widget
        options = self._config.provider_options("news")
        feeds = list(options.get("feeds") or [])

        sources = Adw.ActionRow(
            title="News sources",
            subtitle=f"{len(feeds)} feed{'s' if len(feeds) != 1 else ''} configured",
        )
        manage = Gtk.Button(label="Manage", valign=Gtk.Align.CENTER)
        manage.connect(
            "clicked",
            lambda _button: _NewsFeedsDialog(self, feeds).present(self.get_root()),
        )
        sources.add_suffix(manage)

        presets = list(options.get("topic_presets") or [])
        custom = list(options.get("topics") or [])
        topics = Adw.ActionRow(
            title="Topics",
            subtitle=markup(_topic_summary(presets, custom)),
        )
        topics.set_tooltip_text(
            "Topics switched off exclude matching headlines, even when another selected topic matches"
        )
        choose = Gtk.Button(label="Choose", valign=Gtk.Align.CENTER)
        choose.connect(
            "clicked",
            lambda _button: _NewsTopicsDialog(self, presets, custom).present(
                self.get_root()
            ),
        )
        topics.add_suffix(choose)

        refresh = Adw.SpinRow.new_with_range(5, 120, 5)
        refresh.set_title("Refresh interval")
        refresh.set_subtitle("Minutes between feed updates")
        refresh.set_value(max(5, min(120, int(options.get("interval", 900)) // 60)))
        refresh.connect("notify::value", self._on_news_interval)

        maximum = Adw.SpinRow.new_with_range(5, 100, 5)
        maximum.set_title("Maximum headlines")
        maximum.set_subtitle("Number of articles available in the scrollable feed")
        maximum.set_value(options.get("max_items", 40))
        maximum.connect("notify::value", self._on_news_maximum)
        return [sources, topics, refresh, maximum]

    def _on_news_interval(self, row, _pspec) -> None:
        self._config.providers.setdefault("news", {})["interval"] = (
            int(row.get_value()) * 60
        )
        self._schedule_save()

    def _on_news_maximum(self, row, _pspec) -> None:
        self._config.providers.setdefault("news", {})["max_items"] = int(
            row.get_value()
        )
        self._schedule_save()

    def _system_rows(self, widget: config.Widget) -> list[Gtk.Widget]:
        options = self._config.provider_options("system")
        row = Adw.EntryRow(title="Disk to monitor", text=options.get("disk_path", "/"))
        row.connect("changed", self._on_disk_path)
        return [row]

    def _on_disk_path(self, row) -> None:
        self._config.providers.setdefault("system", {})["disk_path"] = row.get_text().strip() or "/"
        self._schedule_save()

    # -- appearance --------------------------------------------------------

    def _build_appearance(self) -> None:
        style = dict(config.DEFAULT_STYLE)
        style.update(self._config.style)

        self._colour_controls: dict[str, tuple[Gtk.ColorDialogButton, int, Gtk.Button]] = {}

        theme = Adw.ComboRow(
            title="Glass appearance",
            subtitle="Follow GNOME or keep the widgets light or dark",
            model=Gtk.StringList.new(["Follow System", "Light", "Dark"]),
        )
        modes = ["system", "light", "dark"]
        current_mode = style.get("theme_mode", "system")
        theme.set_selected(modes.index(current_mode) if current_mode in modes else 0)
        theme.connect("notify::selected", self._on_theme_mode_changed)
        self._appearance_group.add(theme)

        colourful = self._config.style.get(
            "colorful_accents", "accent" not in self._config.style
        )
        accents = Adw.SwitchRow(
            title="Colorful accents",
            subtitle="Give every widget its own bright system color",
            active=colourful,
        )
        accents.connect("notify::active", self._on_colorful_accents_changed)
        self._appearance_group.add(accents)

        opacity = Adw.SpinRow.new_with_range(0.1, 1.0, 0.05)
        opacity.set_title("Background opacity")
        opacity.set_digits(2)
        opacity.set_value(style["opacity"])
        opacity.connect("notify::value", self._on_style_number, "opacity", float)
        self._appearance_group.add(opacity)

        radius = Adw.SpinRow.new_with_range(0, 40, 1)
        radius.set_title("Corner radius")
        radius.set_value(style["corner_radius"])
        radius.connect("notify::value", self._on_style_number, "corner_radius", int)
        self._appearance_group.add(radius)

        blur = Adw.SpinRow.new_with_range(0, 64, 2)
        blur.set_title("Glass blur")
        blur.set_subtitle("Set to zero to use translucent glass without blur")
        blur.set_value(style["blur_radius"])
        blur.connect("notify::value", self._on_style_number, "blur_radius", int)
        self._appearance_group.add(blur)

        scale = Adw.SpinRow.new_with_range(0.7, 2.0, 0.05)
        scale.set_title("Text size")
        scale.set_digits(2)
        scale.set_value(style["font_scale"])
        scale.connect("notify::value", self._on_style_number, "font_scale", float)
        self._appearance_group.add(scale)

        palette = self._palette_defaults(style.get("theme_mode", "system"))
        self._accent_row = self._colour_row(
            "Single accent colour", "accent", style.get("accent", palette["accent"]),
            "Used when Colorful accents is switched off",
        )
        self._accent_row.set_sensitive(not colourful)
        self._appearance_group.add(self._accent_row)
        self._appearance_group.add(self._colour_row(
            "Glass tint override", "background",
            style.get("background", palette["background"]),
            "Optional; reset to follow the adaptive glass palette",
        ))
        self._appearance_group.add(self._colour_row(
            "Text colour override", "text_color",
            style.get("text_color", palette["text_color"]),
            "Optional; reset to follow the adaptive glass palette",
        ))

    def _palette_defaults(self, mode: str | None = None) -> dict[str, str]:
        mode = mode or self._config.style.get("theme_mode", "system")
        dark = mode == "dark" or (
            mode == "system" and Adw.StyleManager.get_default().get_dark()
        )
        return {
            "accent": config.DEFAULT_STYLE["accent"],
            "background": "#18202c" if dark else "#f8fbff",
            "text_color": "#f7faff" if dark else "#172033",
        }

    def _colour_row(
        self, title: str, key: str, value: str, subtitle: str
    ) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        rgba = Gdk.RGBA()
        rgba.parse(value)
        button = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba, valign=Gtk.Align.CENTER
        )
        handler = button.connect("notify::rgba", self._on_colour_changed, key)
        reset = Gtk.Button(
            icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text=f"Reset {title.lower()}",
        )
        reset.add_css_class("flat")
        reset.set_sensitive(key in self._config.style)
        reset.connect("clicked", self._on_colour_reset, key)
        row.add_suffix(reset)
        row.add_suffix(button)
        self._colour_controls[key] = (button, handler, reset)
        return row

    def _on_colour_changed(self, button, _pspec, key: str) -> None:
        rgba = button.get_rgba()
        # The widgets take plain #rrggbb; alpha is a separate opacity setting so
        # that a single slider controls the whole card rather than each colour.
        self._config.style[key] = "#{:02x}{:02x}{:02x}".format(
            round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
        )
        self._colour_controls[key][2].set_sensitive(True)
        self._schedule_save()

    def _on_colour_reset(self, _button, key: str) -> None:
        self._config.style.pop(key, None)
        colour = self._palette_defaults()[key]
        rgba = Gdk.RGBA()
        rgba.parse(colour)
        picker, handler, reset = self._colour_controls[key]
        picker.handler_block(handler)
        picker.set_rgba(rgba)
        picker.handler_unblock(handler)
        reset.set_sensitive(False)
        self._schedule_save()

    def _on_theme_mode_changed(self, row, _pspec) -> None:
        mode = ["system", "light", "dark"][row.get_selected()]
        self._config.style["theme_mode"] = mode
        palette = self._palette_defaults(mode)
        for key in ("background", "text_color"):
            if key in self._config.style:
                continue
            picker, handler, _reset = self._colour_controls[key]
            rgba = Gdk.RGBA()
            rgba.parse(palette[key])
            picker.handler_block(handler)
            picker.set_rgba(rgba)
            picker.handler_unblock(handler)
        self._schedule_save()

    def _on_colorful_accents_changed(self, row, _pspec) -> None:
        colourful = row.get_active()
        self._config.style["colorful_accents"] = colourful
        self._accent_row.set_sensitive(not colourful)
        self._schedule_save()

    def _on_style_number(self, row, _pspec, key: str, cast) -> None:
        self._config.style[key] = cast(row.get_value())
        self._schedule_save()

    # -- persistence -------------------------------------------------------

    def _schedule_save(self) -> None:
        """Coalesce rapid edits into one write.

        Dragging a spin button emits a change per step, and the extension
        rebuilds every actor on each config write -- so writing on every one
        makes the desktop flicker.
        """
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._flush_save)

    def _flush_save(self) -> bool:
        self._save_source = 0
        self._save_now()
        return GLib.SOURCE_REMOVE

    def _save_now(self, candidate: config.Config | None = None) -> bool:
        outgoing = candidate if candidate is not None else self._config
        # Overall owns shell-chrome and desktop-icon settings. This page keeps
        # a long-lived Config instance for its many controls, so preserve newer
        # appearance values instead of overwriting them with its startup copy.
        appearance = config.load()
        outgoing.chrome = appearance.chrome
        outgoing.desktop_icons = appearance.desktop_icons
        try:
            config.save(outgoing)
        except OSError as exc:
            self._toast(f"Could not save settings: {exc}")
            return False
        if candidate is not None:
            self._config = candidate
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        return True

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=markup(message)))

    def reload(self) -> None:
        """Pick up positions the extension wrote while the app was open."""
        self._config = config.load()
        self._rebuild_widget_list()
        self.refresh_status()


def _normalize_topics(text: str) -> list[str]:
    """Split the custom-topic entry the same way the news provider does."""
    topics = []
    seen = set()
    for value in text.split(","):
        topic = " ".join(value.split())
        folded = topic.casefold()
        if not topic or folded in seen:
            continue
        seen.add(folded)
        topics.append(topic)
    return topics


def _topic_summary(presets: list[str], custom: list[str]) -> str:
    """One line naming the active filters, for the Topics row's subtitle."""
    selected = set(presets) & set(config.NEWS_TOPIC_IDS)
    if not selected:
        return "No topics selected"
    if selected == set(config.NEWS_TOPIC_IDS):
        return "All topics"
    labels = [
        preset["label"]
        for preset in config.NEWS_TOPIC_PRESETS
        if preset["id"] in selected
    ] + list(custom)
    if len(labels) <= 3:
        return ", ".join(labels)
    return f"{', '.join(labels[:3])} +{len(labels) - 3}"


class _NewsTopicsDialog(Adw.Dialog):
    """Pick which topics the News card shows, from presets or free text."""

    def __init__(self, page: WidgetsPage, presets: list[str], custom: list[str]):
        super().__init__(title="News Topics", content_width=660)
        self._page = page
        self._switches: dict[str, Adw.SwitchRow] = {}

        chosen = set(presets)
        group = Adw.PreferencesGroup(
            title="Filter headlines",
            description=(
                "Matches headline titles and feed categories. Topics switched off exclude stories, "
                "even when another selected topic matches. All on shows every headline; "
                "all off shows no news."
            ),
        )
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, active in (("Select All", True), ("Clear All", False)):
            button = Gtk.Button(label=label)
            button.add_css_class("flat")
            button.connect("clicked", lambda _button, value=active: self._select_all(value))
            controls.append(button)
        group.set_header_suffix(controls)
        for preset in config.NEWS_TOPIC_PRESETS:
            row = Adw.SwitchRow(
                title=markup(preset["label"]),
                subtitle=markup(", ".join(preset["keywords"][:4]) + "…"),
                active=preset["id"] in chosen,
            )
            group.add(row)
            self._switches[preset["id"]] = row

        extra = Adw.PreferencesGroup(
            title="Custom topics",
            description=("Additional keywords, separated by commas. Select at least one topic. "
                         "Keywords cannot override topics switched off."),
        )
        self._custom = Adw.EntryRow(
            title="Custom topics", text=", ".join(custom)
        )
        extra.add(self._custom)

        page_content = Adw.PreferencesPage()
        page_content.add(group)
        page_content.add(extra)

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _button: self._save())
        header.pack_end(save)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page_content)
        self.set_child(view)

    def _select_all(self, active: bool) -> None:
        for row in self._switches.values():
            row.set_active(active)
        if not active:
            self._custom.set_text("")

    def _save(self) -> None:
        candidate = copy.deepcopy(self._page._config)
        news = candidate.providers.setdefault("news", {})
        news["topic_presets"] = [
            preset_id
            for preset_id, row in self._switches.items()
            if row.get_active()
        ]
        news["topics"] = _normalize_topics(self._custom.get_text())
        if not self._page._save_now(candidate):
            return
        self._page._rebuild_widget_list()
        self._page._toast("News topics saved")
        self.close()


class _NewsFeedsDialog(Adw.Dialog):
    """Edit the global RSS/Atom source list used by every News widget."""

    def __init__(self, page: WidgetsPage, feeds: list[str]):
        super().__init__(title="News Sources", content_width=660)
        self._page = page
        self._rows: list[Adw.EntryRow] = []

        self._group = Adw.PreferencesGroup(
            title="RSS and Atom feeds",
            description="Add any public HTTP or HTTPS feed URL.",
        )
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        restore = Gtk.Button(label="Restore Defaults")
        restore.add_css_class("flat")
        restore.connect("clicked", lambda _button: self._restore_defaults())
        tools.append(restore)
        add = Gtk.Button(icon_name="list-add-symbolic")
        add.add_css_class("flat")
        add.set_tooltip_text("Add feed")
        add.connect("clicked", lambda _button: self._add_feed("", focus=True))
        tools.append(add)
        self._group.set_header_suffix(tools)

        for url in feeds:
            self._add_feed(str(url))

        page_content = Adw.PreferencesPage()
        page_content.add(self._group)

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _button: self._save())
        header.pack_end(save)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page_content)
        self.set_child(view)

    def _add_feed(self, url: str, *, focus: bool = False) -> None:
        row = Adw.EntryRow(title="Feed URL", text=url)
        remove = Gtk.Button(
            icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER
        )
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove feed")
        remove.connect("clicked", lambda _button: self._remove_feed(row))
        row.add_suffix(remove)
        self._group.add(row)
        self._rows.append(row)
        if focus:
            row.grab_focus()

    def _remove_feed(self, row: Adw.EntryRow) -> None:
        self._group.remove(row)
        self._rows.remove(row)

    def _restore_defaults(self) -> None:
        for row in list(self._rows):
            self._group.remove(row)
        self._rows.clear()
        for url in config.DEFAULT_NEWS_FEEDS:
            self._add_feed(url)

    def _save(self) -> None:
        feeds = []
        for row in self._rows:
            url = row.get_text().strip()
            if not url:
                continue
            try:
                parsed = urllib.parse.urlsplit(url)
            except ValueError:
                self._page._toast(f"Invalid feed URL: {url}")
                row.grab_focus()
                return
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                self._page._toast(f"Invalid feed URL: {url}")
                row.grab_focus()
                return
            if url not in feeds:
                feeds.append(url)

        candidate = copy.deepcopy(self._page._config)
        candidate.providers.setdefault("news", {})["feeds"] = feeds
        if not self._page._save_now(candidate):
            return
        self._page._rebuild_widget_list()
        self._page._toast("News sources saved")
        self.close()
