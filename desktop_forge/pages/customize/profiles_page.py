"""Profiles & Presets: pick a look, save your own, share them as files."""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ...backend import markup
from ...customize import profiles as profile_store
from . import preview


def _accent() -> str:
    try:
        rgba = Adw.StyleManager.get_default().get_accent_color_rgba()
        return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))
    except (AttributeError, TypeError):
        return "#3584e4"


def sketch(values_source, width: int, height: int) -> Gtk.DrawingArea:
    """A drawn mock-up; values_source() is read at each draw, so it follows changes."""
    area = Gtk.DrawingArea(content_width=width, content_height=height)
    area.add_css_class("profile-sketch")

    def draw(_area, cr, w, h):
        cr.save()
        preview._rounded(cr, 0, 0, w, h, 8)
        cr.clip()
        preview.draw(cr, w, h, values_source(), _accent(), Adw.StyleManager.get_default().get_dark())
        cr.restore()

    area.set_draw_func(draw)
    return area


class ProfilesSection:
    def __init__(self, hub):
        self.hub = hub
        self.store: profile_store.ProfileStore = hub.store
        self.page = Adw.PreferencesPage()
        self._sketches: list[Gtk.DrawingArea] = []

        self.current = Adw.PreferencesGroup(title="In Use")
        self.page.add(self.current)
        self.current_row = Adw.ActionRow(title="Your own settings")
        self.current_sketch = sketch(lambda: self.hub.controller.values, 96, 60)
        self.current_sketch.set_valign(Gtk.Align.CENTER)
        self.current_sketch.set_margin_top(6)
        self.current_sketch.set_margin_bottom(6)
        self.current_row.add_prefix(self.current_sketch)
        self.current.add(self.current_row)
        self.update_row = Adw.ButtonRow(title="Update Profile", start_icon_name="document-save-symbolic",
                                        tooltip_text="Save the current desktop into this profile")
        self.update_row.connect("activated", lambda _r: self._update_active())
        self.current.add(self.update_row)
        save_new = Adw.ButtonRow(title="Save as New Profile…", start_icon_name="list-add-symbolic")
        save_new.connect("activated", lambda _r: self._ask_name(
            "New Profile", "Save the current desktop as a profile you can switch back to.", "", "Save",
            self._create))
        self.current.add(save_new)

        self.presets = Adw.PreferencesGroup(
            title="Presets",
            description="Built-in looks. Choosing one shows it on your desktop straight away; keep it, "
                        "or it goes back after 20 seconds. Your input, clock and lock screen settings "
                        "are left alone.")
        self.page.add(self.presets)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                min_children_per_line=1, max_children_per_line=4, column_spacing=12,
                                row_spacing=12)
        self.presets.add(self.flow)
        self._cards = {}
        for profile in self.store.presets():
            self.flow.append(self._card(profile))

        self.mine = Adw.PreferencesGroup(
            title="Your Profiles",
            description="Saved from your own desktop. Export one to use it on another computer.")
        buttons = Gtk.Box(spacing=6)
        import_button = Gtk.Button(label="Import…")
        import_button.connect("clicked", lambda _b: self._import())
        buttons.append(import_button)
        self.mine.set_header_suffix(buttons)
        self.page.add(self.mine)
        self._rows = []
        self.reload()

    # -- building ------------------------------------------------------------

    def _target(self, profile):
        return self.store.target(profile, self.hub.controller.customizer.default)

    def _card(self, profile) -> Gtk.Widget:
        values = {}

        def source():
            if not values:
                values.update({**self.hub.controller.values, **self._target(profile)})
            return values

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=10,
                      margin_start=8, margin_end=8)
        drawing = sketch(source, 200, 125)
        box.append(drawing)
        title = Gtk.Box(spacing=6)
        title.append(Gtk.Image(icon_name=profile.icon))
        name = Gtk.Label(label=profile.name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("heading")
        title.append(name)
        badge = Gtk.Label(label="In use", visible=False)
        badge.add_css_class("caption")
        badge.add_css_class("accent")
        title.append(badge)
        box.append(title)
        description = Gtk.Label(label=profile.description, xalign=0, wrap=True, max_width_chars=28, lines=3,
                                ellipsize=Pango.EllipsizeMode.END)
        description.add_css_class("caption")
        description.add_css_class("dim-label")
        box.append(description)
        button = Gtk.Button(child=box, tooltip_text=f"Try {profile.name}")
        button.add_css_class("card")
        button.add_css_class("profile-card")
        button.connect("clicked", lambda _b: self.hub.apply_profile(profile))
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", lambda *_a: self._menu_for(profile, button).popup())
        button.add_controller(menu)
        self._cards[profile.id] = (button, badge, values, drawing)
        self._sketches.append(drawing)
        return button

    def _row(self, profile) -> Adw.ActionRow:
        row = Adw.ActionRow(title=markup(profile.name))
        drawing = sketch(lambda p=profile: {**self.hub.controller.values, **p.values}, 64, 40)
        drawing.set_valign(Gtk.Align.CENTER)
        drawing.set_margin_top(6)
        drawing.set_margin_bottom(6)
        row.add_prefix(drawing)
        apply = Gtk.Button(label="Switch", valign=Gtk.Align.CENTER)
        apply.connect("clicked", lambda _b: self.hub.apply_profile(profile))
        row.add_suffix(apply)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="More actions")
        more.add_css_class("flat")
        more.set_popover(self._menu_for(profile, more))
        row.add_suffix(more)
        row.profile = profile
        return row

    def _menu_for(self, profile, parent) -> Gtk.Popover:
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4,
                      margin_start=4, margin_end=4)
        items = [("Duplicate", lambda: self._duplicate(profile)), ("Export…", lambda: self._export(profile))]
        if not profile.builtin:
            items = [("Save Current Desktop Here", lambda: self._save_into(profile)),
                     ("Rename…", lambda: self._ask_name("Rename Profile", "", profile.name, "Rename",
                                                        lambda name: self._rename(profile, name))),
                     *items, ("Delete…", lambda: self._delete(profile))]
        for label, action in items:
            item = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            item.add_css_class("flat")
            item.get_child().set_xalign(0)
            if label.startswith("Delete"):
                item.add_css_class("destructive-action")

            def clicked(_b, action=action):
                popover.popdown()
                action()
            item.connect("clicked", clicked)
            box.append(item)
        popover.set_child(box)
        if not isinstance(parent, Gtk.MenuButton):
            popover.set_parent(parent)
        return popover

    # -- state -----------------------------------------------------------------

    def reload(self) -> None:
        for row in self._rows:
            self.mine.remove(row)
        self._rows = []
        users = self.store.user_profiles()
        if not users:
            empty = Adw.ActionRow(title="No profiles yet",
                                  subtitle="Use Save as New Profile to keep the desktop as it is now.")
            empty.add_css_class("dim-label")
            self._rows.append(empty)
            self.mine.add(empty)
        for profile in users:
            row = self._row(profile)
            self._rows.append(row)
            self.mine.add(row)
        self.refresh_state()

    def refresh_state(self) -> None:
        """The In Use row, badges and modified marks, from current values."""
        active_id = self.store.active()
        current = self.hub.controller.values
        active = None
        if active_id:
            try:
                active = self.store.get(active_id)
            except ValueError:
                active = None
        if active is None:
            self.current_row.set_title("Your own settings")
            self.current_row.set_subtitle("Not saved as a profile")
            self.update_row.set_visible(False)
        else:
            changed = self.store.modified(active, current, self.hub.controller.customizer.default)
            self.current_row.set_title(markup(active.name))
            kind = "Built-in preset" if active.builtin else "Saved profile"
            self.current_row.set_subtitle(markup(
                f"{kind} · Modified, {len(changed)} change{'s' if len(changed) != 1 else ''}" if changed else kind))
            self.update_row.set_visible(bool(changed) and not active.builtin)
        for profile_id, (_button, badge, _values, _drawing) in self._cards.items():
            badge.set_visible(profile_id == active_id)
        for row in self._rows:
            profile = getattr(row, "profile", None)
            if profile is not None:
                row.set_subtitle("In use" if profile.id == active_id else "")
        self.current_sketch.queue_draw()

    # -- actions ---------------------------------------------------------------

    def _current_values(self) -> dict:
        self.hub.controller.flush()
        return self.hub.controller.customizer.values(profile_store.PROFILE_IDS)

    def _create(self, name: str) -> None:
        try:
            profile = self.store.create(name, self._current_values())
        except (OSError, ValueError) as exc:
            self.hub.toast(f"Could not save the profile: {exc}")
            return
        self.store.set_active(profile.id)
        self.reload()
        self.hub.toast(f"Saved “{profile.name}”")

    def _update_active(self) -> None:
        active = self.store.active()
        if active:
            try:
                self._save_into(self.store.get(active))
            except ValueError as exc:
                self.hub.toast(str(exc))

    def _save_into(self, profile) -> None:
        try:
            self.store.save_values(profile.id, self._current_values())
        except (OSError, ValueError) as exc:
            self.hub.toast(f"Could not update the profile: {exc}")
            return
        self.store.set_active(profile.id)
        self.reload()
        self.hub.toast(f"Updated “{profile.name}”")

    def _rename(self, profile, name) -> None:
        try:
            self.store.rename(profile.id, name)
        except (OSError, ValueError) as exc:
            self.hub.toast(str(exc))
        self.reload()

    def _duplicate(self, profile) -> None:
        try:
            copy_ = self.store.duplicate(profile.id, defaults=self.hub.controller.customizer.default)
        except (OSError, ValueError) as exc:
            self.hub.toast(str(exc))
            return
        self.reload()
        self.hub.toast(f"Made “{copy_.name}” — you can change and rename it")

    def _delete(self, profile) -> None:
        dialog = Adw.AlertDialog(heading=f"Delete “{profile.name}”?",
                                 body="The profile is removed. Your desktop stays as it is now.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def answered(_dialog, response):
            if response == "delete":
                try:
                    self.store.delete(profile.id)
                except (OSError, ValueError) as exc:
                    self.hub.toast(str(exc))
                self.reload()
        dialog.connect("response", answered)
        dialog.present(self.page)

    def _ask_name(self, heading, body, initial, action, done) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        entry = Gtk.Entry(text=initial, activates_default=True, max_length=profile_store.MAX_NAME,
                          placeholder_text="Profile name")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", action)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("ok", bool(initial.strip()))
        entry.connect("changed", lambda e: dialog.set_response_enabled("ok", bool(e.get_text().strip())))
        dialog.connect("response", lambda _d, response: done(entry.get_text()) if response == "ok" else None)
        dialog.present(self.page)
        entry.grab_focus()

    @staticmethod
    def _filters() -> Gio.ListStore:
        profiles = Gtk.FileFilter(name="Desktop Forge profiles")
        profiles.add_pattern(f"*{profile_store.EXTENSION}")
        profiles.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(profiles)
        return store

    def _export(self, profile) -> None:
        safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in profile.name).strip() or "profile"
        dialog = Gtk.FileDialog(title="Export Profile", initial_name=f"{safe}{profile_store.EXTENSION}",
                                filters=self._filters())

        def chosen(dialog_, result):
            try:
                file = dialog_.save_finish(result)
            except GLib.Error:
                return
            path = file.get_path()
            if not path:
                self.hub.toast("Choose a folder on this computer")
                return
            try:
                self.store.export(profile.id, path, defaults=self.hub.controller.customizer.default)
            except (OSError, ValueError) as exc:
                self.hub.toast(f"Could not export: {exc}")
                return
            self.hub.toast(f"Exported to {os.path.basename(path)}")
        dialog.save(self.page.get_root(), None, chosen)

    def _import(self) -> None:
        dialog = Gtk.FileDialog(title="Import Profile", filters=self._filters())

        def chosen(dialog_, result):
            try:
                file = dialog_.open_finish(result)
            except GLib.Error:
                return
            path = file.get_path()
            if not path:
                self.hub.toast("Choose a file on this computer")
                return
            try:
                report = self.store.import_file(path)
            except (OSError, ValueError) as exc:
                self.hub.toast(str(exc))
                return
            self.reload()
            summary = report.summary()
            self.hub.toast(f"Imported “{report.profile.name}”" + (f": {summary}" if summary else ""))
        dialog.open(self.page.get_root(), None, chosen)
