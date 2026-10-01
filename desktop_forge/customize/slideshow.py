"""The wallpaper slideshow, run by the desktop-forged service.

Every `wallpaper.interval` minutes the next picture from the chosen folder
becomes the wallpaper, for both light and dark style. When it last changed
is kept in the state folder, so logging in again does not skip ahead or
start over.
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

from .. import config
from .backends import DesktopStore

PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".jxl", ".svg", ".bmp", ".tif", ".tiff", ".avif", ".heic",
            ".gif"}
CHECK_EVERY = 15
STATE_PATH = os.path.join(config.STATE_DIR, "slideshow.json")
BACKGROUND = "org.gnome.desktop.background"


def pictures(folder: str) -> list[Path]:
    """Pictures directly inside a folder, by name; hidden files skipped."""
    try:
        root = Path(folder).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    if not root.is_dir():
        return []
    found = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.name.startswith(".") or entry.suffix.lower() not in PICTURES:
            continue
        try:
            resolved = entry.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_file() and resolved.is_relative_to(root):
            found.append(resolved)
    return sorted(found, key=lambda p: p.name.casefold())


def next_picture(choices: list[Path], current: str, shuffle: bool, rng=random) -> Path | None:
    if not choices:
        return None
    others = [p for p in choices if p.as_uri() != current]
    if shuffle:
        return rng.choice(others or choices)
    uris = [p.as_uri() for p in choices]
    if current in uris:
        return choices[(uris.index(current) + 1) % len(choices)]
    return choices[0]


class Slideshow:
    def __init__(self, store: DesktopStore | None = None, background=None, state_path: str = STATE_PATH,
                 clock=time.time, rng=random):
        self._store = store or DesktopStore()
        self._background = background
        self._state_path = state_path
        self._clock = clock
        self._rng = rng
        self._checked = 0.0

    def _settings(self):
        if self._background is None:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
            source = Gio.SettingsSchemaSource.get_default()
            schema = source.lookup(BACKGROUND, True) if source else None
            self._background = Gio.Settings.new_full(schema, None, None) if schema else False
        return self._background or None

    def tick(self, force: bool = False) -> str | None:
        """Change the picture if it is time; returns the new picture's URI."""
        now = self._clock()
        if not force and now - self._checked < CHECK_EVERY:
            return None
        self._checked = now
        values = self._store.read()
        if not values.get("wallpaper.slideshow") or not values.get("wallpaper.folder"):
            return None
        state = config.read_json(self._state_path) or {}
        interval = max(1, int(values.get("wallpaper.interval", 30))) * 60
        last = state.get("changed", 0)
        if not force and isinstance(last, (int, float)) and 0 <= now - last < interval:
            return None
        settings = self._settings()
        if settings is None:
            return None
        current = settings.get_string("picture-uri")
        chosen = next_picture(pictures(values["wallpaper.folder"]), current,
                              values.get("wallpaper.shuffle", True), self._rng)
        if chosen is None:
            return None
        uri = chosen.as_uri()
        settings.set_string("picture-uri", uri)
        settings.set_string("picture-uri-dark", uri)
        try:
            config.write_json(self._state_path, {"changed": now, "picture": uri})
        except OSError:
            pass
        return uri
