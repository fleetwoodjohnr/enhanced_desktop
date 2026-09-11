"""General desktop appearance settings."""
from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from .. import config
from ..backend import markup
from ..backend.desktop_icons import (DesktopIcons, ICON_SIZES, restart_ding,
                                     sync_stylesheet)
from ..backend.folder_colors import FolderColor, FolderColors, PRESETS, normalize_color, render_icon
from ..backend.shell_chrome import DashToDock


SAVE_DELAY_MS = 400
VISIBILITY_VALUES = ["always", "intelligent", "auto"]
TOP_POSITION_VALUES = ["top", "bottom"]
DOCK_POSITION_VALUES = ["TOP", "RIGHT", "BOTTOM", "LEFT"]
ICON_MATERIAL_VALUES = ["system", "solid", "frosted", "liquid"]
ICON_ARTWORK_VALUES = ["original", "monochrome"]
_DELETE = object()


def _hex_from_rgba(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
    )


def _automatic_foreground(background: str) -> str:
    """Choose whichever of black or white has the stronger WCAG contrast."""
    try:
        color = normalize_color(background)
    except ValueError:
        return "#ffffff"
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
              for value in channels]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    white_contrast = 1.05 / (luminance + 0.05)
    black_contrast = (luminance + 0.05) / 0.05
    return "#ffffff" if white_contrast >= black_contrast else "#000000"


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
        self._config = config.load()
        self._chrome_pending: dict[tuple[str, str], object] = {}
        self._icon_pending: dict[str, object] = {}
        self._save_source = 0
        self._syncing_chrome = False
        self._colour_controls = {}
        self._auto_rows = {}

        self._top_group = Adw.PreferencesGroup(
            title="Top Bar",
            description="Customize the bar containing the clock, network, sound, and system menus.",
        )
        self.add(self._top_group)
        self._build_top_bar()

        self._dock = DashToDock.find()
        self._dock_group = Adw.PreferencesGroup(
            title="Dock",
            description="Appearance and behavior for Dash to Dock.",
        )
        self.add(self._dock_group)
        self._build_dock()

        self._desktop_icons = DesktopIcons.find()
        self._icons_group = Adw.PreferencesGroup(
            title="Desktop Icons",
            description=("Choose icon size, installed icon artwork, and an optional "
                         "color or glass finish for icons on the desktop."),
        )
        self.add(self._icons_group)
        self._build_desktop_icons()

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

        Adw.StyleManager.get_default().connect("notify::dark", self._on_system_theme_changed)
        self.reload()

    # -- shell chrome -----------------------------------------------------

    def _build_top_bar(self) -> None:
        options = self._config.chrome_options("top_bar")
        self._top_visibility = Adw.ComboRow(
            title="Visibility",
            subtitle="Intelligent hide follows Dash to Dock's window rule and reveal behavior",
            model=Gtk.StringList.new(["Always Visible", "Intelligent Hide", "Auto-hide"]),
        )
        self._top_visibility.set_selected(self._index(
            VISIBILITY_VALUES, options.get("visibility"), 0
        ))
        self._top_visibility.connect("notify::selected", self._on_top_visibility)
        self._top_group.add(self._top_visibility)

        self._top_position = Adw.ComboRow(
            title="Position",
            model=Gtk.StringList.new(["Top", "Bottom"]),
        )
        self._top_position.set_selected(self._index(
            TOP_POSITION_VALUES, options.get("position"), 0
        ))
        self._top_position.connect("notify::selected", self._on_top_position)
        self._top_group.add(self._top_position)

        self._top_height = Adw.SpinRow.new_with_range(24, 64, 2)
        self._top_height.set_title("Height")
        self._top_height.set_subtitle("Logical pixels")
        self._top_height.set_value(options.get("height", 32))
        self._top_height.connect("notify::value", self._on_chrome_number,
                                 "top_bar", "height", int)
        self._top_group.add(self._top_height)

        self._add_appearance(self._top_group, "top_bar")

    def _build_dock(self) -> None:
        self._dock_warning = Adw.ActionRow(
            title="Dock and top bar use the same edge",
            subtitle="Move either surface to prevent an overlap.",
            visible=False,
        )
        self._dock_warning.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        self._dock_group.add(self._dock_warning)

        if self._dock is None:
            self._dock_group.add(Adw.ActionRow(
                title="Dash to Dock is unavailable",
                subtitle="Install or enable Dash to Dock to configure a dock here.",
            ))
            return

        state = self._dock.read()
        self._dock_visibility = Adw.ComboRow(
            title="Visibility",
            subtitle="Intelligent hide dodges application windows",
            model=Gtk.StringList.new(["Always Visible", "Intelligent Hide", "Auto-hide"]),
        )
        self._dock_visibility.set_selected(self._index(
            VISIBILITY_VALUES, state.visibility, 0
        ))
        self._dock_visibility.connect("notify::selected", self._on_dock_visibility)
        self._dock_visibility.set_sensitive(self._dock.writable(
            "dock-fixed", "autohide", "intellihide", "manualhide"
        ))
        self._dock_group.add(self._dock_visibility)

        self._dock_position = Adw.ComboRow(
            title="Position",
            model=Gtk.StringList.new(["Top", "Right", "Bottom", "Left"]),
        )
        self._dock_position.set_selected(self._index(
            DOCK_POSITION_VALUES, state.position, 2
        ))
        self._dock_position.connect("notify::selected", self._on_dock_position)
        self._dock_position.set_sensitive(self._dock.writable("dock-position"))
        self._dock_group.add(self._dock_position)

        self._dock_icon_size = Adw.SpinRow.new_with_range(16, 64, 2)
        self._dock_icon_size.set_title("Maximum icon size")
        self._dock_icon_size.set_subtitle("Pixels")
        self._dock_icon_size.set_value(state.icon_size)
        self._dock_icon_size.connect("notify::value", self._on_dock_icon_size)
        self._dock_icon_size.set_sensitive(self._dock.writable("dash-max-icon-size"))
        self._dock_group.add(self._dock_icon_size)

        self._dock_length = Adw.SpinRow.new_with_range(33, 100, 1)
        self._dock_length.set_title("Maximum length")
        self._dock_length.set_subtitle("Percent of the available screen edge")
        self._dock_length.set_value(round(state.maximum_length * 100))
        self._dock_length.connect("notify::value", self._on_dock_length)
        self._dock_length.set_sensitive(self._dock.writable(
            "height-fraction", "extend-height"
        ))
        self._dock_group.add(self._dock_length)

        self._add_appearance(self._dock_group, "dock")
        self._dock.settings.connect("changed", self._on_dock_settings_changed)

    def _build_desktop_icons(self) -> None:
        if not self._desktop_icons.ding_available:
            unavailable = Adw.ActionRow(
                title="Desktop Icons NG is unavailable",
                subtitle="Install or enable DING to size and style icons on the desktop.",
            )
            unavailable.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            self._icons_group.add(unavailable)

        self._desktop_icon_size = Adw.ComboRow(
            title="Icon size",
            subtitle="Uses Desktop Icons NG's native grid sizes",
            model=Gtk.StringList.new(["Tiny (36 px)", "Small (48 px)",
                                      "Standard (64 px)", "Large (96 px)"]),
        )
        self._desktop_icon_size.set_sensitive(self._desktop_icons.ding_available)
        self._desktop_icon_size.connect("notify::selected", self._on_desktop_icon_size)
        self._icons_group.add(self._desktop_icon_size)

        default_theme = self._desktop_icons.default_icon_theme()
        discovered = self._desktop_icons.themes()
        current_theme = self._desktop_icons.icon_theme()
        if current_theme and all(theme.identifier != current_theme for theme in discovered):
            from ..backend.desktop_icons import IconTheme
            discovered.append(IconTheme(current_theme, current_theme))
            discovered.sort(key=lambda theme: theme.name.casefold())
        self._icon_themes = [None, *discovered]
        theme_labels = [f"System Default ({default_theme})", *[
            theme.name for theme in self._icon_themes[1:]
        ]]
        self._icon_theme = Adw.ComboRow(
            title="Icon pack",
            subtitle="Changes icons throughout GNOME, including Files, the dock, and app grid",
            model=Gtk.StringList.new(theme_labels),
        )
        self._icon_theme.set_sensitive(self._desktop_icons.interface is not None)
        self._icon_theme.connect("notify::selected", self._on_desktop_icon_theme)
        self._icons_group.add(self._icon_theme)

        self._icon_material = Adw.ComboRow(
            title="Material",
            subtitle="Liquid Glass uses a translucent tint, highlight, rim, and depth",
            model=Gtk.StringList.new(["System", "Solid Tint", "Frosted Glass", "Liquid Glass"]),
        )
        self._icon_material.set_sensitive(self._desktop_icons.ding_available)
        self._icon_material.connect("notify::selected", self._on_icon_material)
        self._icons_group.add(self._icon_material)

        options = self._config.desktop_icon_options()
        tint = Gdk.RGBA()
        tint.parse(options["tint"])
        self._icon_tint_row = Adw.ActionRow(
            title="Glass tint",
            subtitle="Color behind each icon; reset restores the Desktop Forge blue",
        )
        self._icon_tint = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=False), rgba=tint,
            valign=Gtk.Align.CENTER, tooltip_text="Choose glass tint",
        )
        self._icon_tint_handler = self._icon_tint.connect(
            "notify::rgba", self._on_icon_tint
        )
        self._icon_tint_reset = Gtk.Button(
            icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Reset glass tint",
        )
        self._icon_tint_reset.add_css_class("flat")
        self._icon_tint_reset.connect("clicked", self._on_icon_tint_reset)
        self._icon_tint_row.add_suffix(self._icon_tint_reset)
        self._icon_tint_row.add_suffix(self._icon_tint)
        self._icons_group.add(self._icon_tint_row)

        self._icon_opacity = Adw.SpinRow.new_with_range(0.05, 1, 0.05)
        self._icon_opacity.set_title("Tint strength")
        self._icon_opacity.set_subtitle("Translucency of the icon material")
        self._icon_opacity.set_digits(2)
        self._icon_opacity.connect("notify::value", self._on_icon_opacity)
        self._icons_group.add(self._icon_opacity)

        self._icon_artwork = Adw.ComboRow(
            title="Artwork finish",
            subtitle="Monochrome removes color from full-color icon artwork",
            model=Gtk.StringList.new(["Original", "Monochrome"]),
        )
        self._icon_artwork.connect("notify::selected", self._on_icon_artwork)
        self._icons_group.add(self._icon_artwork)

        self._icon_auto_foreground = Adw.SwitchRow(
            title="Automatic label contrast",
            subtitle="Follow GNOME's readable light or dark text color",
        )
        self._icon_auto_foreground.connect(
            "notify::active", self._on_icon_auto_foreground
        )
        self._icons_group.add(self._icon_auto_foreground)

        foreground = Gdk.RGBA()
        foreground.parse(options["foreground"])
        self._icon_foreground_row = Adw.ActionRow(
            title="Label color",
            subtitle="Used when automatic contrast is switched off",
        )
        self._icon_foreground = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=False), rgba=foreground,
            valign=Gtk.Align.CENTER, tooltip_text="Choose label color",
        )
        self._icon_foreground_handler = self._icon_foreground.connect(
            "notify::rgba", self._on_icon_foreground
        )
        self._icon_foreground_row.add_suffix(self._icon_foreground)
        self._icons_group.add(self._icon_foreground_row)

        for control in (self._icon_material, self._icon_tint_row, self._icon_opacity,
                        self._icon_artwork, self._icon_auto_foreground,
                        self._icon_foreground_row):
            control.set_sensitive(self._desktop_icons.ding_available)

        if self._desktop_icons.ding:
            self._desktop_icons.ding.connect("changed::icon-size", self._on_native_icons_changed)
        if self._desktop_icons.interface:
            self._desktop_icons.interface.connect(
                "changed::icon-theme", self._on_native_icons_changed
            )

    def _add_appearance(self, group: Adw.PreferencesGroup, surface: str) -> None:
        options = self._config.chrome_options(surface)
        opacity = Adw.SpinRow.new_with_range(0, 1, 0.05)
        opacity.set_title("Background opacity")
        opacity.set_subtitle("Zero is fully transparent; one is solid")
        opacity.set_digits(2)
        opacity.set_value(options.get("opacity", config.DEFAULT_CHROME[surface]["opacity"]))
        opacity.connect("notify::value", self._on_chrome_number,
                        surface, "opacity", float)
        setattr(self, f"_{surface}_opacity", opacity)
        group.add(self._colour_row(group, surface, "background", "Background color"))
        group.add(opacity)

        automatic = Adw.SwitchRow(
            title="Automatic foreground contrast",
            subtitle="Choose black or white text and symbols for maximum contrast",
            active=options.get("foreground_mode", "auto") == "auto",
        )
        automatic.connect("notify::active", self._on_automatic_contrast, surface)
        self._auto_rows[surface] = automatic
        group.add(automatic)
        group.add(self._colour_row(group, surface, "foreground", "Text and icon color"))
        self._colour_controls[(surface, "foreground")][0].set_sensitive(
            not automatic.get_active()
        )

    def _colour_row(
        self, _group: Adw.PreferencesGroup, surface: str, key: str, title: str
    ) -> Adw.ActionRow:
        options = self._config.chrome_options(surface)
        value = options.get(key) or self._adaptive_colour(surface, key)
        if key == "foreground" and options.get("foreground_mode", "auto") == "auto":
            value = _automatic_foreground(
                options.get("background") or self._adaptive_colour(surface, "background")
            )
        rgba = Gdk.RGBA()
        rgba.parse(value)
        row = Adw.ActionRow(
            title=title,
            subtitle="Reset to follow the adaptive system palette" if key == "background"
                     else "Used when automatic contrast is switched off",
        )
        picker = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=False), rgba=rgba,
            valign=Gtk.Align.CENTER, tooltip_text=f"Choose {title.lower()}",
        )
        handler = picker.connect("notify::rgba", self._on_chrome_colour,
                                 surface, key)
        reset = Gtk.Button(
            icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text=f"Reset {title.lower()}",
        )
        reset.add_css_class("flat")
        reset.set_sensitive(key in self._config.chrome.get(surface, {}))
        reset.connect("clicked", self._on_chrome_colour_reset, surface, key)
        row.add_suffix(reset)
        row.add_suffix(picker)
        self._colour_controls[(surface, key)] = (picker, handler, reset)
        return row

    @staticmethod
    def _index(values: list[str], value, fallback: int) -> int:
        return values.index(value) if value in values else fallback

    @staticmethod
    def _adaptive_colour(surface: str, key: str) -> str:
        palette = "dark" if Adw.StyleManager.get_default().get_dark() else "light"
        return config.CHROME_PALETTES[palette][surface][key]

    def _on_top_visibility(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._queue_chrome("top_bar", "visibility",
                               VISIBILITY_VALUES[row.get_selected()])

    def _on_top_position(self, row, _pspec) -> None:
        if self._syncing_chrome:
            return
        requested = TOP_POSITION_VALUES[row.get_selected()]
        if self._dock and self._dock.read().position == requested.upper():
            self._restore_top_position()
            self._toast("Move the dock first — both surfaces cannot use the same edge")
            return
        self._queue_chrome("top_bar", "position", requested)
        self._refresh_conflict_warning()

    def _restore_top_position(self) -> None:
        value = self._config.chrome_options("top_bar").get("position", "top")
        self._syncing_chrome = True
        self._top_position.set_selected(self._index(TOP_POSITION_VALUES, value, 0))
        self._syncing_chrome = False

    def _on_chrome_number(self, row, _pspec, surface: str, key: str, cast) -> None:
        if not self._syncing_chrome:
            self._queue_chrome(surface, key, cast(row.get_value()))

    def _on_chrome_colour(self, button, _pspec, surface: str, key: str) -> None:
        if self._syncing_chrome:
            return
        self._queue_chrome(surface, key, _hex_from_rgba(button.get_rgba()))
        self._colour_controls[(surface, key)][2].set_sensitive(True)
        if key == "background":
            self._refresh_automatic_foreground(surface)

    def _on_chrome_colour_reset(self, _button, surface: str, key: str) -> None:
        self._queue_chrome(surface, key, _DELETE)
        picker, handler, reset = self._colour_controls[(surface, key)]
        value = self._adaptive_colour(surface, key)
        if key == "foreground":
            self._auto_rows[surface].set_active(True)
            value = _automatic_foreground(self._current_background(surface))
        picker.handler_block(handler)
        rgba = Gdk.RGBA()
        rgba.parse(value)
        picker.set_rgba(rgba)
        picker.handler_unblock(handler)
        reset.set_sensitive(False)
        if key == "background":
            self._refresh_automatic_foreground(surface)

    def _on_automatic_contrast(self, row, _pspec, surface: str) -> None:
        if self._syncing_chrome:
            return
        automatic = row.get_active()
        picker = self._colour_controls[(surface, "foreground")][0]
        picker.set_sensitive(not automatic)
        self._queue_chrome(surface, "foreground_mode", "auto" if automatic else "custom")
        if automatic:
            self._refresh_automatic_foreground(surface)
        elif "foreground" not in self._config.chrome.get(surface, {}):
            self._queue_chrome(surface, "foreground", _hex_from_rgba(picker.get_rgba()))
            self._colour_controls[(surface, "foreground")][2].set_sensitive(True)

    def _current_background(self, surface: str) -> str:
        picker = self._colour_controls[(surface, "background")][0]
        return _hex_from_rgba(picker.get_rgba())

    def _refresh_automatic_foreground(self, surface: str) -> None:
        if not self._auto_rows[surface].get_active():
            return
        picker, handler, _reset = self._colour_controls[(surface, "foreground")]
        rgba = Gdk.RGBA()
        rgba.parse(_automatic_foreground(self._current_background(surface)))
        picker.handler_block(handler)
        picker.set_rgba(rgba)
        picker.handler_unblock(handler)

    def _on_system_theme_changed(self, _manager, _pspec) -> None:
        for surface in ("top_bar", "dock"):
            if (surface, "background") not in self._colour_controls:
                continue
            if "background" not in self._config.chrome.get(surface, {}):
                picker, handler, _reset = self._colour_controls[(surface, "background")]
                rgba = Gdk.RGBA()
                rgba.parse(self._adaptive_colour(surface, "background"))
                picker.handler_block(handler)
                picker.set_rgba(rgba)
                picker.handler_unblock(handler)
            self._refresh_automatic_foreground(surface)

    # -- desktop icons ---------------------------------------------------

    def _on_desktop_icon_size(self, row, _pspec) -> None:
        if self._syncing_chrome:
            return
        try:
            self._desktop_icons.set_icon_size(ICON_SIZES[row.get_selected()])
        except (ValueError, RuntimeError, PermissionError, OSError, GLib.Error) as exc:
            self._toast(f"Could not update desktop icon size: {exc}")
            self._reload_icon_controls(False)

    def _on_desktop_icon_theme(self, row, _pspec) -> None:
        if self._syncing_chrome:
            return
        selected = row.get_selected()
        theme = self._icon_themes[selected] if selected < len(self._icon_themes) else None
        try:
            self._desktop_icons.set_icon_theme(theme.identifier if theme else None)
        except (RuntimeError, PermissionError, OSError, GLib.Error) as exc:
            self._toast(f"Could not update the icon pack: {exc}")
            self._reload_icon_controls(False)

    def _on_icon_material(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._queue_icon("material", ICON_MATERIAL_VALUES[row.get_selected()])
            self._refresh_icon_sensitivity()

    def _on_icon_tint(self, button, _pspec) -> None:
        if self._syncing_chrome:
            return
        self._queue_icon("tint", _hex_from_rgba(button.get_rgba()))
        self._icon_tint_reset.set_sensitive(True)

    def _on_icon_tint_reset(self, _button) -> None:
        self._queue_icon("tint", _DELETE)
        self._set_icon_picker(
            self._icon_tint, self._icon_tint_handler,
            config.DEFAULT_DESKTOP_ICONS["tint"],
        )
        self._icon_tint_reset.set_sensitive(False)

    def _on_icon_opacity(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._queue_icon("opacity", float(row.get_value()))

    def _on_icon_artwork(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._queue_icon("artwork", ICON_ARTWORK_VALUES[row.get_selected()])

    def _on_icon_auto_foreground(self, row, _pspec) -> None:
        if self._syncing_chrome:
            return
        automatic = row.get_active()
        self._icon_foreground.set_sensitive(not automatic)
        self._queue_icon("foreground_mode", "auto" if automatic else "custom")
        if not automatic and "foreground" not in self._config.desktop_icons:
            self._queue_icon("foreground", _hex_from_rgba(self._icon_foreground.get_rgba()))

    def _on_icon_foreground(self, button, _pspec) -> None:
        if not self._syncing_chrome:
            self._queue_icon("foreground", _hex_from_rgba(button.get_rgba()))

    def _on_native_icons_changed(self, _settings, _key) -> None:
        if not self._syncing_chrome:
            self._reload_icon_controls(False)

    @staticmethod
    def _set_icon_picker(picker, handler, color: str) -> None:
        rgba = Gdk.RGBA()
        rgba.parse(color)
        picker.handler_block(handler)
        picker.set_rgba(rgba)
        picker.handler_unblock(handler)

    def _refresh_icon_sensitivity(self) -> None:
        available = self._desktop_icons.ding_available
        material = ICON_MATERIAL_VALUES[self._icon_material.get_selected()]
        for control in (self._icon_tint_row, self._icon_opacity):
            control.set_sensitive(available and material != "system")
        for control in (self._icon_artwork, self._icon_auto_foreground,
                        self._icon_foreground_row):
            control.set_sensitive(available)
        self._icon_foreground.set_sensitive(
            available and not self._icon_auto_foreground.get_active()
        )

    def _queue_icon(self, key: str, value) -> None:
        if value is _DELETE:
            self._config.desktop_icons.pop(key, None)
        else:
            self._config.desktop_icons[key] = value
        self._icon_pending[key] = value
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._flush_chrome)

    def _queue_chrome(self, surface: str, key: str, value) -> None:
        overrides = self._config.chrome.setdefault(surface, {})
        if value is _DELETE:
            overrides.pop(key, None)
        else:
            overrides[key] = value
        self._chrome_pending[(surface, key)] = value
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._flush_chrome)

    def _flush_chrome(self) -> bool:
        self._save_source = 0
        latest = config.load()
        for (surface, key), value in self._chrome_pending.items():
            overrides = latest.chrome.setdefault(surface, {})
            if value is _DELETE:
                overrides.pop(key, None)
            else:
                overrides[key] = value
            if not overrides:
                latest.chrome.pop(surface, None)
        for key, value in self._icon_pending.items():
            if value is _DELETE:
                latest.desktop_icons.pop(key, None)
            else:
                latest.desktop_icons[key] = value
        try:
            config.save(latest)
        except OSError as exc:
            self._toast(f"Could not save desktop appearance: {exc}")
        else:
            self._config = latest
            self._apply_icon_style(latest.desktop_icon_options())
        self._chrome_pending.clear()
        self._icon_pending.clear()
        return GLib.SOURCE_REMOVE

    def _apply_icon_style(self, options: dict) -> None:
        def work():
            try:
                changed = sync_stylesheet(options)
                restarted = not changed or restart_ding()
            except (OSError, ValueError) as exc:
                GLib.idle_add(self._toast, f"Could not apply desktop icon style: {exc}")
                return
            if changed and not restarted:
                GLib.idle_add(
                    self._toast,
                    "Desktop icon style saved; log out and back in to finish applying it",
                )

        threading.Thread(target=work, daemon=True, name="desktop-icon-style").start()

    def _on_dock_visibility(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._dock_change(lambda: self._dock.set_visibility(
                VISIBILITY_VALUES[row.get_selected()]
            ))

    def _on_dock_position(self, row, _pspec) -> None:
        if self._syncing_chrome:
            return
        requested = DOCK_POSITION_VALUES[row.get_selected()]
        top = TOP_POSITION_VALUES[self._top_position.get_selected()].upper()
        if requested == top:
            self._refresh_dock_controls()
            self._toast("Move the top bar first — both surfaces cannot use the same edge")
            return
        self._dock_change(lambda: self._dock.set_position(requested))

    def _on_dock_icon_size(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._dock_change(lambda: self._dock.set_icon_size(round(row.get_value())))

    def _on_dock_length(self, row, _pspec) -> None:
        if not self._syncing_chrome:
            self._dock_change(lambda: self._dock.set_maximum_length(row.get_value() / 100))

    def _dock_change(self, operation) -> None:
        try:
            operation()
        except (ValueError, PermissionError, OSError, GLib.Error) as exc:
            self._toast(f"Could not update the dock: {exc}")
            self._refresh_dock_controls()
        self._refresh_conflict_warning()

    def _on_dock_settings_changed(self, _settings, _key) -> None:
        if not self._syncing_chrome:
            self._refresh_dock_controls()

    def _refresh_dock_controls(self) -> None:
        if not self._dock or not hasattr(self, "_dock_visibility"):
            return
        state = self._dock.read()
        self._syncing_chrome = True
        self._dock_visibility.set_selected(self._index(
            VISIBILITY_VALUES, state.visibility, 0
        ))
        self._dock_position.set_selected(self._index(
            DOCK_POSITION_VALUES, state.position, 2
        ))
        self._dock_icon_size.set_value(state.icon_size)
        self._dock_length.set_value(round(state.maximum_length * 100))
        self._syncing_chrome = False
        self._refresh_conflict_warning()

    def _refresh_conflict_warning(self) -> None:
        conflict = False
        if self._dock:
            top = TOP_POSITION_VALUES[self._top_position.get_selected()].upper()
            conflict = self._dock.read().position == top
        self._dock_warning.set_visible(conflict)

    def reload(self) -> None:
        self._config = config.load()
        self._reload_chrome_controls()
        self._reload_icon_controls()
        self._reload_folders()

    def _reload_chrome_controls(self) -> None:
        options = self._config.chrome_options("top_bar")
        self._syncing_chrome = True
        self._top_visibility.set_selected(self._index(
            VISIBILITY_VALUES, options.get("visibility"), 0
        ))
        self._top_position.set_selected(self._index(
            TOP_POSITION_VALUES, options.get("position"), 0
        ))
        self._top_height.set_value(max(24, min(64, options.get("height", 32))))
        for surface in ("top_bar", "dock"):
            if (surface, "background") not in self._colour_controls:
                continue
            surface_options = self._config.chrome_options(surface)
            opacity = getattr(self, f"_{surface}_opacity")
            opacity.set_value(max(0, min(1, surface_options.get(
                "opacity", config.DEFAULT_CHROME[surface]["opacity"]
            ))))
            automatic = surface_options.get("foreground_mode", "auto") == "auto"
            self._auto_rows[surface].set_active(automatic)
            for key in ("background", "foreground"):
                picker, handler, reset = self._colour_controls[(surface, key)]
                value = surface_options.get(key) or self._adaptive_colour(surface, key)
                if key == "foreground" and automatic:
                    background = surface_options.get("background") or self._adaptive_colour(
                        surface, "background"
                    )
                    value = _automatic_foreground(background)
                rgba = Gdk.RGBA()
                if not rgba.parse(value):
                    rgba.parse(self._adaptive_colour(surface, key))
                picker.handler_block(handler)
                picker.set_rgba(rgba)
                picker.handler_unblock(handler)
                reset.set_sensitive(key in self._config.chrome.get(surface, {}))
            self._colour_controls[(surface, "foreground")][0].set_sensitive(not automatic)
        self._syncing_chrome = False
        self._refresh_dock_controls()
        self._refresh_conflict_warning()

    def _reload_icon_controls(self, apply_style: bool = True) -> None:
        options = self._config.desktop_icon_options()
        self._syncing_chrome = True
        self._desktop_icon_size.set_selected(self._index(
            list(ICON_SIZES), self._desktop_icons.icon_size(), 2
        ))
        current_theme = self._desktop_icons.icon_theme()
        default_theme = self._desktop_icons.default_icon_theme()
        selected = 0
        if current_theme != default_theme:
            for index, theme in enumerate(self._icon_themes[1:], 1):
                if theme.identifier == current_theme:
                    selected = index
                    break
        self._icon_theme.set_selected(selected)
        self._icon_material.set_selected(self._index(
            ICON_MATERIAL_VALUES, options["material"], 0
        ))
        self._icon_opacity.set_value(options["opacity"])
        self._icon_artwork.set_selected(self._index(
            ICON_ARTWORK_VALUES, options["artwork"], 0
        ))
        automatic = options["foreground_mode"] == "auto"
        self._icon_auto_foreground.set_active(automatic)
        self._set_icon_picker(self._icon_tint, self._icon_tint_handler, options["tint"])
        self._set_icon_picker(
            self._icon_foreground, self._icon_foreground_handler, options["foreground"]
        )
        self._icon_tint_reset.set_sensitive("tint" in self._config.desktop_icons)
        self._syncing_chrome = False
        self._refresh_icon_sensitivity()
        if apply_style and self._desktop_icons.ding_available:
            self._apply_icon_style(options)

    def _reload_folders(self) -> None:
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
