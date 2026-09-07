"""General desktop appearance settings."""
from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from ..backend import markup
from ..backend.folder_colors import FolderColor, FolderColors, PRESETS, normalize_color, render_icon


def _texture(color: str) -> Gdk.Texture:
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(render_icon(color)))
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
        return Gdk.Texture.new_for_pixbuf(pixbuf)
    finally:
        stream.close(None)


class FolderColorDialog(Adw.Dialog):
    def __init__(self, folder: Gio.File, color: str, apply):
        super().__init__(title="Folder Color", content_width=420)
        self._apply = apply
        self._syncing = False
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                          margin_start=24, margin_end=24, margin_top=12, margin_bottom=24)
        self.preview = Gtk.Image(pixel_size=96, halign=Gtk.Align.CENTER)
        content.append(self.preview)
        name = Gtk.Label(label=folder.get_basename(), wrap=True)
        name.add_css_class("title-3")
        content.append(name)
        path = Gtk.Label(label=folder.get_path(), wrap=True, selectable=True)
        path.add_css_class("dim-label")
        content.append(path)

        swatches = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                               max_children_per_line=5, min_children_per_line=3,
                               homogeneous=True, row_spacing=4, column_spacing=4)
        for label, value in PRESETS:
            button = Gtk.Button(tooltip_text=label)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.add_css_class("flat")
            button.set_child(Gtk.Image(paintable=_texture(value), pixel_size=36))
            button.connect("clicked", lambda _b, c=value: self.hex.set_text(c))
            swatches.insert(button, -1)
        content.append(swatches)

        group = Adw.PreferencesGroup()
        self.hex = Adw.EntryRow(title="Hex color (#RRGGBB)")
        self.picker = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                           valign=Gtk.Align.CENTER, tooltip_text="Choose a custom color")
        self.hex.add_suffix(self.picker)
        group.add(self.hex)
        content.append(group)
        self.error = Gtk.Label(label="Enter six hexadecimal digits after #.", wrap=True, visible=False)
        self.error.add_css_class("error")
        content.append(self.error)
        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        actions.append(cancel)
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", self._on_apply)
        actions.append(self.apply_button)
        content.append(actions)
        self.hex.connect("changed", self._on_hex)
        self.hex.connect("entry-activated", lambda _r: self._on_apply())
        self.picker.connect("notify::rgba", self._on_picker)
        self.hex.set_text(color)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    propagate_natural_height=True, child=content)
        toolbar.set_content(scroll)
        self.set_child(toolbar)

    def _on_hex(self, _row) -> None:
        try:
            color = normalize_color(self.hex.get_text())
        except ValueError:
            self.apply_button.set_sensitive(False)
            self.error.set_visible(True)
            return
        self.error.set_visible(False)
        self.apply_button.set_sensitive(True)
        self.preview.set_from_paintable(_texture(color))
        self._syncing = True
        rgba = Gdk.RGBA()
        rgba.parse(color)
        self.picker.set_rgba(rgba)
        self._syncing = False

    def _on_picker(self, button, _pspec) -> None:
        if not self._syncing:
            rgba = button.get_rgba()
            self.hex.set_text("#{:02x}{:02x}{:02x}".format(
                round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)))

    def _on_apply(self, *_args) -> None:
        if self.apply_button.get_sensitive():
            self._apply(normalize_color(self.hex.get_text()), self)


class OverallPage(Adw.PreferencesPage):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__()
        self._toasts = toast_overlay
        self._store = FolderColors()
        self._busy = False
        self._rows = []
        self._group = Adw.PreferencesGroup(
            title="Folder Colors",
            description="Give individual folders their own color in Files and on the desktop.",
        )
        self._add_button = Gtk.Button(label="Add Folder", valign=Gtk.Align.CENTER)
        self._add_button.connect("clicked", lambda _b: self._choose_folder())
        self._group.set_header_suffix(self._add_button)
        self.add(self._group)
        self.reload()

    def reload(self) -> None:
        if self._busy:
            return
        for row in self._rows:
            self._group.remove(row)
        self._rows.clear()
        self._add_button.set_sensitive(True)
        try:
            entries = self._store.load()
        except (ValueError, OSError) as exc:
            self._add_row(Adw.ActionRow(title="Folder colors are unavailable", subtitle=markup(exc)))
            self._add_button.set_sensitive(False)
            return
        if not entries:
            self._add_row(Adw.ActionRow(
                title="Choose your first folder",
                subtitle="Use Add Folder to preview a color before applying it.",
            ))
        for entry in sorted(entries, key=lambda item: item.path.casefold()):
            self._add_row(self._folder_row(entry))

    def _add_row(self, row) -> None:
        self._rows.append(row)
        self._group.add(row)

    def _folder_row(self, entry: FolderColor) -> Adw.ActionRow:
        available = os.path.isdir(entry.path)
        row = Adw.ActionRow(title=markup(os.path.basename(entry.path) or entry.path),
                            subtitle=markup(entry.path if available else f"Unavailable · {entry.path}"))
        row.add_prefix(Gtk.Image(paintable=_texture(entry.color), pixel_size=48))
        if available:
            change = Gtk.Button(label="Change Color", valign=Gtk.Align.CENTER)
            change.connect("clicked", lambda _b: self._edit(Gio.File.new_for_uri(entry.uri), entry.color))
            row.add_suffix(change)
            reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text="Reset to the previous folder icon")
            reset.add_css_class("flat")
            reset.connect("clicked", lambda _b: self._operate(lambda: self._store.reset(entry.uri),
                                                              "Folder color reset"))
            row.add_suffix(reset)
        else:
            locate = Gtk.Button(label="Locate", valign=Gtk.Align.CENTER)
            locate.connect("clicked", lambda _b: self._choose_folder(entry))
            row.add_suffix(locate)
            forget = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Remove unavailable folder from this list")
            forget.add_css_class("flat")
            forget.connect("clicked", lambda _b: self._operate(lambda: self._store.forget(entry.uri),
                                                               "Folder removed from the list"))
            row.add_suffix(forget)
        return row

    def _choose_folder(self, missing: FolderColor | None = None) -> None:
        picker = Gtk.FileDialog(title="Locate Folder" if missing else "Choose a Folder")
        self._group.set_sensitive(False)

        def selected(dialog, result):
            self._group.set_sensitive(True)
            try:
                folder = dialog.select_folder_finish(result)
                if not folder.is_native():
                    raise ValueError("Choose a local folder.")
                if missing:
                    self._operate(lambda: self._store.relocate(missing.uri, folder.get_uri()),
                                  "Folder location updated")
                else:
                    canonical = self._store.folder(folder.get_uri())
                    entry = next((item for item in self._store.load() if item.uri == canonical.get_uri()), None)
                    self._edit(canonical, entry.color if entry else PRESETS[0][1])
            except GLib.Error as exc:
                if not exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    self._toast(str(exc))
            except (ValueError, OSError) as exc:
                self._toast(str(exc))

        picker.select_folder(self.get_root(), None, selected)

    def _edit(self, folder: Gio.File, color: str) -> None:
        dialog = FolderColorDialog(folder, color, lambda value, editor: self._operate(
            lambda: self._store.apply(folder.get_uri(), value), "Folder color applied", editor))
        dialog.present(self)

    def _operate(self, operation, message: str, dialog: FolderColorDialog | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        self._group.set_sensitive(False)
        root = self.get_root()
        application = root.get_application() if isinstance(root, Gtk.ApplicationWindow) else None
        if application:
            application.hold()
        if dialog:
            dialog.set_can_close(False)
            dialog.get_child().set_sensitive(False)

        def finish(error):
            self._busy = False
            self._group.set_sensitive(True)
            if dialog:
                dialog.set_can_close(True)
                dialog.get_child().set_sensitive(True)
                if error is None:
                    dialog.close()
            self.reload()
            self._toast(f"Could not update folder color: {error}" if error else message)
            if application:
                application.release()
            return GLib.SOURCE_REMOVE

        def work():
            try:
                operation()
            except Exception as exc:
                GLib.idle_add(finish, str(exc))
            else:
                GLib.idle_add(finish, None)

        threading.Thread(target=work, daemon=True).start()

    def _toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=markup(message)))
