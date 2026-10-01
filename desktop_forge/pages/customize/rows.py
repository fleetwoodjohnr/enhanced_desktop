"""Rows generated from the settings registry, and the controller that binds them.

One SettingsController owns the values: every row that shows a setting
registers with it, a change made in any row (or by a profile) reaches all
of them, and writes are coalesced so dragging a spin button does not
rewrite desktop.json for every step.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...backend import markup
from ...customize import BY_ID, Customizer, PreviewSession, Setting

WRITE_DELAY_MS = 250


def _hex(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(round(max(0, min(1, c)) * 255) for c in
                                          (rgba.red, rgba.green, rgba.blue)))


class SettingsController:
    def __init__(self, customizer: Customizer | None = None, on_applied=None, on_problem=None):
        self.customizer = customizer or Customizer()
        self.session = PreviewSession(self.customizer)
        self.values = self.customizer.values()
        self._bindings: dict[str, list] = {}
        self._dependents: dict[str, list] = {}
        self._pending: dict = {}
        self._source = 0
        self._syncing = False
        self._on_applied = on_applied or (lambda ids: None)
        self._on_problem = on_problem or (lambda message: None)
        self._watched = {}
        self._scope: list | None = None
        # The section rows for each setting (not search results), so a page
        # can adjust one -- say, explain why it is switched off.
        self.rows: dict[str, Gtk.Widget] = {}

    # -- values ---------------------------------------------------------------

    def get(self, setting_id: str, fallback=None):
        return self.values.get(setting_id, fallback)

    def change(self, setting_id: str, value) -> None:
        """A row changed: remember it, update the other rows, write soon."""
        if self._syncing or self.values.get(setting_id) == value:
            return
        self.values[setting_id] = value
        self._pending[setting_id] = value
        self._sync_rows([setting_id])
        if self._source:
            GLib.source_remove(self._source)
        self._source = GLib.timeout_add(WRITE_DELAY_MS, self.flush)

    def flush(self) -> bool:
        self._source = 0
        pending, self._pending = self._pending, {}
        if pending:
            self._apply(pending, self.session.apply)
        return GLib.SOURCE_REMOVE

    def apply_now(self, values: dict, session: PreviewSession | None = None) -> dict:
        """Apply several values at once (a profile); returns the problems."""
        self.flush()
        return self._apply(values, (session or self.session).apply)

    def _apply(self, values: dict, apply) -> dict:
        _applied, problems = apply(values)
        self.reload(list(values))
        if problems:
            first = next(iter(problems.values()))
            more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
            self._on_problem(f"{first}{more}")
        self._on_applied(list(values))
        return problems

    def reload(self, ids=None) -> None:
        """Re-read values from their backends and show them."""
        fresh = self.customizer.values(ids)
        if ids is None:
            self.values = fresh
        else:
            self.values.update(fresh)
        self._sync_rows(ids if ids is not None else list(self._bindings))

    def _sync_rows(self, ids) -> None:
        self._syncing = True
        try:
            for setting_id in ids:
                for setter in self._bindings.get(setting_id, []):
                    if setting_id in self.values:
                        setter(self.values[setting_id])
                for update in self._dependents.get(setting_id, []):
                    update()
        finally:
            self._syncing = False

    # -- watching GNOME settings changed elsewhere -------------------------------

    def watch_gsettings(self) -> None:
        backend = self.customizer.gsettings
        for setting in BY_ID.values():
            if setting.backend != "gsettings" or setting.schema in self._watched:
                continue
            if not backend.available(setting):
                continue
            settings = backend.settings_for(setting.schema)
            if settings is None:
                continue
            self._watched[setting.schema] = (settings, settings.connect("changed", self._gsettings_changed))

    def _gsettings_changed(self, settings, key) -> None:
        schema = settings.props.schema_id
        ids = [s.id for s in BY_ID.values() if s.backend == "gsettings" and s.schema == schema and s.key == key]
        if ids and not self._pending:
            self.reload(ids)

    def unwatch(self) -> None:
        for settings, handler in self._watched.values():
            settings.disconnect(handler)
        self._watched = {}

    # -- rows ----------------------------------------------------------------

    def bind(self, setting_id: str, setter) -> None:
        self._bindings.setdefault(setting_id, []).append(setter)
        if self._scope is not None:
            self._scope.append((self._bindings, setting_id, setter))

    def depend(self, setting_id: str, update) -> None:
        self._dependents.setdefault(setting_id, []).append(update)
        if self._scope is not None:
            self._scope.append((self._dependents, setting_id, update))

    def collect(self) -> list:
        """Start recording bindings, for rows that will be thrown away
        (search results); pass the list to release() when they are."""
        self._scope = []
        return self._scope

    def stop_collecting(self) -> None:
        self._scope = None

    def release(self, scope: list) -> None:
        for table, setting_id, callback in scope:
            callbacks = table.get(setting_id, [])
            if callback in callbacks:
                callbacks.remove(callback)
        scope.clear()

    def row(self, setting: Setting) -> Gtk.Widget:
        builder = {"bool": self._switch, "int": self._spin, "float": self._spin, "enum": self._combo,
                   "color": self._color}.get(setting.kind)
        if builder is None:
            raise ValueError(f"{setting.id} has no generated row")
        row = builder(setting)
        if self._scope is None:
            self.rows.setdefault(setting.id, row)
        problem = self.customizer.problem(setting)
        if setting.experimental:
            tag = Gtk.Label(label="Experimental", valign=Gtk.Align.CENTER)
            tag.add_css_class("caption")
            tag.add_css_class("dim-label")
            row.add_suffix(tag)
        if problem:
            row.set_sensitive(False)
            row.set_subtitle(markup(problem))
            row.set_tooltip_text(problem)
            return row
        if setting.requires:
            def update(row=row, requires=setting.requires):
                row.set_sensitive(bool(self.values.get(requires)))
            update()
            self.depend(setting.requires, update)
        return row

    @staticmethod
    def _subtitle(setting: Setting) -> str:
        return markup(setting.description) if setting.description else ""

    def _switch(self, setting):
        row = Adw.SwitchRow(title=markup(setting.label), subtitle=self._subtitle(setting),
                            active=bool(self.values.get(setting.id, setting.default)))
        row.connect("notify::active", lambda r, _p: self.change(setting.id, r.get_active()))
        self.bind(setting.id, lambda value: row.set_active(bool(value)))
        return row

    def _spin(self, setting):
        row = Adw.SpinRow.new_with_range(setting.minimum, setting.maximum, setting.step)
        row.set_title(markup(setting.label))
        row.set_subtitle(self._subtitle(setting))
        row.set_digits(setting.digits)
        value = self.values.get(setting.id, setting.default)
        row.set_value(value)
        if setting.unit:
            unit = Gtk.Label(label=setting.unit, valign=Gtk.Align.CENTER)
            unit.add_css_class("dim-label")
            row.add_suffix(unit)
        cast = int if setting.kind == "int" else (lambda v: round(v, max(setting.digits, 2)))
        row.connect("notify::value", lambda r, _p: self.change(setting.id, cast(r.get_value())))
        self.bind(setting.id, lambda value: row.set_value(value))
        return row

    def _combo(self, setting):
        values = [choice[0] for choice in setting.choices]
        row = Adw.ComboRow(title=markup(setting.label), subtitle=self._subtitle(setting),
                           model=Gtk.StringList.new([choice[1] for choice in setting.choices]))
        current = self.values.get(setting.id, setting.default)
        row.set_selected(values.index(current) if current in values else 0)
        row.connect("notify::selected", lambda r, _p: self.change(setting.id, values[r.get_selected()])
                    if r.get_selected() < len(values) else None)
        self.bind(setting.id, lambda value: row.set_selected(values.index(value)) if value in values else None)
        return row

    def _color(self, setting):
        row = Adw.ActionRow(title=markup(setting.label), subtitle=self._subtitle(setting))
        rgba = Gdk.RGBA()
        rgba.parse(self.values.get(setting.id) or setting.default or "#3584e4")
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba,
                                       valign=Gtk.Align.CENTER)
        button.connect("notify::rgba", lambda b, _p: self.change(setting.id, _hex(b.get_rgba())))

        def show(value):
            colour = Gdk.RGBA()
            if colour.parse(value or setting.default or "#3584e4"):
                button.set_rgba(colour)
        self.bind(setting.id, show)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        return row


def groups_for(controller: SettingsController, settings, *, page: Adw.PreferencesPage,
               descriptions: dict | None = None, leading: dict | None = None) -> dict:
    """Generated rows for the given settings, one group per registry group.

    `leading` maps a group name to hand-built rows placed before its
    generated ones. Returns the groups by name, in page order.
    """
    made: dict[str, Adw.PreferencesGroup] = {}

    def group_for(name):
        group = made.get(name)
        if group is None:
            group = Adw.PreferencesGroup(title=markup(name))
            if descriptions and name in descriptions:
                group.set_description(markup(descriptions[name]))
            made[name] = group
            page.add(group)
            for row in (leading or {}).get(name, []):
                group.add(row)
        return group

    for setting in settings:
        if setting.generated_ui:
            group_for(setting.group).add(controller.row(setting))
        elif leading and setting.group in leading:
            group_for(setting.group)
    return made
