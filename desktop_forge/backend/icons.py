"""Icon resolution shared by the app grid and the entry diagnostics."""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

FALLBACK_ICON = "application-x-executable"


def icon_from_string(value: str) -> Gio.Icon:
    """Turn an Icon= value into something a Gtk.Image will accept.

    A desktop entry's Icon is either a themed name ("libreoffice-writer") or
    an absolute path (as in the hand-written photo-video-organizer entry,
    which points at an .svg in a checkout). The two need different Gio types.
    """
    if not value:
        return Gio.ThemedIcon.new(FALLBACK_ICON)
    if os.path.isabs(value):
        gfile = Gio.File.new_for_path(value)
        if gfile.query_exists(None):
            return Gio.FileIcon.new(gfile)
        return Gio.ThemedIcon.new(FALLBACK_ICON)
    return Gio.ThemedIcon.new(value)


def icon_exists(value: str) -> bool:
    """Whether an Icon= value will actually render as something."""
    if not value:
        return False
    if os.path.isabs(value):
        return os.path.isfile(value)
    display = Gdk.Display.get_default()
    if display is None:
        return True  # headless: can't tell, so don't cry wolf
    return Gtk.IconTheme.get_for_display(display).has_icon(value)
