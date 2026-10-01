"""Keyboard shortcuts: Desktop Forge's own, a chosen set of GNOME's, and
custom commands, with conflict detection across all of them.

Shortcuts stay in GSettings where GNOME and Shell read them, so a change
applies at once. Desktop Forge's own live in the extension's schema, which
is found next to the installed extension.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

EXTENSION_UUID = "desktop-forge@jrf.local"
EXTENSION = "org.gnome.shell.extensions.desktop-forge"
WM = "org.gnome.desktop.wm.keybindings"
SHELL = "org.gnome.shell.keybindings"
MUTTER = "org.gnome.mutter.keybindings"
MUTTER_WAYLAND = "org.gnome.mutter.wayland.keybindings"
MEDIA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM = f"{MEDIA}.custom-keybinding"
CUSTOM_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
# Every schema whose keys can collide with a new shortcut.
SCANNED = (EXTENSION, WM, SHELL, MUTTER, MUTTER_WAYLAND, MEDIA)


@dataclass(frozen=True)
class Shortcut:
    schema: str
    key: str
    label: str


def _group(schema, *pairs):
    return [Shortcut(schema, key, label) for key, label in pairs]


GROUPS = (
    ("Desktop Forge", "Tiling, snapping, the scratchpad and the top bar. None is set until you choose one.", _group(
        EXTENSION,
        ("toggle-tiling", "Turn tiling on or off"), ("next-layout", "Next tiling layout"),
        ("toggle-floating", "Float or tile the focused window"),
        ("focus-left", "Focus the window to the left"), ("focus-right", "Focus the window to the right"),
        ("focus-up", "Focus the window above"), ("focus-down", "Focus the window below"),
        ("swap-left", "Swap with the window to the left"), ("swap-right", "Swap with the window to the right"),
        ("swap-up", "Swap with the window above"), ("swap-down", "Swap with the window below"),
        ("grow-main", "Make the main area larger"), ("shrink-main", "Make the main area smaller"),
        ("snap-left", "Snap to the left half"), ("snap-right", "Snap to the right half"),
        ("snap-top-left", "Snap to the top-left quarter"), ("snap-top-right", "Snap to the top-right quarter"),
        ("snap-bottom-left", "Snap to the bottom-left quarter"),
        ("snap-bottom-right", "Snap to the bottom-right quarter"),
        ("center-window", "Center the focused window"), ("toggle-top-bar", "Show or hide the top bar"),
        ("scratchpad-toggle", "Show or hide the scratchpad"),
        ("scratchpad-send", "Move the focused window to or from the scratchpad"),
        ("open-clive", "Open CLIVE"))),
    ("Windows", "", [
        *_group(WM, ("close", "Close window"), ("toggle-maximized", "Maximize or restore"),
                ("minimize", "Minimize"), ("toggle-fullscreen", "Fullscreen"),
                ("toggle-above", "Always on top"), ("toggle-on-all-workspaces", "On every workspace"),
                ("begin-move", "Move with the keyboard"), ("begin-resize", "Resize with the keyboard"),
                ("switch-applications", "Switch apps"), ("switch-windows", "Switch windows"),
                ("show-desktop", "Show the desktop")),
        *_group(MUTTER, ("toggle-tiled-left", "Fill the left half"), ("toggle-tiled-right", "Fill the right half")),
    ]),
    ("Workspaces & Displays", "", _group(
        WM, *((f"switch-to-workspace-{n}", f"Go to workspace {n}") for n in range(1, 5)),
        *((f"move-to-workspace-{n}", f"Move window to workspace {n}") for n in range(1, 5)),
        ("switch-to-workspace-left", "Workspace to the left"), ("switch-to-workspace-right", "Workspace to the right"),
        ("move-to-workspace-left", "Move window a workspace left"),
        ("move-to-workspace-right", "Move window a workspace right"),
        ("move-to-monitor-left", "Move window to the display on the left"),
        ("move-to-monitor-right", "Move window to the display on the right"))),
    ("GNOME Shell", "", [
        *_group(SHELL, ("toggle-overview", "Activities overview"), ("toggle-application-view", "App grid"),
                ("toggle-message-tray", "Notifications"), ("toggle-quick-settings", "Quick settings"),
                ("show-screenshot-ui", "Take a screenshot"),
                ("focus-active-notification", "Focus the current notification")),
        *_group(MEDIA, ("screensaver", "Lock the screen")),
    ]),
)

# Keys that need no modifier (Super, Ctrl, Alt): function keys and the like.
_BARE_OK = re.compile(r"^(F\d{1,2}|Print|Pause|Scroll_Lock|XF86\w+|Menu)$")


def extension_schema_dir() -> str | None:
    """The installed extension's compiled schemas, or the source tree's."""
    for base in (GLib.get_user_data_dir(), *GLib.get_system_data_dirs()):
        folder = os.path.join(base, "gnome-shell", "extensions", EXTENSION_UUID, "schemas")
        if os.path.exists(os.path.join(folder, "gschemas.compiled")):
            return folder
    source = Path(__file__).resolve().parents[2] / "extension" / "schemas"
    return str(source) if (source / "gschemas.compiled").exists() else None


def pretty(key: str) -> str:
    return key.replace("-", " ").capitalize()


def normalize(accelerator: str):
    """(keyval, modifiers) for comparing shortcuts written differently."""
    ok, keyval, modifiers = Gtk.accelerator_parse(accelerator)
    if not ok or not keyval:
        return None
    from gi.repository import Gdk
    return Gdk.keyval_to_lower(keyval), int(modifiers)


def acceptable(accelerator: str) -> str:
    """Why an accelerator cannot be a shortcut, or "" when it can."""
    parsed = normalize(accelerator) if accelerator else None
    if parsed is None:
        return "That is not a key combination"
    keyval, modifiers = parsed
    from gi.repository import Gdk
    name = Gdk.keyval_name(keyval) or ""
    if not modifiers & int(Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.CONTROL_MASK |
                           Gdk.ModifierType.ALT_MASK) and not _BARE_OK.match(name):
        return "Include Super, Ctrl or Alt, so typing that key still works in apps"
    return ""


class ShortcutStore:
    def __init__(self, source=None, extension_dir: str | None = None, backend=None):
        self._source = source or Gio.SettingsSchemaSource.get_default()
        folder = extension_dir if extension_dir is not None else extension_schema_dir()
        self._extension_source = None
        if folder:
            try:
                self._extension_source = Gio.SettingsSchemaSource.new_from_directory(folder, self._source, False)
            except GLib.Error:
                self._extension_source = None
        self._backend = backend
        self._settings: dict[tuple, Gio.Settings | None] = {}

    def _schema(self, schema_id: str):
        source = self._extension_source if schema_id == EXTENSION else self._source
        return source.lookup(schema_id, True) if source else None

    def settings(self, schema_id: str, path: str | None = None) -> Gio.Settings | None:
        key = (schema_id, path)
        if key not in self._settings:
            schema = self._schema(schema_id)
            self._settings[key] = None if schema is None else Gio.Settings.new_full(schema, self._backend, path)
        return self._settings[key]

    @property
    def extension_available(self) -> bool:
        return self._schema(EXTENSION) is not None

    def available(self, shortcut: Shortcut) -> bool:
        schema = self._schema(shortcut.schema)
        return schema is not None and schema.has_key(shortcut.key)

    def get(self, shortcut: Shortcut) -> list[str]:
        if not self.available(shortcut):
            return []
        value = self.settings(shortcut.schema).get_value(shortcut.key).unpack()
        return [value] if isinstance(value, str) and value else list(value or [])

    def set(self, shortcut: Shortcut, accelerators: list[str]) -> None:
        settings = self.settings(shortcut.schema)
        if settings is None or not self.available(shortcut):
            raise ValueError(f"{shortcut.label} is not available here")
        if not settings.is_writable(shortcut.key):
            raise PermissionError(f"{shortcut.label} is locked by your administrator")
        settings.set_strv(shortcut.key, [a for a in accelerators if a])

    def reset(self, shortcut: Shortcut) -> None:
        if self.available(shortcut):
            self.settings(shortcut.schema).reset(shortcut.key)

    def is_default(self, shortcut: Shortcut) -> bool:
        if not self.available(shortcut):
            return True
        key = self._schema(shortcut.schema).get_key(shortcut.key)
        return self.settings(shortcut.schema).get_value(shortcut.key).equal(key.get_default_value())

    # -- custom commands ---------------------------------------------------------

    def custom(self) -> list[dict]:
        media = self.settings(MEDIA)
        if media is None or self._schema(CUSTOM) is None:
            return []
        result = []
        for path in media.get_strv("custom-keybindings"):
            one = self.settings(CUSTOM, path)
            result.append({"path": path, "name": one.get_string("name"), "command": one.get_string("command"),
                           "binding": one.get_string("binding")})
        return result

    def save_custom(self, name: str, command: str, binding: str, path: str | None = None) -> str:
        media = self.settings(MEDIA)
        if media is None or self._schema(CUSTOM) is None:
            raise ValueError("Custom shortcuts need GNOME Settings Daemon")
        if not name.strip() or not command.strip():
            raise ValueError("Give the shortcut a name and a command")
        paths = media.get_strv("custom-keybindings")
        if path is None:
            used = set(paths)
            number = 0
            while f"{CUSTOM_PATH}custom{number}/" in used:
                number += 1
            path = f"{CUSTOM_PATH}custom{number}/"
        one = self.settings(CUSTOM, path)
        one.set_string("name", name.strip())
        one.set_string("command", command.strip())
        one.set_string("binding", binding)
        if path not in paths:
            media.set_strv("custom-keybindings", paths + [path])
        return path

    def remove_custom(self, path: str) -> None:
        media = self.settings(MEDIA)
        if media is None:
            return
        media.set_strv("custom-keybindings", [p for p in media.get_strv("custom-keybindings") if p != path])
        one = self.settings(CUSTOM, path)
        for key in ("name", "command", "binding"):
            one.reset(key)

    # -- conflicts ----------------------------------------------------------------

    def _labels(self) -> dict:
        return {(s.schema, s.key): s.label for _title, _about, items in GROUPS for s in items}

    def conflicts(self, accelerator: str, exclude: tuple | None = None) -> list[dict]:
        """Everything already using this combination, as {label, schema, key, path}."""
        wanted = normalize(accelerator)
        if wanted is None:
            return []
        labels = self._labels()
        found = []
        for schema_id in SCANNED:
            schema = self._schema(schema_id)
            settings = self.settings(schema_id)
            if schema is None or settings is None:
                continue
            for key in schema.list_keys():
                if schema.get_key(key).get_value_type().dup_string() != "as" or (schema_id, key) == exclude:
                    continue
                if any(normalize(value) == wanted for value in settings.get_strv(key) if value):
                    found.append({"label": labels.get((schema_id, key), pretty(key)), "schema": schema_id,
                                  "key": key, "path": None})
        for entry in self.custom():
            if (CUSTOM, entry["path"]) == exclude:
                continue
            if entry["binding"] and normalize(entry["binding"]) == wanted:
                found.append({"label": entry["name"] or "Custom shortcut", "schema": CUSTOM, "key": "binding",
                              "path": entry["path"]})
        return found

    def release(self, conflict: dict, accelerator: str) -> None:
        """Take a combination away from whatever holds it."""
        wanted = normalize(accelerator)
        if conflict["path"]:
            self.settings(CUSTOM, conflict["path"]).set_string("binding", "")
            return
        settings = self.settings(conflict["schema"])
        settings.set_strv(conflict["key"], [v for v in settings.get_strv(conflict["key"]) if normalize(v) != wanted])
