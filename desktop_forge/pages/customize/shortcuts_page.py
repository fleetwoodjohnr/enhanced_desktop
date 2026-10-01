"""Keyboard Shortcuts: capture a key combination, warn about clashes."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from ...backend import markup
from ...customize import shortcuts as logic

MODIFIER_KEYS = {getattr(Gdk, f"KEY_{name}") for name in (
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R", "Meta_L",
    "Meta_R", "Hyper_L", "Hyper_R", "ISO_Level3_Shift", "Caps_Lock") if hasattr(Gdk, f"KEY_{name}")}


class CaptureDialog(Adw.Dialog):
    """Press the combination; Escape cancels and Backspace clears."""

    def __init__(self, title: str, done):
        super().__init__(title=title, content_width=420)
        self._done = done
        self._inhibited = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24)
        box.append(Gtk.Image(icon_name="preferences-desktop-keyboard-shortcuts-symbolic", pixel_size=64))
        heading = Gtk.Label(label="Press the new shortcut")
        heading.add_css_class("title-3")
        box.append(heading)
        self.message = Gtk.Label(label="Escape cancels · Backspace removes the shortcut", wrap=True)
        self.message.add_css_class("dim-label")
        box.append(self.message)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._pressed)
        self.add_controller(keys)
        self.connect("map", self._mapped)
        self.connect("closed", self._closed)

    def _mapped(self, *_args) -> None:
        # Let Super combinations reach this dialog instead of GNOME Shell.
        surface = self.get_native().get_surface() if self.get_native() else None
        if surface is not None and hasattr(surface, "inhibit_system_shortcuts"):
            surface.inhibit_system_shortcuts(None)
            self._inhibited = surface

    def _closed(self, *_args) -> None:
        if self._inhibited is not None:
            self._inhibited.restore_system_shortcuts()
            self._inhibited = None

    def _pressed(self, _controller, keyval, keycode, state) -> bool:
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if keyval in MODIFIER_KEYS:
            return True
        if not modifiers and keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if not modifiers and keyval == Gdk.KEY_BackSpace:
            self.close()
            self._done("")
            return True
        accelerator = Gtk.accelerator_name_with_keycode(None, keyval, keycode, modifiers)
        problem = logic.acceptable(accelerator)
        if problem:
            self.message.set_label(problem)
            return True
        self.close()
        self._done(accelerator)
        return True


def shortcut_label(accelerator: str) -> Gtk.ShortcutLabel:
    label = Gtk.ShortcutLabel(accelerator=accelerator or "", disabled_text="Not set", valign=Gtk.Align.CENTER)
    return label


class ShortcutsSection:
    def __init__(self, hub):
        self.hub = hub
        self.store = logic.ShortcutStore()
        self.page = Adw.PreferencesPage()
        self._rows = {}
        if not self.store.extension_available:
            notice = Adw.PreferencesGroup()
            row = Adw.ActionRow(title="Desktop Forge's shortcuts are not installed yet",
                                subtitle="Run the installer, then log out and back in, to set tiling and "
                                         "snapping shortcuts. GNOME's shortcuts below work now.")
            row.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
            notice.add(row)
            self.page.add(notice)
        for title, about, items in logic.GROUPS:
            group = Adw.PreferencesGroup(title=markup(title))
            if about:
                group.set_description(markup(about))
            shown = 0
            for shortcut in items:
                if not self.store.available(shortcut):
                    continue
                group.add(self._row(shortcut))
                shown += 1
            if shown:
                self.page.add(group)
        self.custom = Adw.PreferencesGroup(
            title="Custom Commands", description="Run any command with a shortcut, such as opening a terminal.")
        add = Gtk.Button(label="Add Command…", valign=Gtk.Align.CENTER)
        add.connect("clicked", lambda _b: self._edit_custom(None))
        self.custom.set_header_suffix(add)
        self.page.add(self.custom)
        self._custom_rows = []
        self._fill_custom()

    # -- fixed shortcuts ----------------------------------------------------------

    def _row(self, shortcut) -> Adw.ActionRow:
        row = Adw.ActionRow(title=markup(shortcut.label))
        label = shortcut_label("")
        row.add_suffix(label)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Reset to default")
        reset.add_css_class("flat")
        reset.connect("clicked", lambda _b: self._apply(shortcut, None))
        row.add_suffix(reset)
        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Change")
        edit.add_css_class("flat")
        edit.connect("clicked", lambda _b: CaptureDialog(shortcut.label,
                                                         lambda accel: self._chosen(shortcut, accel)).present(self.page))
        row.add_suffix(edit)
        row.set_activatable_widget(edit)
        self._rows[shortcut] = (row, label, reset)
        self._show(shortcut)
        return row

    def _show(self, shortcut) -> None:
        _row, label, reset = self._rows[shortcut]
        accelerators = self.store.get(shortcut)
        label.set_accelerator(" ".join(accelerators[:1]))
        reset.set_visible(not self.store.is_default(shortcut))

    def _chosen(self, shortcut, accelerator: str) -> None:
        if not accelerator:
            self._apply(shortcut, [])
            return
        clashes = self.store.conflicts(accelerator, exclude=(shortcut.schema, shortcut.key))
        if not clashes:
            self._apply(shortcut, [accelerator])
            return
        names = ", ".join(f"“{c['label']}”" for c in clashes)
        dialog = Adw.AlertDialog(heading="Replace the existing shortcut?",
                                 body=f"{Gtk.accelerator_get_label(*Gtk.accelerator_parse(accelerator)[1:])} "
                                      f"is already used by {names}. Use it for “{shortcut.label}” instead?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("replace", "Replace")
        dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def answered(_d, response):
            if response != "replace":
                return
            for clash in clashes:
                self.store.release(clash, accelerator)
            self._apply(shortcut, [accelerator])
            self._refresh_all()
        dialog.connect("response", answered)
        dialog.present(self.page)

    def _apply(self, shortcut, accelerators) -> None:
        try:
            if accelerators is None:
                self.store.reset(shortcut)
            else:
                self.store.set(shortcut, accelerators)
        except (ValueError, PermissionError, GLib.Error) as exc:
            self.hub.toast(str(exc))
        self._show(shortcut)

    def _refresh_all(self) -> None:
        for shortcut in self._rows:
            self._show(shortcut)
        self._fill_custom()

    # -- custom commands ------------------------------------------------------------

    def _fill_custom(self) -> None:
        for row in self._custom_rows:
            self.custom.remove(row)
        self._custom_rows = []
        entries = self.store.custom()
        if not entries:
            empty = Adw.ActionRow(title="No custom commands")
            empty.add_css_class("dim-label")
            self._custom_rows.append(empty)
            self.custom.add(empty)
        for entry in entries:
            row = Adw.ActionRow(title=markup(entry["name"] or "Unnamed"), subtitle=markup(entry["command"]))
            row.add_suffix(shortcut_label(entry["binding"]))
            edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Edit")
            edit.add_css_class("flat")
            edit.connect("clicked", lambda _b, e=entry: self._edit_custom(e))
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
            remove.add_css_class("flat")
            remove.connect("clicked", lambda _b, e=entry: self._remove_custom(e))
            row.add_suffix(edit)
            row.add_suffix(remove)
            self._custom_rows.append(row)
            self.custom.add(row)

    def _remove_custom(self, entry) -> None:
        self.store.remove_custom(entry["path"])
        self._fill_custom()

    def _edit_custom(self, entry) -> None:
        dialog = Adw.AlertDialog(heading="Edit Command" if entry else "Add Command")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        group = Adw.PreferencesGroup()
        name = Adw.EntryRow(title="Name", text=entry["name"] if entry else "")
        command = Adw.EntryRow(title="Command", text=entry["command"] if entry else "")
        binding = {"value": entry["binding"] if entry else ""}
        key_row = Adw.ActionRow(title="Shortcut")
        key_label = shortcut_label(binding["value"])
        key_row.add_suffix(key_label)
        pick = Gtk.Button(label="Set…", valign=Gtk.Align.CENTER)
        key_row.add_suffix(pick)

        def picked(accelerator):
            binding["value"] = accelerator
            key_label.set_accelerator(accelerator)
        pick.connect("clicked", lambda _b: CaptureDialog("Command Shortcut", picked).present(self.page))
        for row in (name, command, key_row):
            group.add(row)
        box.append(group)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def answered(_d, response):
            if response != "save":
                return
            path = entry["path"] if entry else None
            if binding["value"]:
                for clash in self.store.conflicts(binding["value"], exclude=(logic.CUSTOM, path)):
                    self.hub.toast(f"{binding['value']} is also used by “{clash['label']}”")
            try:
                self.store.save_custom(name.get_text(), command.get_text(), binding["value"], path)
            except (ValueError, GLib.Error) as exc:
                self.hub.toast(str(exc))
            self._fill_custom()
        dialog.connect("response", answered)
        dialog.present(self.page)
