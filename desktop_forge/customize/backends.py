"""Where each customization setting is read from and written to.

Every backend answers the same four questions for a Setting: is it available
here, can it be changed, what is it now, and change it. The Customizer in
__init__ routes each setting to its backend, so nothing above this module
cares whether a value lives in GSettings or a JSON file.
"""
from __future__ import annotations

import copy
import os
import re
import tempfile
import threading
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from .. import config
from ..backend.shell_chrome import DashToDock
from .registry import SETTINGS, Setting, validate

DESKTOP_PATH = os.path.join(config.CONFIG_DIR, "desktop.json")
FORMAT_VERSION = 1


class Unavailable(Exception):
    """The setting cannot be used on this system; the message says why."""


def split_id(setting_id: str) -> tuple[str, str]:
    group, _, key = setting_id.partition(".")
    return group, key


class DesktopStore:
    """desktop.json: settings the Shell extension applies itself.

    The file is always written complete -- every desktop and gtkcss setting,
    defaults included, grouped by the first part of the ID -- so the
    extension reads values and never keeps defaults of its own. A value that
    fails validation (a hand edit, an older version) reads as its default.
    """

    backends = ("desktop", "gtkcss")

    def __init__(self, path: str = DESKTOP_PATH, settings=SETTINGS):
        self.path = path
        self.settings = [s for s in settings if s.backend in self.backends]
        self._lock = threading.RLock()

    def _raw(self) -> dict:
        data = config.read_json(self.path)
        return data if isinstance(data, dict) else {}

    def read(self) -> dict:
        raw = self._raw()
        values = {}
        for setting in self.settings:
            group, key = split_id(setting.id)
            section = raw.get(group)
            value = section.get(key) if isinstance(section, dict) else None
            try:
                values[setting.id] = validate(setting, value) if value is not None \
                    else copy.deepcopy(setting.default)
            except ValueError:
                values[setting.id] = copy.deepcopy(setting.default)
        return values

    def document(self, values: dict) -> dict:
        document: dict = {"version": FORMAT_VERSION}
        for setting in self.settings:
            group, key = split_id(setting.id)
            document.setdefault(group, {})[key] = values.get(setting.id, setting.default)
        return document

    def write(self, changes: dict) -> dict:
        """Merge changes into the file; returns the complete values written."""
        with self._lock:
            values = self.read()
            values.update(changes)
            document = self.document(values)
            if document != self._raw():
                config.write_json(self.path, document)
            return values

    def normalize(self) -> bool:
        """Rewrite the file if it is missing keys or holds invalid values.

        Called when the app starts, so settings added in a new version reach
        the extension without the user touching them.
        """
        with self._lock:
            document = self.document(self.read())
            if document == self._raw():
                return False
            config.write_json(self.path, document)
            return True


class GSettingsBackend:
    """GNOME settings, checked before use: GLib aborts the whole process on a
    key its schema does not have, so every key is looked up first."""

    def __init__(self, source=None, backend=None):
        self._source = source
        self._backend = backend
        self._settings: dict[str, Gio.Settings] = {}
        self._lock = threading.Lock()

    def _schema(self, schema_id: str):
        source = self._source or Gio.SettingsSchemaSource.get_default()
        return source.lookup(schema_id, True) if source else None

    def settings_for(self, schema_id: str) -> Gio.Settings | None:
        with self._lock:
            if schema_id not in self._settings:
                schema = self._schema(schema_id)
                self._settings[schema_id] = None if schema is None else \
                    Gio.Settings.new_full(schema, self._backend, None)
            return self._settings[schema_id]

    def check(self, setting: Setting) -> None:
        schema = self._schema(setting.schema)
        if schema is None:
            owner = "Dash to Dock" if "dash-to-dock" in setting.schema else setting.schema
            raise Unavailable(f"{owner} is not installed")
        if not schema.has_key(setting.key):
            raise Unavailable("This version of GNOME does not have this setting")

    def available(self, setting: Setting) -> bool:
        try:
            self.check(setting)
        except Unavailable:
            return False
        return True

    def writable(self, setting: Setting) -> bool:
        settings = self.settings_for(setting.schema)
        return bool(settings) and self.available(setting) and settings.is_writable(setting.key)

    def get(self, setting: Setting):
        self.check(setting)
        value = self.settings_for(setting.schema).get_value(setting.key).unpack()
        return _from_gsettings(setting, value)

    def default(self, setting: Setting):
        self.check(setting)
        key = self._schema(setting.schema).get_key(setting.key)
        return _from_gsettings(setting, key.get_default_value().unpack())

    def set(self, setting: Setting, value) -> None:
        self.check(setting)
        settings = self.settings_for(setting.schema)
        if not settings.is_writable(setting.key):
            raise PermissionError(f"{setting.label} is locked by your administrator")
        current = settings.get_value(setting.key)
        variant = GLib.Variant(current.get_type_string(), _to_gsettings(setting, value, current))
        if not self._schema(setting.schema).get_key(setting.key).range_check(variant):
            raise ValueError(f"GNOME does not allow that value for {setting.label}")
        if not settings.set_value(setting.key, variant):
            raise ValueError(f"GNOME did not accept that value for {setting.label}")

    def sync(self) -> None:
        Gio.Settings.sync()


def _from_gsettings(setting: Setting, value):
    if setting.kind == "float":
        return round(float(value), 4)
    if setting.kind == "color" and isinstance(value, str):
        return value.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else setting.default
    return value


def _to_gsettings(setting: Setting, value, current: GLib.Variant):
    if current.get_type_string() in ("u", "i") and isinstance(value, float):
        return int(round(value))
    return value


class DockBackend:
    """Dash to Dock's visibility, written the way the Dock rows write it.

    It is four GSettings keys (fixed, autohide, intellihide, manualhide)
    that only work in the combinations DashToDock.set_visibility writes;
    setting two of them from a profile could leave a dock that never shows.
    """

    VISIBILITY_KEYS = ("dock-fixed", "autohide", "intellihide", "manualhide")

    def __init__(self, find=DashToDock.find):
        self._find = find
        self._dock = None
        self._looked = False

    def dock(self) -> DashToDock | None:
        if not self._looked:
            self._dock, self._looked = self._find(), True
        return self._dock

    def check(self, _setting: Setting) -> None:
        if self.dock() is None:
            raise Unavailable("Dash to Dock is not installed")

    def available(self, setting: Setting) -> bool:
        return self.dock() is not None

    def writable(self, _setting: Setting) -> bool:
        dock = self.dock()
        return dock is not None and dock.writable(*self.VISIBILITY_KEYS)

    def get(self, setting: Setting):
        self.check(setting)
        return self.dock().read().visibility

    def set(self, setting: Setting, value) -> None:
        self.check(setting)
        self.dock().set_visibility(value)


class ChromeBackend:
    """The top bar and dock look already stored in config.json's "chrome".

    IDs read "chrome.<surface>.<key>". An empty colour means "follow the
    light/dark palette", which is stored as no override at all.
    """

    def __init__(self, load=config.load, save=config.save):
        self._load = load
        self._save = save
        self._lock = threading.Lock()

    @staticmethod
    def _parts(setting: Setting) -> tuple[str, str]:
        _chrome, surface, key = setting.id.split(".", 2)
        return surface, key

    def get(self, setting: Setting):
        surface, key = self._parts(setting)
        options = self._load().chrome_options(surface)
        return options.get(key, "" if setting.kind == "color" else setting.default)

    def write(self, changes: dict[Setting, object]) -> None:
        with self._lock:
            current = self._load()
            for setting, value in changes.items():
                surface, key = self._parts(setting)
                overrides = current.chrome.setdefault(surface, {})
                if setting.kind == "color" and not value:
                    overrides.pop(key, None)
                else:
                    overrides[key] = value
                if not overrides:
                    current.chrome.pop(surface, None)
            self._save(current)


def _atomic_text(path: Path, contents: str) -> None:
    """Replace a text file in one step (this module is also used by the
    background service, so it avoids desktop_icons and its GTK import)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


START_MARKER = "/* Desktop Forge window styles: begin */"
END_MARKER = "/* Desktop Forge window styles: end */"
GTK_CSS_PATHS = tuple(Path(GLib.get_user_config_dir()) / version / "gtk.css"
                      for version in ("gtk-3.0", "gtk-4.0"))

# GTK 3 draws the shadow on "decoration"; GTK 4 on the window itself. The
# 1px ring keeps a window's edge visible when its shadow is gone.
SHADOWS = {
    "none": ("0 0 0 1px rgba(0,0,0,0.18)", "0 0 0 1px rgba(0,0,0,0.12)"),
    "subtle": ("0 2px 6px rgba(0,0,0,0.20), 0 0 0 1px rgba(0,0,0,0.14)",
               "0 1px 4px rgba(0,0,0,0.14), 0 0 0 1px rgba(0,0,0,0.10)"),
    "strong": ("0 8px 28px 2px rgba(0,0,0,0.55), 0 0 0 1px rgba(0,0,0,0.25)",
               "0 4px 16px 1px rgba(0,0,0,0.35), 0 0 0 1px rgba(0,0,0,0.18)"),
}


def render_window_css(values: dict, gtk_version: str) -> str:
    shadow = values.get("windows.shadow", "default")
    if shadow not in SHADOWS:
        return ""
    focused, backdrop = SHADOWS[shadow]
    selector = "decoration" if gtk_version == "gtk-3.0" else "window.csd"
    backdrop_selector = "decoration:backdrop" if gtk_version == "gtk-3.0" else "window.csd:backdrop"
    return (f"{selector} {{ box-shadow: {focused}; }}\n"
            f"{backdrop_selector} {{ box-shadow: {backdrop}; }}\n")


def _without_block(contents: str) -> str:
    pattern = re.compile(rf"(?:\n?){re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}(?:\n?)", re.DOTALL)
    return pattern.sub("\n", contents).strip("\n")


def sync_window_css(values: dict, paths=GTK_CSS_PATHS) -> bool:
    """Write (or remove) the marked block in each GTK user stylesheet.

    Everything outside the markers -- the user's own CSS, and the desktop
    icon block -- is kept exactly. Returns whether any file changed.
    """
    changed = False
    for path in paths:
        path = Path(path)
        try:
            original = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            original = ""
        css = render_window_css(values, path.parent.name)
        if not css and START_MARKER not in original:
            continue
        rest = _without_block(original)
        if css:
            block = f"{START_MARKER}\n{css}{END_MARKER}\n"
            updated = f"{rest}\n\n{block}" if rest else block
        else:
            updated = f"{rest}\n" if rest else ""
        if updated == original:
            continue
        if updated:
            _atomic_text(path, updated)
        else:
            path.unlink(missing_ok=True)
        changed = True
    return changed
