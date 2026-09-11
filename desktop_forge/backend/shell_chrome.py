"""Small, typed bridge to Dash-to-Dock's public GSettings schema."""
from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio


SCHEMA = "org.gnome.shell.extensions.dash-to-dock"
POSITIONS = ("TOP", "RIGHT", "BOTTOM", "LEFT")
VISIBILITIES = ("always", "intelligent", "auto")


@dataclass
class DockState:
    visibility: str
    position: str
    icon_size: int
    maximum_length: float


class DashToDock:
    """Read and update only the small settings subset exposed by Overall."""

    def __init__(self, settings: Gio.Settings):
        self.settings = settings

    @classmethod
    def find(cls) -> "DashToDock | None":
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(SCHEMA, True) if source else None
        if schema is None:
            return None
        return cls(Gio.Settings.new_full(schema, None, None))

    def writable(self, *keys: str) -> bool:
        return all(self.settings.is_writable(key) for key in keys)

    def read(self) -> DockState:
        if self.settings.get_boolean("dock-fixed"):
            visibility = "always"
        elif self.settings.get_boolean("intellihide"):
            visibility = "intelligent"
        else:
            visibility = "auto"
        maximum = 1.0 if self.settings.get_boolean("extend-height") else max(
            0.33, min(1.0, self.settings.get_double("height-fraction"))
        )
        position = self.settings.get_string("dock-position").upper()
        return DockState(
            visibility=visibility,
            position=position if position in POSITIONS else "BOTTOM",
            icon_size=max(16, min(64, self.settings.get_int("dash-max-icon-size"))),
            maximum_length=maximum,
        )

    def set_visibility(self, value: str) -> None:
        if value not in VISIBILITIES:
            raise ValueError(f"Unknown dock visibility: {value}")
        if not self.writable("dock-fixed", "autohide", "intellihide", "manualhide"):
            raise PermissionError("Dash-to-Dock visibility is locked")
        self.settings.delay()
        self.settings.set_boolean("manualhide", False)
        self.settings.set_boolean("dock-fixed", value == "always")
        self.settings.set_boolean("autohide", value != "always")
        self.settings.set_boolean("intellihide", value == "intelligent")
        self.settings.apply()

    def set_position(self, value: str) -> None:
        value = value.upper()
        if value not in POSITIONS:
            raise ValueError(f"Unknown dock position: {value}")
        if not self.writable("dock-position"):
            raise PermissionError("Dash-to-Dock position is locked")
        if not self.settings.set_string("dock-position", value):
            raise OSError("Dash-to-Dock rejected the position")
        self.settings.apply()

    def set_icon_size(self, value: int) -> None:
        if not self.writable("dash-max-icon-size"):
            raise PermissionError("Dash-to-Dock icon size is locked")
        self.settings.set_int("dash-max-icon-size", max(16, min(64, int(value))))
        self.settings.apply()

    def set_maximum_length(self, value: float) -> None:
        if not self.writable("height-fraction", "extend-height"):
            raise PermissionError("Dash-to-Dock length is locked")
        self.settings.delay()
        self.settings.set_boolean("extend-height", False)
        self.settings.set_double("height-fraction", max(0.33, min(1.0, float(value))))
        self.settings.apply()
