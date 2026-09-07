"""List, diagnose, repair and remove the desktop entries the user owns."""
from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..backend import desktop_entry as de
from ..backend import icons, markup, scanner
from ..backend.scanner import ScannedEntry


class ManagePage(Gtk.Box):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._toasts = toast_overlay

        self._content = Adw.PreferencesPage()
        self._groups: list[Adw.PreferencesGroup] = []

        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(
            Adw.StatusPage(title="Scanning…", icon_name="content-loading-symbolic"), "loading"
        )
        self._stack.add_named(self._content, "list")
        self._stack.add_named(
            Adw.StatusPage(
                title="No desktop entries yet",
                description="Launchers you create will appear here.",
                icon_name="user-bookmarks-symbolic",
            ),
            "empty",
        )
        self.append(self._stack)

        self.refresh()

    def refresh(self) -> None:
        """Rescan off the main loop -- diagnose() shells out to
        desktop-file-validate once per entry, which is too slow to block on."""
        self._stack.set_visible_child_name("loading")

        def _worker() -> None:
            entries = scanner.scan_all()
            GLib.idle_add(self._rebuild, entries)

        threading.Thread(target=_worker, daemon=True).start()

    def _rebuild(self, entries: list[ScannedEntry]) -> bool:
        for group in self._groups:
            self._content.remove(group)
        self._groups.clear()

        if not entries:
            self._stack.set_visible_child_name("empty")
            return GLib.SOURCE_REMOVE

        for location, description in (
            ("Desktop", "Icons on your desktop."),
            ("Applications", "Entries in your applications menu."),
        ):
            in_location = [e for e in entries if e.location == location]
            if not in_location:
                continue
            broken = sum(1 for e in in_location if not e.ok)
            group = Adw.PreferencesGroup(
                title=location,
                description=(
                    f"{description}  {broken} of {len(in_location)} need attention."
                    if broken
                    else description
                ),
            )
            for entry in in_location:
                group.add(_EntryRow(entry, self))
            self._content.add(group)
            self._groups.append(group)

        self._stack.set_visible_child_name("list")
        return GLib.SOURCE_REMOVE

    # -- actions invoked by the rows ---------------------------------------

    def repair(self, entry: ScannedEntry) -> None:
        actions = scanner.repair(entry)
        if not actions:
            self._toasts.add_toast(Adw.Toast(title="Nothing here can be repaired automatically"))
        else:
            self._toasts.add_toast(Adw.Toast(title=markup("; ".join(actions))))
        self.refresh()

    def confirm_remove(self, entry: ScannedEntry) -> None:
        body = f"“{entry.name}” will be deleted from {entry.location}."
        if entry.is_symlink:
            # Worth spelling out: several entries here are symlinks into project
            # checkouts, and the obvious fear is that removing one deletes the
            # project file. It does not.
            body += (
                "\n\nThis is a link to:\n"
                f"{entry.symlink_target}\n\n"
                "Only the link is removed. The original file is left alone."
            )

        dialog = Adw.AlertDialog(heading="Remove this launcher?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_response, entry)
        dialog.present(self.get_root())

    def _on_remove_response(self, _dialog, response: str, entry: ScannedEntry) -> None:
        if response != "remove":
            return
        try:
            scanner.remove(entry)
        except OSError as exc:
            self._toasts.add_toast(Adw.Toast(title=markup(f"Could not remove: {exc}")))
            return
        self._toasts.add_toast(Adw.Toast(title=f"Removed “{entry.name}”"))
        self.refresh()

    def edit(self, entry: ScannedEntry) -> None:
        _EditDialog(entry, self).present(self.get_root())


class _EntryRow(Adw.ExpanderRow):
    def __init__(self, entry: ScannedEntry, page: ManagePage):
        super().__init__(title=GLib.markup_escape_text(entry.name))
        self._entry = entry
        self._page = page

        subtitle = entry.basename
        if entry.is_symlink:
            subtitle += "  (link)"
        self.set_subtitle(GLib.markup_escape_text(subtitle))

        image = Gtk.Image(pixel_size=32)
        image.set_from_gicon(icons.icon_from_string(entry.icon))
        self.add_prefix(image)

        if entry.ok:
            status = Gtk.Image(icon_name="emblem-ok-symbolic", valign=Gtk.Align.CENTER)
            status.set_tooltip_text("No problems found")
            if any(i.severity == "warning" for i in entry.issues):
                status.set_from_icon_name("dialog-warning-symbolic")
                status.add_css_class("issue-warning")
                status.set_tooltip_text("Works, but has warnings")
        else:
            status = Gtk.Image(icon_name="dialog-error-symbolic", valign=Gtk.Align.CENTER)
            status.add_css_class("issue-error")
            status.set_tooltip_text("This launcher will not work")
        self.add_suffix(status)

        self._add_detail("Command", entry.exec_line or "(none)")
        if entry.is_symlink:
            self._add_detail("Links to", entry.symlink_target)

        for issue in entry.issues:
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(issue.message),
                subtitle=GLib.markup_escape_text(issue.hint) if issue.hint else None,
            )
            icon = Gtk.Image(
                icon_name="dialog-error-symbolic"
                if issue.severity == "error"
                else "dialog-warning-symbolic",
                valign=Gtk.Align.CENTER,
            )
            icon.add_css_class(f"issue-{issue.severity}")
            row.add_prefix(icon)
            self.add_row(row)

        self.add_row(self._build_actions(entry))

    def _add_detail(self, title: str, value: str) -> None:
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value))
        row.set_subtitle_selectable(True)
        row.add_css_class("monospace-dim")
        self.add_row(row)

    def _build_actions(self, entry: ScannedEntry) -> Gtk.ListBoxRow:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_halign(Gtk.Align.END)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # Only offer Repair when there is something mechanical to fix. A
        # missing Exec target isn't repairable without asking the user where
        # the program went, so that path leads to Edit instead.
        repairable = (not entry.executable) or (
            not entry.trusted and entry.location == "Desktop"
        )
        if repairable:
            repair = Gtk.Button(label="Repair")
            repair.add_css_class("suggested-action")
            repair.connect("clicked", lambda _b: self._page.repair(entry))
            box.append(repair)

        edit = Gtk.Button(label="Edit")
        edit.connect("clicked", lambda _b: self._page.edit(entry))
        box.append(edit)

        remove = Gtk.Button(label="Remove")
        remove.add_css_class("destructive-action")
        remove.connect("clicked", lambda _b: self._page.confirm_remove(entry))
        box.append(remove)

        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        row.set_child(box)
        return row


class _EditDialog(Adw.Dialog):
    def __init__(self, entry: ScannedEntry, page: ManagePage):
        super().__init__(title="Edit Launcher", content_width=520)
        self._entry = entry
        self._page = page

        self._name = Adw.EntryRow(title="Name", text=entry.name)
        self._exec = Adw.EntryRow(title="Command", text=entry.exec_line)
        self._comment = Adw.EntryRow(title="Comment", text=entry.comment)
        self._icon = Adw.EntryRow(title="Icon (name or path)", text=entry.icon)

        group = Adw.PreferencesGroup()
        for row in (self._name, self._exec, self._comment, self._icon):
            group.add(row)

        prefs = Adw.PreferencesPage()
        prefs.add(group)

        if entry.is_symlink:
            warn = Adw.PreferencesGroup()
            warn.add(
                Adw.ActionRow(
                    title="This entry is a link",
                    subtitle=markup(f"Saving edits the original file:\n{entry.symlink_target}"),
                )
            )
            prefs.add(warn)

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda _b: self._save())
        header.pack_end(save)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(prefs)
        self.set_child(view)

    def _save(self) -> None:
        kf = de.load_keyfile(self._entry.path)
        if kf is None:
            self._page._toasts.add_toast(Adw.Toast(title="Could not read this entry"))
            self.close()
            return

        kf.set_string(de.DESKTOP_GROUP, "Name", self._name.get_text().strip())
        kf.set_string(de.DESKTOP_GROUP, "Exec", self._exec.get_text().strip())
        comment = self._comment.get_text().strip()
        if comment:
            kf.set_string(de.DESKTOP_GROUP, "Comment", comment)
        icon = self._icon.get_text().strip()
        if icon:
            kf.set_string(de.DESKTOP_GROUP, "Icon", icon)

        # Write through the symlink to the real file, so a linked entry keeps
        # its link instead of being silently replaced by a regular file.
        target = os.path.realpath(self._entry.path)
        try:
            de.write_entry(kf, target, trust=self._entry.location == "Desktop")
        except OSError as exc:
            self._page._toasts.add_toast(Adw.Toast(title=markup(f"Could not save: {exc}")))
            self.close()
            return

        self._page._toasts.add_toast(Adw.Toast(title="Saved"))
        self.close()
        self._page.refresh()
