"""The built-in looks. Each is a starting point: duplicate one to change it.

A preset only speaks for the look and feel: window effects, animations,
tiling, the top bar, the dock and notification banners. Applying one resets
those to neutral first and then applies the preset's own values, so moving
from Hyprland-inspired to Minimal does not leave tiling switched on. Input
devices, the clock, lock screen, pictures, shortcuts and window rules are
the user's own and are never touched by a preset.
"""
from __future__ import annotations

from .registry import BY_ID, SETTINGS

_EXTRA = ("animations.enabled", "snap.edge_tiling", "dock.position", "dock.visibility", "dock.extend",
          "dock.length", "dock.transparency", "dock.shrink", "dock.indicator", "dock.intellihide_mode")
_GROUPS = ("windows", "animations", "tiling", "snap", "top_bar", "chrome", "notifications")
_NOT_LOOK = {"windows.exclude", "top_bar.battery_percentage", "notifications.banners",
             "snap.gnome_edge_before"}

# Settings a preset resets before applying its own values.
MANAGED = tuple(
    s.id for s in SETTINGS
    if s.id not in _NOT_LOOK and (
        s.id in _EXTRA
        or (s.backend != "gsettings" and s.id.split(".", 1)[0] in _GROUPS)
        or s.id in ("wallpaper.blur", "wallpaper.dim")))

PRESETS = (
    {"id": "minimal", "name": "Minimal", "icon": "view-reveal-symbolic",
     "description": "Out of the way: the top bar and dock hide until you need them.",
     "values": {"chrome.top_bar.visibility": "intelligent", "top_bar.show_activities": False,
                "dock.visibility": "intelligent", "dock.shrink": True,
                "dock.transparency": "DYNAMIC", "windows.shadow": "subtle",
                "animations.window_open": "fade", "animations.window_close": "fade",
                "animations.duration": 180, "notifications.timeout": 3}},
    {"id": "productivity", "name": "Productivity", "icon": "view-dual-symbolic",
     "description": "Windows tile side by side with small gaps; snapping fills halves and quarters.",
     "values": {"tiling.enabled": True, "tiling.layout": "master", "tiling.gaps_inner": 6,
                "tiling.gaps_outer": 6, "snap.quarters": True, "snap.gaps": 6,
                "animations.speed": 1.5, "animations.workspace": "fast", "windows.border_width": 2,
                "dock.visibility": "intelligent", "notifications.timeout": 3}},
    {"id": "macos", "name": "macOS-inspired", "icon": "starred-symbolic",
     "description": "A translucent bar, a floating dock with a dot under open apps, soft round windows.",
     "values": {"chrome.top_bar.opacity": 0.78, "top_bar.blur": True, "top_bar.clock_position": "right",
                "dock.position": "BOTTOM", "dock.visibility": "intelligent",
                "dock.transparency": "FIXED", "chrome.dock.opacity": 0.55, "dock.indicator": "DOT",
                "windows.corner_radius": 12, "windows.shadow": "strong",
                "animations.window_open": "scale", "animations.minimize": "scale",
                "animations.duration": 260, "notifications.position": "right"}},
    {"id": "windows", "name": "Windows-inspired", "icon": "view-app-grid-symbolic",
     "description": "A full-width taskbar along the bottom, the clock on the right, snap to corners.",
     "values": {"dock.position": "BOTTOM", "dock.extend": True, "dock.length": 1.0, "dock.visibility": "always", "dock.indicator": "METRO", "dock.transparency": "FIXED",
                "chrome.dock.opacity": 0.9, "top_bar.clock_position": "right",
                "windows.corner_radius": 8, "windows.shadow": "subtle", "snap.edge_tiling": True,
                "snap.quarters": True, "animations.window_open": "slide", "animations.duration": 200,
                "notifications.position": "right"}},
    {"id": "hyprland", "name": "Hyprland-inspired", "icon": "view-grid-symbolic",
     "description": "Dwindle tiling with gaps, windows gliding into place, a gradient focus border "
                    "and a floating bar.",
     "values": {"tiling.enabled": True, "tiling.layout": "dwindle", "tiling.gaps_inner": 5,
                "tiling.gaps_outer": 12, "tiling.smart_gaps": False, "snap.gaps": 5,
                "windows.border_width": 2, "windows.border_accent": False, "windows.border_color": "#33ccff",
                "windows.border_gradient": True, "windows.border_color2": "#00ff99",
                "windows.inactive_border_width": 2, "windows.inactive_border_color": "#595959",
                "windows.corner_radius": 10, "windows.shadow": "subtle",
                "top_bar.floating": True, "top_bar.margin": 6, "top_bar.radius": 12, "top_bar.blur": True,
                "chrome.top_bar.opacity": 0.85, "dock.visibility": "intelligent",
                "dock.intellihide_mode": "ALL_WINDOWS",
                "animations.window_open": "pop", "animations.window_close": "fade",
                "animations.duration": 200, "animations.speed": 1.25, "animations.workspace": "fast"}},
    {"id": "glass", "name": "Glass", "icon": "weather-clear-night-symbolic",
     "description": "See-through surfaces: a blurred bar, a clear dock and banners, and windows behind "
                    "others fading a little.",
     "values": {"windows.inactive_opacity": 0.9,
                "windows.corner_radius": 14, "windows.shadow": "strong", "top_bar.blur": True,
                "top_bar.floating": True, "chrome.top_bar.opacity": 0.5, "dock.transparency": "FIXED",
                "chrome.dock.opacity": 0.4, "notifications.opacity": 0.85,
                "animations.window_open": "fade", "animations.window_close": "fade"}},
    {"id": "compact", "name": "Compact", "icon": "zoom-out-symbolic",
     "description": "A thinner bar, a smaller dock and quicker animations for small screens.",
     "values": {"chrome.top_bar.height": 26, "dock.shrink": True, "dock.extend": False,
                "windows.corner_radius": 6, "animations.speed": 1.5, "notifications.timeout": 3,
                "top_bar.show_activities": False}},
    {"id": "gaming", "name": "Gaming", "icon": "input-gaming-symbolic",
     "description": "Every window effect off and near-instant animations, so games get the GPU.",
     "values": {"animations.speed": 2.5, "animations.window_open": "none", "animations.window_close": "none",
                "animations.minimize": "none", "animations.workspace": "instant",
                "chrome.top_bar.visibility": "intelligent", "dock.visibility": "intelligent",
                "windows.shadow": "default"}},
)

BY_ID_PRESET = {preset["id"]: preset for preset in PRESETS}


def neutral(defaults) -> dict:
    """What every managed setting is reset to before a preset applies.

    `defaults(id)` gives a setting's default here -- for GNOME settings, the
    system's own default rather than the registry's guess.
    """
    return {setting_id: defaults(setting_id) for setting_id in MANAGED}


def values(preset: dict, defaults) -> dict:
    return {**neutral(defaults), **preset["values"]}


def check() -> list[str]:
    """Problems with the presets themselves; used by the tests."""
    from .registry import validate
    problems = []
    for preset in PRESETS:
        for setting_id, value in preset["values"].items():
            setting = BY_ID.get(setting_id)
            if setting is None:
                problems.append(f"{preset['id']}: unknown setting {setting_id}")
                continue
            try:
                if validate(setting, value) != value:
                    problems.append(f"{preset['id']}: {setting_id} is out of range")
            except ValueError as exc:
                problems.append(f"{preset['id']}: {exc}")
    return problems
