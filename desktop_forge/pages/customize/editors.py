"""Hand-built editors for settings a generated row cannot show well:
window rules, the list of apps left out of window effects, pictures, and
the tiling layout as pictures."""
from __future__ import annotations

import copy
import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ...backend import markup
from ...backend.app_index import AppIndex
from ...customize import rules as rule_logic
from ...customize.registry import BY_ID
from .profiles_page import sketch

SHELL_BUS = "org.jrf.DesktopForge.Shell"
SHELL_PATH = "/org/jrf/DesktopForge/Shell"


def open_windows() -> list[dict]:
    """Open windows as the Shell extension reports them; empty when it is not running."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(SHELL_BUS, SHELL_PATH, "org.jrf.DesktopForge.Shell1", "Windows", None,
                              GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
        windows = json.loads(reply.unpack()[0])
    except (GLib.Error, ValueError, TypeError):
        return []
    return windows if isinstance(windows, list) else []


def running_features() -> dict | None:
    """What the running Shell extension can apply; None when it is not running.

    An extension from before customization answers with an unknown-method
    error, which reads as "no modules".
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(SHELL_BUS, SHELL_PATH, "org.jrf.DesktopForge.Shell1", "Features", None,
                              GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 500, None)
        features = json.loads(reply.unpack()[0])
        return features if isinstance(features, dict) else {}
    except GLib.Error as exc:
        if exc.matches(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD):
            return {}
        return None
    except (ValueError, TypeError):
        return {}


class AppPicker(Adw.Dialog):
    """Search the installed apps and pick one."""

    def __init__(self, title: str, chosen, exclude=()):
        super().__init__(title=title, content_width=420, content_height=520)
        self._chosen = chosen
        self._exclude = set(exclude)
        self._index = AppIndex()
        self.search = Gtk.SearchEntry(placeholder_text="Search apps", hexpand=True)
        self.search.connect("search-changed", lambda _e: self._fill())
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.connect("row-activated", self._activated)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(self.search)
        body.append(Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))
        view = Adw.ToolbarView(content=body)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self._index.build_async(self._fill)

    def _fill(self) -> None:
        self.list.remove_all()
        shown = 0
        for app in self._index.search(self.search.get_text().strip()):
            if app.desktop_id in self._exclude:
                continue
            row = Adw.ActionRow(title=markup(app.name), subtitle=markup(app.desktop_id), activatable=True)
            if app.icon is not None:
                row.add_prefix(Gtk.Image(gicon=app.icon, pixel_size=32))
            row.desktop_id = app.desktop_id
            row.app_name = app.name
            self.list.append(row)
            shown += 1
            if shown >= 200:
                break

    def _activated(self, _list, row) -> None:
        self._chosen(row.desktop_id, row.app_name)
        self.close()


class ExcludedApps:
    """The apps window effects never touch, as an expander of rows."""

    def __init__(self, controller, setting_id: str = "windows.exclude"):
        self.controller = controller
        self.setting_id = setting_id
        self.row = Adw.ExpanderRow(title="Apps left as they are",
                                   subtitle="Window effects never touch these, such as games or video players")
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add an app")
        add.add_css_class("flat")
        add.connect("clicked", self._add)
        self.row.add_suffix(add)
        self._children = []
        controller.bind(setting_id, lambda _value: self._fill())
        self._fill()

    def _ids(self) -> list[str]:
        return list(self.controller.get(self.setting_id, []) or [])

    def _fill(self) -> None:
        for child in self._children:
            self.row.remove(child)
        self._children = []
        ids = self._ids()
        self.row.set_subtitle(markup(f"{len(ids)} app{'s' if len(ids) != 1 else ''}" if ids else
                                     "Window effects never touch these, such as games or video players"))
        for desktop_id in ids:
            child = Adw.ActionRow(title=markup(desktop_id.removesuffix(".desktop")), subtitle=markup(desktop_id))
            remove = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
            remove.add_css_class("flat")
            remove.connect("clicked", lambda _b, d=desktop_id: self.controller.change(
                self.setting_id, [i for i in self._ids() if i != d]))
            child.add_suffix(remove)
            self.row.add_row(child)
            self._children.append(child)

    def _add(self, button) -> None:
        AppPicker("Leave an App as It Is", lambda desktop_id, _name: self.controller.change(
            self.setting_id, self._ids() + [desktop_id]), exclude=self._ids()).present(button)


class PictureRow:
    """A picture (or folder) setting stored as a file URI or path."""

    def __init__(self, controller, setting_id: str, title: str, *, folder=False, uri=True, subtitle_empty=""):
        self.controller = controller
        self.setting_id = setting_id
        self.folder = folder
        self.uri = uri
        self.empty = subtitle_empty or ("No folder chosen" if folder else "None")
        self.row = Adw.ActionRow(title=markup(title))
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._choose)
        self.row.add_suffix(choose)
        problem = controller.customizer.problem(controller.customizer.setting(setting_id))
        if problem:
            self.row.set_sensitive(False)
            self.row.set_subtitle(markup(problem))
        else:
            controller.bind(setting_id, self._show)
            self._show(controller.get(setting_id, ""))

    def _show(self, value) -> None:
        if not value:
            self.row.set_subtitle(markup(self.empty))
            return
        path = Gio.File.new_for_uri(value).get_path() if self.uri and "://" in value else value
        self.row.set_subtitle(markup(os.path.basename(path or value) or value))
        self.row.set_tooltip_text(path or value)

    def _choose(self, button) -> None:
        dialog = Gtk.FileDialog(title=self.row.get_title())
        if not self.folder:
            images = Gtk.FileFilter(name="Pictures")
            images.add_mime_type("image/*")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(images)
            dialog.set_filters(filters)

        def chosen(dialog_, result):
            try:
                file = (dialog_.select_folder_finish if self.folder else dialog_.open_finish)(result)
            except GLib.Error:
                return
            value = file.get_uri() if self.uri else file.get_path()
            if value:
                self.controller.change(self.setting_id, value)
        (dialog.select_folder if self.folder else dialog.open)(button.get_root(), None, chosen)


class RuleDialog(Adw.Dialog):
    """Create or edit one window rule."""

    def __init__(self, rule: dict | None, saved):
        super().__init__(title="Edit Rule" if rule else "New Rule", content_width=520, content_height=680)
        self._saved = saved
        rule = rule_logic.validate_rule(rule) if rule else {
            "name": "", "enabled": True, "match": dict(rule_logic.MATCH_DEFAULTS),
            "actions": dict(rule_logic.ACTION_DEFAULTS)}
        self.rule = copy.deepcopy(rule)
        match, actions = self.rule["match"], self.rule["actions"]

        page = Adw.PreferencesPage()
        naming = Adw.PreferencesGroup()
        self.name = Adw.EntryRow(title="Name (optional)", text=rule["name"])
        naming.add(self.name)
        page.add(naming)

        which = Adw.PreferencesGroup(title="Windows It Applies To",
                                     description="Every filled-in condition must match.")
        self.app = Adw.ActionRow(title="App", subtitle=markup(match["app"] or "Any app"))
        pick = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        pick.connect("clicked", self._pick_app)
        clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Any app")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda _b: self._set_app("", ""))
        self.app.add_suffix(clear)
        self.app.add_suffix(pick)
        which.add(self.app)
        self.wm_class = Adw.EntryRow(title="Window class", text=match["wm_class"])
        which.add(self.wm_class)
        self.title_text = Adw.EntryRow(title="Title contains", text=match["title"])
        which.add(self.title_text)
        self.regex = Adw.SwitchRow(title="Title is a pattern", subtitle="A regular expression",
                                   active=match["title_regex"])
        which.add(self.regex)
        self.kind = Adw.ComboRow(title="Kind of window",
                                 model=Gtk.StringList.new(["Any", "Normal windows", "Dialogs"]))
        self.kind.set_selected(rule_logic.TYPES.index(match["type"]))
        which.add(self.kind)
        self.matches = Adw.ActionRow(title="Open windows it matches", subtitle="")
        which.add(self.matches)
        page.add(which)

        what = Adw.PreferencesGroup(title="What Happens")
        self.mode = Adw.ComboRow(title="Tiling", model=Gtk.StringList.new(
            ["Leave as it is", "Always floating", "Always tiled"]))
        self.mode.set_selected(rule_logic.MODES.index(actions["mode"]))
        what.add(self.mode)
        self.workspace = self._spin("Workspace", "0 leaves it where it opens", 0, 36, actions["workspace"])
        what.add(self.workspace)
        self.monitor = self._spin("Display", "0 leaves it; 1 is the main display", 0, 8, actions["monitor"] + 1)
        what.add(self.monitor)
        self.width = self._spin("Width", "0 leaves the size alone", 0, 8192, actions["width"], step=10)
        self.height = self._spin("Height", "", 0, 8192, actions["height"], step=10)
        what.add(self.width)
        what.add(self.height)
        self.opacity = Adw.SpinRow.new_with_range(0.2, 1.0, 0.05)
        self.opacity.set_title("Opacity")
        self.opacity.set_digits(2)
        self.opacity.set_value(actions["opacity"])
        what.add(self.opacity)
        self.flags = {}
        for key, label in (("center", "Center it"), ("maximize", "Maximize it"), ("fullscreen", "Make it fullscreen"),
                           ("above", "Always on top"), ("sticky", "On every workspace"),
                           ("no_effects", "No window effects")):
            row = Adw.SwitchRow(title=label, active=actions[key])
            self.flags[key] = row
            what.add(row)
        page.add(what)

        for entry in (self.wm_class, self.title_text):
            entry.connect("changed", lambda *_a: self._validate())
        self.regex.connect("notify::active", lambda *_a: self._validate())
        self.kind.connect("notify::selected", lambda *_a: self._validate())

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        self.save = Gtk.Button(label="Save")
        self.save.add_css_class("suggested-action")
        self.save.connect("clicked", self._save)
        header.pack_end(self.save)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_child(view)
        self._windows = open_windows()
        self._validate()

    @staticmethod
    def _spin(title, subtitle, low, high, value, step=1):
        row = Adw.SpinRow.new_with_range(low, high, step)
        row.set_title(title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_value(value)
        return row

    def _pick_app(self, button) -> None:
        AppPicker("Choose an App", self._set_app).present(button)

    def _set_app(self, desktop_id: str, name: str) -> None:
        self.rule["match"]["app"] = desktop_id
        self.app.set_subtitle(markup(f"{name} · {desktop_id}" if desktop_id else "Any app"))
        self._validate()

    def _collect(self) -> dict:
        return {"name": self.name.get_text(), "enabled": self.rule.get("enabled", True),
                "match": {"app": self.rule["match"]["app"], "wm_class": self.wm_class.get_text(),
                          "title": self.title_text.get_text(), "title_regex": self.regex.get_active(),
                          "type": rule_logic.TYPES[self.kind.get_selected()]},
                "actions": {"mode": rule_logic.MODES[self.mode.get_selected()],
                            "workspace": int(self.workspace.get_value()),
                            "monitor": int(self.monitor.get_value()) - 1,
                            "width": int(self.width.get_value()), "height": int(self.height.get_value()),
                            "opacity": round(self.opacity.get_value(), 2),
                            **{key: row.get_active() for key, row in self.flags.items()}}}

    def _validate(self) -> None:
        try:
            rule = rule_logic.validate_rule(self._collect())
        except ValueError as exc:
            self.save.set_sensitive(False)
            self.matches.set_subtitle(markup(str(exc)))
            return
        self.save.set_sensitive(True)
        if not self._windows:
            self.matches.set_subtitle("Open windows can be counted while Desktop Forge's Shell extension runs")
            return
        found = [w for w in self._windows if rule_logic.matches(rule, w)]
        names = ", ".join(dict.fromkeys(str(w.get("name") or w.get("desktop_id")) for w in found))
        self.matches.set_subtitle(markup(f"{len(found)} now: {names}" if found else "None open right now"))

    def _save(self, _button) -> None:
        try:
            rule = rule_logic.validate_rule(self._collect())
        except ValueError as exc:
            self.matches.set_subtitle(markup(str(exc)))
            return
        self._saved(rule)
        self.close()


class RulesEditor:
    """The ordered list of window rules. The first matching rule for each
    action wins, so order matters and can be changed."""

    def __init__(self, controller, page: Adw.PreferencesPage, setting_id: str = "rules.list"):
        self.controller = controller
        self.setting_id = setting_id
        self.group = Adw.PreferencesGroup(
            title="Window Rules",
            description="Open particular windows floating, on a set workspace or display, at a set size, "
                        "always on top, or without effects. Rules apply when a window opens and when you "
                        "change them. Earlier rules win.")
        add = Gtk.Button(label="Add Rule", valign=Gtk.Align.CENTER)
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda b: RuleDialog(None, self._added).present(b))
        self.group.set_header_suffix(add)
        page.add(self.group)
        self._rows = []
        controller.bind(setting_id, lambda _v: self.reload())
        self.reload()

    def _rules(self) -> list[dict]:
        try:
            return rule_logic.validate_rules(copy.deepcopy(self.controller.get(self.setting_id, []) or []))
        except ValueError:
            return []

    def _store(self, rules: list[dict]) -> None:
        self.controller.change(self.setting_id, rules)
        self.reload()

    def _added(self, rule) -> None:
        self._store(self._rules() + [rule])

    def reload(self) -> None:
        for row in self._rows:
            self.group.remove(row)
        self._rows = []
        rules = self._rules()
        if not rules:
            empty = Adw.ActionRow(title="No rules", subtitle="Use Add Rule to make one")
            empty.add_css_class("dim-label")
            self._rows.append(empty)
            self.group.add(empty)
            return
        for index, rule in enumerate(rules):
            summary = rule_logic.describe(rule)
            row = Adw.ActionRow(title=markup(rule["name"] or summary.split(" · ")[0]),
                                subtitle=markup(summary.split(" · ", 1)[-1]))
            row.set_subtitle_lines(2)
            enabled = Gtk.Switch(active=rule["enabled"], valign=Gtk.Align.CENTER, tooltip_text="Use this rule")
            enabled.connect("notify::active", lambda s, _p, i=index: self._toggle(i, s.get_active()))
            row.add_prefix(enabled)
            for icon, tip, handler, sensitive in (
                    ("go-up-symbolic", "Move up", lambda _b, i=index: self._move(i, -1), index > 0),
                    ("go-down-symbolic", "Move down", lambda _b, i=index: self._move(i, 1), index < len(rules) - 1),
                    ("document-edit-symbolic", "Edit", lambda b, i=index: self._edit(b, i), True),
                    ("user-trash-symbolic", "Delete", lambda _b, i=index: self._remove(i), True)):
                button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tip, sensitive=sensitive)
                button.add_css_class("flat")
                button.connect("clicked", handler)
                row.add_suffix(button)
            self._rows.append(row)
            self.group.add(row)

    def _toggle(self, index, active) -> None:
        rules = self._rules()
        if index < len(rules) and rules[index]["enabled"] != active:
            rules[index]["enabled"] = active
            self._store(rules)

    def _move(self, index, step) -> None:
        rules = self._rules()
        other = index + step
        if 0 <= other < len(rules):
            rules[index], rules[other] = rules[other], rules[index]
            self._store(rules)

    def _edit(self, button, index) -> None:
        rules = self._rules()
        if index >= len(rules):
            return

        def saved(rule):
            current = self._rules()
            if index < len(current):
                rule["enabled"] = current[index]["enabled"]
                current[index] = rule
                self._store(current)
        RuleDialog(rules[index], saved).present(button)

    def _remove(self, index) -> None:
        rules = self._rules()
        if index < len(rules):
            del rules[index]
            self._store(rules)


class LayoutPicker:
    """Each tiling layout drawn with the current gaps, borders and ratio;
    choosing one applies it. The generated Layout row stays too, for
    search, reset and keyboard users."""

    REDRAW = ("tiling.gaps_inner", "tiling.gaps_outer", "tiling.master_ratio", "windows.corner_radius",
              "windows.border_width", "windows.border_accent", "windows.border_color",
              "windows.border_gradient", "windows.border_color2", "windows.shadow")

    def __init__(self, controller):
        self.controller = controller
        self.group = Adw.PreferencesGroup(
            title="Layout", description="How windows share each workspace. The switcher in the top bar "
                                        "changes it for one workspace at a time.")
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                min_children_per_line=2, max_children_per_line=4, column_spacing=12,
                                row_spacing=12, margin_top=6)
        self.children: dict[str, Gtk.FlowBoxChild] = {}
        self._sketches = []
        for value, label in BY_ID["tiling.layout"].choices:
            area = sketch(lambda value=value: {**controller.values, "tiling.enabled": True,
                                               "tiling.layout": value}, 132, 84)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4, margin_bottom=4)
            box.append(area)
            box.append(Gtk.Label(label=label, wrap=True, justify=Gtk.Justification.CENTER))
            child = Gtk.FlowBoxChild(child=box, tooltip_text=label)
            child.layout = value
            self.flow.append(child)
            self.children[value] = child
            self._sketches.append(area)
        self.flow.connect("child-activated", self._activated)
        self.group.add(self.flow)
        controller.bind("tiling.layout", self._select)
        for setting_id in self.REDRAW:
            controller.depend(setting_id, self._redraw)
        controller.depend("tiling.enabled", self._enabled)
        self._select(controller.get("tiling.layout", "master"))
        self._enabled()

    def _activated(self, _flow, child) -> None:
        self.controller.change("tiling.layout", child.layout)

    def _select(self, value) -> None:
        child = self.children.get(value)
        if child is not None:
            self.flow.select_child(child)

    def _redraw(self) -> None:
        for area in self._sketches:
            area.queue_draw()

    def _enabled(self) -> None:
        self.flow.set_sensitive(self.controller.get("tiling.enabled") is True)
