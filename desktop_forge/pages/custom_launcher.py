"""Turn a script or binary into a desktop launcher.

This is the replacement for hand-writing .desktop files for personal projects
(the way ~/Desktop/photo-video-organizer.desktop was made).
"""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from ..backend import desktop_entry as de
from ..backend import markup
from ..backend import icons

# The XDG registered main categories, which are what a menu actually keys off.
CATEGORIES = [
    "Utility", "Development", "Graphics", "AudioVideo", "Office",
    "Network", "System", "Settings", "Game", "Education", "Science",
]


class CustomLauncherPage(Adw.PreferencesPage):
    def __init__(self, toast_overlay: Adw.ToastOverlay, on_created=None):
        super().__init__()
        self._toasts = toast_overlay
        self._on_created = on_created
        self._icon_path = ""

        program_group = Adw.PreferencesGroup(
            title="Program",
            description="The script or binary this launcher runs.",
        )
        self._program_row = Adw.EntryRow(title="Program or script")
        self._program_row.connect("changed", self._on_program_changed)
        browse = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER)
        browse.add_css_class("flat")
        browse.set_tooltip_text("Browse for a program")
        browse.connect("clicked", self._on_browse_program)
        self._program_row.add_suffix(browse)
        program_group.add(self._program_row)

        self._args_row = Adw.EntryRow(title="Arguments (optional)")
        program_group.add(self._args_row)

        self._workdir_row = Adw.EntryRow(title="Working directory (optional)")
        program_group.add(self._workdir_row)
        self.add(program_group)

        appearance = Adw.PreferencesGroup(title="Appearance")
        self._name_row = Adw.EntryRow(title="Name")
        self._name_row.connect("changed", lambda _r: self._update_sensitivity())
        appearance.add(self._name_row)

        self._comment_row = Adw.EntryRow(title="Comment (optional)")
        appearance.add(self._comment_row)

        self._icon_row = Adw.ActionRow(
            title="Icon",
            subtitle="Using the default application icon",
        )
        self._icon_image = Gtk.Image(pixel_size=32)
        self._icon_image.set_from_icon_name(icons.FALLBACK_ICON)
        self._icon_row.add_prefix(self._icon_image)
        pick_icon = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        pick_icon.connect("clicked", self._on_browse_icon)
        self._icon_row.add_suffix(pick_icon)
        clear_icon = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER)
        clear_icon.add_css_class("flat")
        clear_icon.set_tooltip_text("Clear icon")
        clear_icon.connect("clicked", lambda _b: self._set_icon(""))
        self._icon_row.add_suffix(clear_icon)
        appearance.add(self._icon_row)
        self.add(appearance)

        behaviour = Adw.PreferencesGroup(title="Behaviour")
        self._category_row = Adw.ComboRow(
            title="Category",
            model=Gtk.StringList.new(CATEGORIES),
        )
        behaviour.add(self._category_row)

        self._terminal_row = Adw.SwitchRow(
            title="Run in a terminal",
            subtitle="For command-line programs that print output.",
        )
        behaviour.add(self._terminal_row)

        self._also_menu_row = Adw.SwitchRow(
            title="Also add to the applications menu",
            subtitle="Makes it searchable in the app grid, not just on the desktop.",
        )
        self._also_menu_row.set_active(True)
        behaviour.add(self._also_menu_row)
        self.add(behaviour)

        actions = Adw.PreferencesGroup()
        self._create_button = Gtk.Button(label="Create Launcher")
        self._create_button.add_css_class("suggested-action")
        self._create_button.add_css_class("pill")
        self._create_button.set_halign(Gtk.Align.CENTER)
        self._create_button.set_sensitive(False)
        self._create_button.connect("clicked", lambda _b: self._create())
        actions.add(self._create_button)
        self.add(actions)

    # -- program / icon pickers -------------------------------------------

    def _on_browse_program(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Choose a program or script")
        dialog.open(self.get_root(), None, self._on_program_chosen)

    def _on_program_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # user cancelled
        if gfile is None:
            return
        path = gfile.get_path()
        if not path:
            return
        self._program_row.set_text(path)
        if not self._name_row.get_text().strip():
            # A sensible default the user can immediately overwrite: the file
            # name, tidied into something that reads like an app name.
            stem = os.path.splitext(os.path.basename(path))[0]
            self._name_row.set_text(stem.replace("-", " ").replace("_", " ").title())

    def _on_browse_icon(self, _button) -> None:
        dialog = Gtk.FileDialog(title="Choose an icon")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images")
        for pattern in ("*.svg", "*.png", "*.jpg", "*.jpeg", "*.xpm", "*.ico"):
            image_filter.add_pattern(pattern)
        filters.append(image_filter)
        dialog.set_filters(filters)
        dialog.open(self.get_root(), None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is not None and gfile.get_path():
            self._set_icon(gfile.get_path())

    def _set_icon(self, path: str) -> None:
        self._icon_path = path
        if path:
            self._icon_image.set_from_gicon(icons.icon_from_string(path))
            self._icon_row.set_subtitle(markup(path))
        else:
            self._icon_image.set_from_icon_name(icons.FALLBACK_ICON)
            self._icon_row.set_subtitle("Using the default application icon")

    # -- validation / creation --------------------------------------------

    def _on_program_changed(self, _row) -> None:
        self._update_sensitivity()
        program = self._program_row.get_text().strip()
        if program and not de.program_exists(program):
            self._program_row.add_css_class("error")
            self._program_row.set_tooltip_text(
                "This program was not found, or is not executable."
            )
        else:
            self._program_row.remove_css_class("error")
            self._program_row.set_tooltip_text(None)

    def _update_sensitivity(self) -> None:
        has_program = bool(self._program_row.get_text().strip())
        has_name = bool(self._name_row.get_text().strip())
        self._create_button.set_sensitive(has_program and has_name)

    def _spec(self) -> de.LauncherSpec:
        selected = self._category_row.get_selected()
        category = CATEGORIES[selected] if selected < len(CATEGORIES) else "Utility"
        return de.LauncherSpec(
            name=self._name_row.get_text().strip(),
            exec_line=de.quote_exec(
                self._program_row.get_text().strip(), self._args_row.get_text().strip()
            ),
            comment=self._comment_row.get_text().strip(),
            icon=self._icon_path,
            categories=[category],
            terminal=self._terminal_row.get_active(),
            working_dir=self._workdir_row.get_text().strip(),
        )

    def _create(self) -> None:
        spec = self._spec()
        kf = de.build_keyfile(spec)

        targets = [de.desktop_dir()]
        if self._also_menu_row.get_active():
            targets.append(de.applications_dir())

        written: list[str] = []
        desktop_trusted = True
        try:
            for directory in targets:
                path = de.unique_path(directory, spec.filename())
                # Only the copy on the desktop needs the trusted flag; the one
                # in the applications menu is launched by the shell, not DING.
                on_desktop = directory == de.desktop_dir()
                trusted = de.write_entry(kf, path, trust=on_desktop)
                if on_desktop:
                    desktop_trusted = trusted
                written.append(path)
        except OSError as exc:
            self._toasts.add_toast(Adw.Toast(title=markup(f"Could not create launcher: {exc}")))
            return

        problems = de.validate(written[0])
        if problems:
            message = f"Created, but with warnings: {problems[0]}"
        elif not desktop_trusted:
            message = f"“{spec.name}” created. {de.TRUST_HINT}"
        else:
            message = f"“{spec.name}” created"

        toast = Adw.Toast(title=markup(message))
        toast.set_timeout(8 if not desktop_trusted else 5)
        self._toasts.add_toast(toast)

        self._reset()
        if self._on_created is not None:
            self._on_created()

    def _reset(self) -> None:
        for row in (self._program_row, self._args_row, self._workdir_row,
                    self._name_row, self._comment_row):
            row.set_text("")
        self._set_icon("")
        self._terminal_row.set_active(False)
