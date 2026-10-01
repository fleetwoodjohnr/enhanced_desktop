"""Every desktop customization setting, defined once.

The settings page, profiles, presets, preview/revert and the extension all
read these definitions; adding a setting here is all it takes to give it a
control, include it in profiles, and validate it on import. Values live in one
of four backends:

- "desktop": ~/.config/desktop-forge/desktop.json, applied live by the Shell
  extension. Written fully resolved, so the extension never needs its own
  copy of the defaults.
- "gsettings": a GNOME setting (schema and key), applied by GNOME itself.
- "chrome": the top bar and dock appearance already kept in config.json.
- "gtkcss": stored in desktop.json and also written as GTK 3/4 user CSS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

SECTIONS = (
    ("profiles", "Profiles & Presets", "preferences-desktop-appearance-symbolic"),
    ("windows", "Windows", "preferences-desktop-apps-symbolic"),
    ("animations", "Animations", "media-playback-start-symbolic"),
    ("tiling", "Tiling & Snapping", "view-dual-symbolic"),
    ("workspaces", "Workspaces & Displays", "video-display-symbolic"),
    ("top_bar", "Top Bar", "view-top-pane-symbolic"),
    ("dock", "Dock", "view-bottom-pane-symbolic"),
    ("desktop", "Desktop & Widgets", "user-desktop-symbolic"),
    ("wallpaper", "Wallpaper & Lock", "preferences-desktop-wallpaper-symbolic"),
    ("notifications", "Notifications", "preferences-system-notifications-symbolic"),
    ("input", "Mouse & Touchpad", "input-touchpad-symbolic"),
    ("shortcuts", "Keyboard Shortcuts", "preferences-desktop-keyboard-shortcuts-symbolic"),
    ("rules", "Window Rules", "document-properties-symbolic"),
)

INTERFACE = "org.gnome.desktop.interface"
TOUCHPAD = "org.gnome.desktop.peripherals.touchpad"
MOUSE = "org.gnome.desktop.peripherals.mouse"
WM = "org.gnome.desktop.wm.preferences"
MUTTER = "org.gnome.mutter"
BACKGROUND = "org.gnome.desktop.background"
SCREENSAVER = "org.gnome.desktop.screensaver"
SESSION = "org.gnome.desktop.session"
NOTIFY = "org.gnome.desktop.notifications"
DOCK = "org.gnome.shell.extensions.dash-to-dock"

OPEN_STYLES = (("gnome", "GNOME default"), ("fade", "Fade"), ("scale", "Zoom"), ("slide", "Slide up"),
               ("pop", "Pop"), ("none", "None"))
GESTURE_ACTIONS = (("default", "GNOME default"), ("none", "Nothing"), ("overview", "Activities overview"),
                   ("app_grid", "App grid"), ("show_desktop", "Show desktop"),
                   ("workspace_next", "Next workspace"), ("workspace_previous", "Previous workspace"),
                   ("maximize", "Maximize or restore"), ("minimize", "Minimize window"),
                   ("close", "Close window"), ("tiling", "Turn tiling on or off"),
                   ("tiling_layout", "Next tiling layout"))


@dataclass(frozen=True)
class Setting:
    id: str
    section: str
    group: str
    label: str
    kind: str  # bool, int, float, enum, color, string, list
    default: Any
    backend: str = "desktop"  # desktop, gsettings, chrome, gtkcss or dock
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    digits: int = 0
    choices: tuple = ()
    schema: str = ""
    key: str = ""
    unit: str = ""
    # Only shown and applied while this bool setting is on.
    requires: str = ""
    # Part of profiles and presets. Machine-specific values (paths, the dock's
    # icon size for this screen) warn when imported elsewhere.
    profile: bool = True
    machine: bool = False
    # Drawn by a hand-built editor (existing Top Bar/Dock rows, rules,
    # shortcuts) rather than a generated row.
    generated_ui: bool = True
    experimental: bool = False
    # For lists: "string" (desktop IDs) or "rule" (window rules).
    items: str = ""


def _s(*args, **kwargs) -> Setting:
    return Setting(*args, **kwargs)


SETTINGS: tuple[Setting, ...] = (
    # -- windows ---------------------------------------------------------------
    _s("windows.active_opacity", "windows", "Transparency", "Focused window", "float", 1.0,
       minimum=0.5, maximum=1.0, step=0.05, digits=2, description="1 is fully opaque"),
    _s("windows.inactive_opacity", "windows", "Transparency", "Other windows", "float", 1.0,
       minimum=0.3, maximum=1.0, step=0.05, digits=2),
    _s("windows.moving_opacity", "windows", "Transparency", "While moving", "float", 1.0,
       minimum=0.3, maximum=1.0, step=0.05, digits=2),
    _s("windows.dim_inactive", "windows", "Transparency", "Dim other windows", "float", 0.0,
       minimum=0.0, maximum=0.6, step=0.05, digits=2, description="0 leaves them as they are"),
    _s("windows.corner_radius", "windows", "Shape", "Rounded corners", "int", 0, minimum=0, maximum=32,
       unit="px", description="0 keeps each app's own corners; maximized windows stay square"),
    _s("windows.border_width", "windows", "Borders", "Focus border", "int", 0, minimum=0, maximum=8,
       unit="px", description="0 draws no border"),
    _s("windows.border_accent", "windows", "Borders", "Use the accent color", "bool", True,
       requires="windows.border_width"),
    _s("windows.border_color", "windows", "Borders", "Border color", "color", "#3584e4",
       requires="windows.border_width"),
    _s("windows.border_gradient", "windows", "Borders", "Gradient border", "bool", False,
       description="The focused window's border blends into a second color", requires="windows.border_width"),
    _s("windows.border_color2", "windows", "Borders", "Second border color", "color", "#00ff99",
       requires="windows.border_gradient"),
    _s("windows.border_rotate", "windows", "Borders", "Turn the gradient slowly", "bool", False,
       description="Redraws the focused window about 20 times a second", requires="windows.border_gradient"),
    _s("windows.inactive_border_width", "windows", "Borders", "Border on other windows", "int", 0,
       minimum=0, maximum=4, unit="px"),
    _s("windows.inactive_border_color", "windows", "Borders", "Other windows' border color", "color",
       "#77767b"),
    _s("windows.shadow", "windows", "Shadows", "Window shadows", "enum", "default", backend="gtkcss",
       choices=(("default", "App default"), ("none", "None"), ("subtle", "Subtle"), ("strong", "Strong")),
       description="For GTK apps; other apps draw their own"),
    _s("windows.exclude", "windows", "Exceptions", "Apps left as they are", "list", [],
       description="Desktop IDs these effects never touch, such as games", generated_ui=False,
       items="string"),

    # -- animations -------------------------------------------------------------
    _s("animations.enabled", "animations", "General", "Animations", "bool", True, backend="gsettings",
       schema=INTERFACE, key="enable-animations"),
    _s("animations.speed", "animations", "General", "Speed", "float", 1.0, minimum=0.25, maximum=3.0,
       step=0.05, digits=2, description="2 is twice as fast", requires="animations.enabled"),
    _s("animations.window_open", "animations", "Windows", "Opening", "enum", "gnome", choices=OPEN_STYLES,
       requires="animations.enabled"),
    _s("animations.window_close", "animations", "Windows", "Closing", "enum", "gnome", choices=OPEN_STYLES,
       requires="animations.enabled"),
    _s("animations.minimize", "animations", "Windows", "Minimizing", "enum", "gnome",
       choices=(("gnome", "GNOME default"), ("fade", "Fade"), ("scale", "Zoom"), ("none", "None")),
       requires="animations.enabled"),
    _s("animations.duration", "animations", "Windows", "Duration", "int", 250, minimum=50, maximum=1000,
       step=10, unit="ms", description="For the styles above other than GNOME default",
       requires="animations.enabled"),
    _s("animations.workspace", "animations", "Workspaces", "Switching workspaces", "enum", "default",
       choices=(("default", "GNOME default"), ("fast", "Faster"), ("slow", "Slower"), ("instant", "Instant")),
       requires="animations.enabled"),

    # -- tiling and snapping ------------------------------------------------------
    _s("tiling.enabled", "tiling", "Tiling", "Tile windows automatically", "bool", False,
       description="New windows are arranged side by side instead of overlapping"),
    _s("tiling.layout", "tiling", "Tiling", "Layout", "enum", "master",
       choices=(("master", "Main and stack"), ("dwindle", "Dwindle (Hyprland)"), ("centered", "Centered main"),
                ("scrolling", "Scrolling columns"), ("columns", "Columns"), ("rows", "Rows"), ("grid", "Grid"),
                ("monocle", "One at a time")),
       requires="tiling.enabled"),
    _s("tiling.master_ratio", "tiling", "Tiling", "Main area", "float", 0.55, minimum=0.2, maximum=0.8,
       step=0.05, digits=2, requires="tiling.enabled"),
    _s("tiling.scroll_columns", "tiling", "Tiling", "Columns on screen", "int", 2, minimum=1, maximum=4,
       description="For Scrolling columns", requires="tiling.enabled"),
    _s("tiling.animate", "tiling", "Tiling", "Glide windows into place", "bool", True,
       requires="tiling.enabled"),
    _s("tiling.indicator", "tiling", "Tiling", "Layout switcher in the top bar", "bool", True,
       requires="tiling.enabled"),
    _s("tiling.gaps_inner", "tiling", "Gaps", "Between windows", "int", 8, minimum=0, maximum=48, unit="px"),
    _s("tiling.gaps_outer", "tiling", "Gaps", "Around the edges", "int", 8, minimum=0, maximum=64, unit="px"),
    _s("tiling.smart_gaps", "tiling", "Gaps", "No gaps around a single window", "bool", True),
    _s("tiling.new_window", "tiling", "Tiling", "New windows go", "enum", "end",
       choices=(("end", "At the end"), ("after_focus", "After the focused window"), ("master", "Into the main area")),
       requires="tiling.enabled"),
    _s("tiling.float_dialogs", "tiling", "Floating windows", "Keep dialogs floating", "bool", True,
       requires="tiling.enabled"),
    _s("tiling.float_fixed", "tiling", "Floating windows", "Keep fixed-size windows floating", "bool", True,
       requires="tiling.enabled"),
    _s("snap.edge_tiling", "tiling", "Snapping", "Snap to screen edges", "bool", True, backend="gsettings",
       schema=MUTTER, key="edge-tiling"),
    _s("snap.quarters", "tiling", "Snapping", "Snap to corners (quarters)", "bool", False,
       description="Drag a window to a corner to fill that quarter of the screen"),
    _s("snap.gaps", "tiling", "Snapping", "Gaps around snapped windows", "int", 0, minimum=0, maximum=32,
       unit="px"),
    # Whether GNOME's edge snapping was on when Desktop Forge's replaced it,
    # so turning corners and gaps off again restores what the user had.
    _s("snap.gnome_edge_before", "tiling", "Snapping", "GNOME edge snapping before", "bool", True,
       profile=False, generated_ui=False),
    _s("floating.center_new", "tiling", "Floating windows", "Center new windows", "bool", True,
       backend="gsettings", schema=MUTTER, key="center-new-windows"),
    _s("floating.attach_modal", "tiling", "Floating windows", "Attach dialogs to their window", "bool", True,
       backend="gsettings", schema=MUTTER, key="attach-modal-dialogs"),

    # -- workspaces and displays ----------------------------------------------------
    _s("workspaces.dynamic", "workspaces", "Workspaces", "Add and remove workspaces as needed", "bool", True,
       backend="gsettings", schema=MUTTER, key="dynamic-workspaces"),
    _s("workspaces.count", "workspaces", "Workspaces", "Number of workspaces", "int", 4, backend="gsettings",
       schema=WM, key="num-workspaces", minimum=1, maximum=36, description="When not added as needed"),
    _s("workspaces.primary_only", "workspaces", "Displays", "Workspaces on the main display only", "bool",
       False, backend="gsettings", schema=MUTTER, key="workspaces-only-on-primary",
       description="Other displays then show the same windows on every workspace"),
    _s("workspaces.wrap", "workspaces", "Workspaces", "Wrap around at the last workspace", "bool", False),

    # -- top bar (beyond the existing Top Bar rows) ----------------------------------
    _s("top_bar.floating", "top_bar", "Shape", "Floating bar", "bool", False,
       description="Detached from the screen edge, with rounded ends"),
    _s("top_bar.margin", "top_bar", "Shape", "Distance from the edge", "int", 6, minimum=0, maximum=24,
       unit="px", requires="top_bar.floating"),
    _s("top_bar.radius", "top_bar", "Shape", "Corner radius", "int", 12, minimum=0, maximum=24, unit="px",
       requires="top_bar.floating"),
    _s("top_bar.blur", "top_bar", "Shape", "Blur the wallpaper behind the bar", "bool", False),
    _s("top_bar.show_activities", "top_bar", "Contents", "Workspace indicator", "bool", True),
    _s("top_bar.clock_position", "top_bar", "Contents", "Clock position", "enum", "center",
       choices=(("center", "Center"), ("left", "Left"), ("right", "Right"))),
    _s("clock.format", "top_bar", "Clock", "Time format", "enum", "24h", backend="gsettings",
       schema=INTERFACE, key="clock-format", choices=(("24h", "24-hour"), ("12h", "12-hour"))),
    _s("clock.seconds", "top_bar", "Clock", "Seconds", "bool", False, backend="gsettings",
       schema=INTERFACE, key="clock-show-seconds"),
    _s("clock.weekday", "top_bar", "Clock", "Weekday", "bool", False, backend="gsettings",
       schema=INTERFACE, key="clock-show-weekday"),
    _s("clock.date", "top_bar", "Clock", "Date", "bool", True, backend="gsettings",
       schema=INTERFACE, key="clock-show-date"),
    _s("top_bar.battery_percentage", "top_bar", "Contents", "Battery percentage", "bool", False,
       backend="gsettings", schema=INTERFACE, key="show-battery-percentage"),
    # Kept in config.json by the existing Top Bar rows; listed for profiles.
    _s("chrome.top_bar.visibility", "top_bar", "Top Bar", "Visibility", "enum", "always", backend="chrome",
       choices=(("always", "Always visible"), ("intelligent", "Intelligent auto-hide"),
                ("auto", "Always hidden")), generated_ui=False),
    _s("chrome.top_bar.position", "top_bar", "Top Bar", "Position", "enum", "top", backend="chrome",
       choices=(("top", "Top"), ("bottom", "Bottom")), generated_ui=False),
    _s("chrome.top_bar.height", "top_bar", "Top Bar", "Height", "int", 32, backend="chrome",
       minimum=24, maximum=64, generated_ui=False),
    _s("chrome.top_bar.opacity", "top_bar", "Top Bar", "Opacity", "float", 0.96, backend="chrome",
       minimum=0, maximum=1, digits=2, generated_ui=False),
    _s("chrome.top_bar.background", "top_bar", "Top Bar", "Background", "color", "", backend="chrome",
       generated_ui=False),

    # -- dock -----------------------------------------------------------------------
    _s("dock.icon_size", "dock", "Dock", "Icon size", "int", 48, backend="gsettings", schema=DOCK,
       key="dash-max-icon-size", minimum=16, maximum=128, generated_ui=False, machine=True),
    _s("dock.position", "dock", "Dock", "Position", "enum", "BOTTOM", backend="gsettings", schema=DOCK,
       key="dock-position", choices=(("BOTTOM", "Bottom"), ("LEFT", "Left"), ("RIGHT", "Right"),
                                     ("TOP", "Top")), generated_ui=False),
    # Dash to Dock's visibility is four keys that only make sense together
    # (fixed, autohide, intellihide, manualhide), so it is one setting here.
    _s("dock.visibility", "dock", "Dock", "Visibility", "enum", "intelligent", backend="dock",
       choices=(("always", "Always visible"), ("intelligent", "Intelligent hide"), ("auto", "Auto-hide")),
       generated_ui=False),
    # The existing Dock rows show these two as one "maximum length" slider.
    _s("dock.extend", "dock", "Dock", "Stretch to the full edge (panel mode)", "bool", False,
       backend="gsettings", schema=DOCK, key="extend-height", generated_ui=False),
    _s("dock.length", "dock", "Dock", "Maximum length", "float", 0.9, backend="gsettings", schema=DOCK,
       key="height-fraction", minimum=0.33, maximum=1.0, step=0.05, digits=2, generated_ui=False),
    _s("chrome.dock.opacity", "dock", "Dock", "Background opacity", "float", 0.92, backend="chrome",
       minimum=0, maximum=1, digits=2, generated_ui=False),
    _s("chrome.dock.background", "dock", "Dock", "Background color", "color", "", backend="chrome",
       generated_ui=False),
    _s("dock.transparency", "dock", "Look", "Background", "enum", "DEFAULT", backend="gsettings",
       schema=DOCK, key="transparency-mode",
       choices=(("DEFAULT", "Theme default"), ("FIXED", "Fixed opacity"), ("DYNAMIC", "Clear until a window is near"))),
    _s("dock.shrink", "dock", "Look", "Compact", "bool", False, backend="gsettings", schema=DOCK,
       key="custom-theme-shrink"),
    _s("dock.indicator", "dock", "Look", "Running apps marker", "enum", "DEFAULT", backend="gsettings",
       schema=DOCK, key="running-indicator-style",
       choices=(("DEFAULT", "Default"), ("DOTS", "Dots"), ("SQUARES", "Squares"), ("DASHES", "Dashes"),
                ("SEGMENTED", "Segmented"), ("SOLID", "Solid"), ("METRO", "Metro"), ("DOT", "Single dot"))),
    _s("dock.show_trash", "dock", "Contents", "Trash", "bool", True, backend="gsettings", schema=DOCK,
       key="show-trash"),
    _s("dock.show_mounts", "dock", "Contents", "Drives and volumes", "bool", True, backend="gsettings",
       schema=DOCK, key="show-mounts"),
    _s("dock.show_apps_button", "dock", "Contents", "Show Apps button", "bool", True, backend="gsettings",
       schema=DOCK, key="show-show-apps-button"),
    _s("dock.click_action", "dock", "Behavior", "Clicking an open app", "enum", "focus-or-previews",
       backend="gsettings", schema=DOCK, key="click-action",
       choices=(("focus-or-previews", "Focus, or show its windows"), ("minimize", "Minimize"),
                ("cycle-windows", "Cycle through its windows"), ("previews", "Show its windows"),
                ("launch", "Open a new window"), ("focus-minimize-or-previews", "Focus or minimize"))),
    _s("dock.intellihide_mode", "dock", "Behavior", "Intelligent hide reacts to", "enum",
       "FOCUS_APPLICATION_WINDOWS", backend="gsettings", schema=DOCK, key="intellihide-mode",
       choices=(("ALL_WINDOWS", "Any window"), ("FOCUS_APPLICATION_WINDOWS", "The focused app's windows"),
                ("MAXIMIZED_WINDOWS", "Maximized windows only"), ("ALWAYS_ON_TOP", "Always-on-top windows only"))),
    _s("dock.all_monitors", "dock", "Behavior", "On every display", "bool", False, backend="gsettings",
       schema=DOCK, key="multi-monitor"),

    # -- desktop and widgets --------------------------------------------------------
    _s("desktop.grid", "desktop", "Arranging widgets", "Grid", "int", 8, minimum=4, maximum=32, unit="px",
       description="Widgets move and resize in steps of this size"),
    _s("desktop.snap_distance", "desktop", "Arranging widgets", "Magnet distance", "int", 12, minimum=0,
       maximum=48, unit="px", description="How close to another widget's edge before it snaps; 0 is off"),
    _s("desktop.hot_corner", "desktop", "Desktop", "Hot corner opens the overview", "bool", True,
       backend="gsettings", schema=INTERFACE, key="enable-hot-corners"),

    # -- wallpaper and lock screen --------------------------------------------------
    _s("wallpaper.light", "wallpaper", "Wallpaper", "Wallpaper", "string", "", backend="gsettings",
       schema=BACKGROUND, key="picture-uri", machine=True, description="An image file", generated_ui=False),
    _s("wallpaper.dark", "wallpaper", "Wallpaper", "Wallpaper in dark style", "string", "",
       backend="gsettings", schema=BACKGROUND, key="picture-uri-dark", machine=True, generated_ui=False),
    _s("wallpaper.options", "wallpaper", "Wallpaper", "Fit", "enum", "zoom", backend="gsettings",
       schema=BACKGROUND, key="picture-options",
       choices=(("zoom", "Fill the screen"), ("scaled", "Fit inside"), ("centered", "Center"),
                ("stretched", "Stretch"), ("wallpaper", "Tile"), ("spanned", "Span displays"))),
    _s("wallpaper.color", "wallpaper", "Wallpaper", "Color behind it", "color", "#023c88",
       backend="gsettings", schema=BACKGROUND, key="primary-color"),
    _s("wallpaper.blur", "wallpaper", "Effects", "Blur the wallpaper", "int", 0, minimum=0, maximum=60,
       unit="px", description="Behind the desktop icons and widgets"),
    _s("wallpaper.dim", "wallpaper", "Effects", "Dim the wallpaper", "float", 0.0, minimum=0, maximum=0.8,
       step=0.05, digits=2),
    _s("wallpaper.slideshow", "wallpaper", "Slideshow", "Change the wallpaper automatically", "bool",
       False),
    _s("wallpaper.folder", "wallpaper", "Slideshow", "Folder of pictures", "string", "", machine=True,
       requires="wallpaper.slideshow", generated_ui=False),
    _s("wallpaper.interval", "wallpaper", "Slideshow", "Every", "int", 30, minimum=1, maximum=1440,
       unit="min", requires="wallpaper.slideshow"),
    _s("wallpaper.shuffle", "wallpaper", "Slideshow", "In random order", "bool", True,
       requires="wallpaper.slideshow"),
    _s("lock.picture", "wallpaper", "Lock screen", "Lock screen picture", "string", "", backend="gsettings",
       schema=SCREENSAVER, key="picture-uri", machine=True, generated_ui=False),
    _s("lock.enabled", "wallpaper", "Lock screen", "Lock when the screen blanks", "bool", True,
       backend="gsettings", schema=SCREENSAVER, key="lock-enabled"),
    _s("lock.delay", "wallpaper", "Lock screen", "Lock after the screen blanks", "int", 0,
       backend="gsettings", schema=SCREENSAVER, key="lock-delay", minimum=0, maximum=3600, step=30, unit="s"),
    _s("lock.idle", "wallpaper", "Lock screen", "Blank the screen after", "int", 300, backend="gsettings",
       schema=SESSION, key="idle-delay", minimum=0, maximum=3600, step=60, unit="s",
       description="0 never blanks it"),
    _s("lock.notifications", "wallpaper", "Lock screen", "Notifications on the lock screen", "bool", True,
       backend="gsettings", schema=NOTIFY, key="show-in-lock-screen"),
    _s("lock.full_name", "wallpaper", "Lock screen", "Your name in the top bar while locked", "bool", False,
       backend="gsettings", schema=SCREENSAVER, key="show-full-name-in-top-bar"),

    # -- notifications -----------------------------------------------------------------
    _s("notifications.banners", "notifications", "Banners", "Show banners", "bool", True,
       backend="gsettings", schema=NOTIFY, key="show-banners",
       description="Off is Do Not Disturb: notifications still collect in the list"),
    _s("notifications.position", "notifications", "Banners", "Position", "enum", "center",
       choices=(("center", "Top center"), ("left", "Top left"), ("right", "Top right")),
       requires="notifications.banners"),
    _s("notifications.timeout", "notifications", "Banners", "Stay on screen for", "int", 4, minimum=2,
       maximum=30, unit="s", requires="notifications.banners"),
    _s("notifications.opacity", "notifications", "Banners", "Opacity", "float", 1.0, minimum=0.4,
       maximum=1.0, step=0.05, digits=2, requires="notifications.banners"),

    # -- input -------------------------------------------------------------------------
    _s("touchpad.tap", "input", "Touchpad", "Tap to click", "bool", False, backend="gsettings",
       schema=TOUCHPAD, key="tap-to-click"),
    _s("touchpad.natural", "input", "Touchpad", "Natural scrolling", "bool", True, backend="gsettings",
       schema=TOUCHPAD, key="natural-scroll", description="Content moves with your fingers"),
    _s("touchpad.two_finger", "input", "Touchpad", "Two-finger scrolling", "bool", True, backend="gsettings",
       schema=TOUCHPAD, key="two-finger-scrolling-enabled"),
    _s("touchpad.speed", "input", "Touchpad", "Speed", "float", 0.0, backend="gsettings", schema=TOUCHPAD,
       key="speed", minimum=-1, maximum=1, step=0.05, digits=2),
    _s("touchpad.typing", "input", "Touchpad", "Off while typing", "bool", True, backend="gsettings",
       schema=TOUCHPAD, key="disable-while-typing"),
    _s("touchpad.click", "input", "Touchpad", "Secondary click", "enum", "default", backend="gsettings",
       schema=TOUCHPAD, key="click-method",
       choices=(("default", "Device default"), ("fingers", "Two-finger click"), ("areas", "Corner click"))),
    _s("mouse.speed", "input", "Mouse", "Speed", "float", 0.0, backend="gsettings", schema=MOUSE, key="speed",
       minimum=-1, maximum=1, step=0.05, digits=2),
    _s("mouse.natural", "input", "Mouse", "Natural scrolling", "bool", False, backend="gsettings",
       schema=MOUSE, key="natural-scroll"),
    _s("mouse.acceleration", "input", "Mouse", "Acceleration", "enum", "default", backend="gsettings",
       schema=MOUSE, key="accel-profile",
       choices=(("default", "Device default"), ("flat", "Off"), ("adaptive", "On"))),
    _s("input.drag_key", "input", "Moving windows", "Hold to drag a window from anywhere", "enum",
       "<Super>", backend="gsettings", schema=WM, key="mouse-button-modifier",
       choices=(("<Super>", "Super"), ("<Alt>", "Alt"), ("", "Nothing"))),
    _s("input.resize_right", "input", "Moving windows", "Resize with the right button", "bool", False,
       backend="gsettings", schema=WM, key="resize-with-right-button"),
    *(_s(f"gestures.{name}", "input", "Touchpad gestures", label, "enum", "default", choices=GESTURE_ACTIONS,
         description=hint)
      for name, label, hint in (
          ("three_up", "Three fingers up", "GNOME: overview"),
          ("three_down", "Three fingers down", "GNOME: leave the overview"),
          ("three_left", "Three fingers left", "GNOME: next workspace"),
          ("three_right", "Three fingers right", "GNOME: previous workspace"),
          ("four_up", "Four fingers up", ""), ("four_down", "Four fingers down", ""),
          ("four_left", "Four fingers left", ""), ("four_right", "Four fingers right", ""),
          ("pinch_in", "Three-finger pinch in", ""), ("pinch_out", "Three-finger pinch out", ""))),

    # -- rules ----------------------------------------------------------------------------
    _s("rules.list", "rules", "Window rules", "Rules", "list", [], generated_ui=False, items="rule"),
)

BY_ID = {setting.id: setting for setting in SETTINGS}


def in_section(section: str):
    return [setting for setting in SETTINGS if setting.section == section]


def validate(setting: Setting, value):
    """The value, coerced and clamped to the setting; ValueError if it cannot be."""
    kind = setting.kind
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{setting.label}: must be on or off")
        return value
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{setting.label}: must be a number")
        low = setting.minimum if setting.minimum is not None else value
        high = setting.maximum if setting.maximum is not None else value
        value = max(low, min(high, value))
        return int(round(value)) if kind == "int" else round(float(value), 4)
    if kind == "enum":
        allowed = [choice[0] for choice in setting.choices]
        if value not in allowed:
            raise ValueError(f"{setting.label}: choose one of the listed options")
        return value
    if kind == "color":
        if not isinstance(value, str) or (value and not re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
            raise ValueError(f"{setting.label}: enter a color such as #3584e4")
        return value.lower()
    if kind == "string":
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError(f"{setting.label}: enter text")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise ValueError(f"{setting.label}: must be a list")
        if setting.items == "rule":
            from .rules import validate_rules
            return validate_rules(value)
        if setting.items == "string":
            if len(value) > 200 or not all(isinstance(item, str) and 0 < len(item) <= 255 for item in value):
                raise ValueError(f"{setting.label}: must be a list of app IDs")
            return list(dict.fromkeys(value))
        return value
    raise ValueError(f"Unknown kind for {setting.id}")
