"""Native DING settings and reversible Desktop Forge icon styling."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk


DING_UUID = "ding@rastersoft.com"
DING_SCHEMA = "org.gnome.shell.extensions.ding"
ICON_SIZES = ("tiny", "small", "standard", "large")
START_MARKER = "/* Desktop Forge desktop-icon styles: begin */"
END_MARKER = "/* Desktop Forge desktop-icon styles: end */"
GTK_CSS_PATH = Path(GLib.get_user_config_dir()) / "gtk-4.0" / "gtk.css"
GENERATED_CSS_PATH = Path(GLib.get_user_config_dir()) / "desktop-forge" / "desktop-icons.css"


@dataclass(frozen=True)
class IconTheme:
    identifier: str
    name: str


class DesktopIcons:
    """A typed bridge to DING and GNOME's system icon-theme setting."""

    def __init__(self, ding: Gio.Settings | None, interface: Gio.Settings | None):
        self.ding = ding
        self.interface = interface

    @classmethod
    def find(cls) -> "DesktopIcons":
        ding = None
        parent = Gio.SettingsSchemaSource.get_default()
        roots = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
        for root in roots:
            schemas = Path(root) / "gnome-shell" / "extensions" / DING_UUID / "schemas"
            if not (schemas / "gschemas.compiled").is_file():
                continue
            try:
                source = Gio.SettingsSchemaSource.new_from_directory(
                    str(schemas), parent, False
                )
                schema = source.lookup(DING_SCHEMA, False)
                if schema is not None:
                    ding = Gio.Settings.new_full(schema, None, None)
                    break
            except GLib.Error:
                continue

        interface = None
        if parent and parent.lookup("org.gnome.desktop.interface", True):
            interface = Gio.Settings.new("org.gnome.desktop.interface")
        return cls(ding, interface)

    @property
    def ding_available(self) -> bool:
        return self.ding is not None

    def icon_size(self) -> str:
        if self.ding is None:
            return "standard"
        value = self.ding.get_string("icon-size")
        return value if value in ICON_SIZES else "standard"

    def set_icon_size(self, value: str) -> None:
        if self.ding is None:
            raise RuntimeError("Desktop Icons NG is unavailable")
        if value not in ICON_SIZES:
            raise ValueError(f"Unknown desktop icon size: {value}")
        if not self.ding.is_writable("icon-size"):
            raise PermissionError("Desktop icon size is locked")
        if not self.ding.set_string("icon-size", value):
            raise OSError("Desktop Icons NG rejected the icon size")
        self.ding.apply()

    def icon_theme(self) -> str:
        return self.interface.get_string("icon-theme") if self.interface else ""

    def default_icon_theme(self) -> str:
        if not self.interface:
            return "Adwaita"
        value = self.interface.get_default_value("icon-theme")
        return value.unpack() if value else "Adwaita"

    def set_icon_theme(self, value: str | None) -> None:
        if self.interface is None:
            raise RuntimeError("GNOME icon-theme settings are unavailable")
        if not self.interface.is_writable("icon-theme"):
            raise PermissionError("The system icon theme is locked")
        if value is None:
            self.interface.reset("icon-theme")
        elif not self.interface.set_string("icon-theme", value):
            raise OSError("GNOME rejected the icon theme")
        self.interface.apply()

    @staticmethod
    def themes() -> list[IconTheme]:
        """List real XDG icon themes, with localized display names."""
        found: dict[str, IconTheme] = {}
        for root in [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]:
            icons = Path(root) / "icons"
            try:
                children = list(icons.iterdir())
            except OSError:
                continue
            for directory in children:
                index = directory / "index.theme"
                if not index.is_file() or directory.name in found:
                    continue
                keyfile = GLib.KeyFile()
                try:
                    keyfile.load_from_file(str(index), GLib.KeyFileFlags.NONE)
                    if keyfile.get_boolean("Icon Theme", "Hidden"):
                        continue
                except GLib.Error:
                    # Hidden is optional. Retry only the required Name key.
                    try:
                        keyfile.load_from_file(str(index), GLib.KeyFileFlags.NONE)
                    except GLib.Error:
                        continue
                try:
                    name = keyfile.get_locale_string("Icon Theme", "Name", None)
                except GLib.Error:
                    name = directory.name
                found[directory.name] = IconTheme(directory.name, name or directory.name)
        return sorted(found.values(), key=lambda item: item.name.casefold())


def _rgba(color: str, opacity: float) -> str:
    channels = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    return f"rgba({channels[0]},{channels[1]},{channels[2]},{opacity:.3f})"


def render_stylesheet(options: dict) -> str:
    """Render CSS scoped to DING's desktop window, never to other GTK apps."""
    material = options["material"]
    tint = options["tint"]
    opacity = options["opacity"]
    foreground = options["foreground"] if options["foreground_mode"] == "custom" \
        else "@window_fg_color"
    artwork = "grayscale(1)" if options["artwork"] == "monochrome" else "none"

    material_css = ""
    if material == "solid":
        material_css = f"""
  background-color: {_rgba(tint, max(0.08, opacity))};
  border: 1px solid {_rgba(tint, min(1, opacity + 0.30))};
  box-shadow: 0 3px 10px rgba(0,0,0,0.20);"""
    elif material == "frosted":
        material_css = f"""
  background-color: {_rgba(tint, max(0.08, opacity * 0.72))};
  background-image: linear-gradient(to bottom right,
      rgba(255,255,255,{min(0.64, opacity + 0.20):.3f}),
      {_rgba(tint, opacity * 0.45)} 55%, rgba(255,255,255,0.06));
  border: 1px solid rgba(255,255,255,{min(0.70, opacity + 0.24):.3f});
  box-shadow: inset 0 1px rgba(255,255,255,0.34), 0 5px 14px rgba(0,0,0,0.22);"""
    elif material == "liquid":
        material_css = f"""
  background-color: {_rgba(tint, max(0.06, opacity * 0.55))};
  background-image: linear-gradient(145deg,
      rgba(255,255,255,{min(0.78, opacity + 0.34):.3f}) 0%,
      {_rgba(tint, opacity * 0.34)} 42%,
      rgba(255,255,255,0.05) 68%, {_rgba(tint, opacity * 0.72)} 100%);
  border: 1px solid rgba(255,255,255,{min(0.78, opacity + 0.30):.3f});
  box-shadow: inset 0 1px rgba(255,255,255,0.52),
              inset 0 -1px {_rgba(tint, min(0.70, opacity + 0.18))},
              0 6px 16px rgba(0,0,0,0.24);"""

    tile = ""
    if material_css:
        tile = f"""
window.desktopwindow picture.icon-item {{
  padding: 7px;
  border-radius: 18px;
  transition: 160ms ease-out;{material_css}
  -gtk-icon-filter: {artwork};
}}
window.desktopwindow box.file-item:hover picture.icon-item,
window.desktopwindow box.file-item-hover picture.icon-item {{
  -gtk-icon-transform: scale(1.035);
}}
window.desktopwindow box.file-item.desktop-icons-selected picture.icon-item {{
  border-color: alpha(@accent_bg_color, 0.90);
}}
"""
    elif artwork != "none":
        tile = f"""
window.desktopwindow picture.icon-item {{ -gtk-icon-filter: {artwork}; }}
"""

    label = ""
    if options["foreground_mode"] == "custom":
        label = f"""
window.desktopwindow label.file-label,
window.desktopwindow label.file-label-dark {{
  color: {foreground};
  text-shadow: 0 1px 3px rgba(0,0,0,0.82);
}}
"""
    return "/* Generated by Desktop Forge. */\n" + tile + label


def validate_stylesheet(css: str) -> None:
    provider = Gtk.CssProvider()
    errors: list[str] = []

    def parsing_error(_provider, _section, error) -> None:
        errors.append(error.message)

    provider.connect("parsing-error", parsing_error)
    provider.load_from_string(css)
    if errors:
        raise ValueError("Generated desktop icon style is invalid: " + "; ".join(errors))


def _without_managed_block(contents: str) -> str:
    pattern = re.compile(
        rf"(?:\n?){re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}(?:\n?)",
        re.DOTALL,
    )
    return pattern.sub("\n", contents).lstrip("\n")


def _atomic_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def sync_stylesheet(options: dict) -> bool:
    """Install/remove the scoped user stylesheet; return whether DING must reload."""
    css = render_stylesheet(options)
    active = bool(css.split("\n", 1)[1].strip())
    try:
        original = GTK_CSS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        original = ""
    cleaned = _without_managed_block(original)

    generated_changed = False
    if active:
        validate_stylesheet(css)
        try:
            generated_changed = GENERATED_CSS_PATH.read_text(encoding="utf-8") != css
        except FileNotFoundError:
            generated_changed = True
        if generated_changed:
            _atomic_text(GENERATED_CSS_PATH, css)
        uri = GENERATED_CSS_PATH.as_uri().replace('"', '%22')
        block = f'{START_MARKER}\n@import url("{uri}");\n{END_MARKER}\n'
        updated = block + cleaned
    else:
        updated = cleaned

    changed = updated != original
    if changed:
        if updated:
            _atomic_text(GTK_CSS_PATH, updated)
        elif GTK_CSS_PATH.exists():
            GTK_CSS_PATH.unlink()
    if not active and GENERATED_CSS_PATH.exists():
        GENERATED_CSS_PATH.unlink()
    return changed or generated_changed


def restart_ding() -> bool:
    """Reload DING after GTK CSS changes without touching icon positions."""
    executable = shutil.which("gnome-extensions")
    if not executable:
        return False
    enabled = subprocess.run(
        [executable, "list", "--enabled"], capture_output=True, text=True,
        check=False, timeout=10,
    )
    if enabled.returncode != 0:
        return False
    # A disabled extension will read the new stylesheet when the user next
    # enables it. Do not unexpectedly change that user's extension setting.
    if DING_UUID not in enabled.stdout.splitlines():
        return True
    disabled = subprocess.run(
        [executable, "disable", DING_UUID], capture_output=True, check=False, timeout=10
    )
    if disabled.returncode != 0:
        return False
    enabled = subprocess.run(
        [executable, "enable", DING_UUID], capture_output=True, check=False, timeout=10
    )
    return enabled.returncode == 0
