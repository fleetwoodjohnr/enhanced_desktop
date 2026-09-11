"""Read and activate GNOME windows through Desktop Forge's Shell extension."""
from __future__ import annotations

import json

from gi.repository import Gio, GLib


BUS_NAME = "org.jrf.DesktopForge.Shell"
OBJECT_PATH = "/org/jrf/DesktopForge/Shell"
INTERFACE = "org.jrf.DesktopForge.Shell1"


class ShellBridge:
    def __init__(self, bus=None):
        self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def windows(self) -> list[dict]:
        try:
            result = self.bus.call_sync(
                BUS_NAME, OBJECT_PATH, INTERFACE, "Windows", None,
                GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 3000, None,
            )
            value = json.loads(result.unpack()[0])
        except (GLib.Error, ValueError, TypeError) as exc:
            raise RuntimeError(
                "The Desktop Forge Shell extension is required to control this application"
            ) from exc
        if not isinstance(value, list):
            raise RuntimeError("The Desktop Forge Shell extension returned invalid window data")
        return [row for row in value if isinstance(row, dict) and row.get("desktop_id")]

    def activate(self, desktop_id: str) -> bool:
        try:
            result = self.bus.call_sync(
                BUS_NAME, OBJECT_PATH, INTERFACE, "Activate",
                GLib.Variant("(s)", (desktop_id,)), GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE, 3000, None,
            )
            return bool(result.unpack()[0])
        except GLib.Error as exc:
            raise RuntimeError(
                "The Desktop Forge Shell extension is required to focus this application"
            ) from exc
