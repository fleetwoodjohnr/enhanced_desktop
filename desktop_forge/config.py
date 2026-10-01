"""The config and state contract shared by the application components.

This module is the single definition of where things live and what shape they
have. The GTK app writes config.json; the daemon reads it and writes state
files; the GNOME Shell extension reads both. Keeping paths and schemas in one
place lets the Python data service and JavaScript renderer stay in step.

Nothing here imports GTK -- the daemon runs headless.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 5

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "desktop-forge"
)
DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "desktop-forge"
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_DIR = os.path.join(DATA_DIR, "state")

DEFAULT_NEWS_FEEDS = [
    "https://feeds.npr.org/1003/rss.xml",  # U.S.
    "https://feeds.npr.org/1006/rss.xml",  # markets and economics
    "https://feeds.npr.org/1004/rss.xml",  # world
    "https://feeds.npr.org/1019/rss.xml",  # technology
    "https://news.mit.edu/rss/topic/artificial-intelligence2",
    "https://www.phoronix.com/rss.php",  # Linux and open source
]

# Topic filters offered as a checklist in the settings UI. Each preset expands
# to the keywords the news provider matches against headline titles and feed
# categories, so "Artificial intelligence" catches a story tagged only "LLM".
# Keywords are matched on word boundaries, so short ones like "AI" are safe.
NEWS_TOPIC_PRESETS = [
    {
        "id": "world",
        "label": "World news",
        "keywords": [
            "World", "International", "Global", "Europe", "Asia", "Africa",
            "Middle East", "Latin America", "Ukraine", "China", "Russia",
        ],
    },
    {
        "id": "us",
        "label": "U.S. news",
        "keywords": [
            "U.S.", "US", "USA", "United States", "America", "American", "Washington",
            "Congress", "White House", "Supreme Court",
        ],
    },
    {
        "id": "politics",
        "label": "Politics",
        "keywords": [
            "Politics", "Political", "Election", "Senate", "Parliament",
            "Policy", "Government", "Campaign", "Legislation",
        ],
    },
    {
        "id": "business",
        "label": "Business & markets",
        "keywords": [
            "Business", "Markets", "Market", "Economy", "Economics",
            "Finance", "Stocks", "Inflation", "Trade", "Earnings",
        ],
    },
    {
        "id": "technology",
        "label": "Technology",
        "keywords": [
            "Technology", "Tech", "Software", "Hardware", "Computing",
            "Internet", "Cybersecurity", "Cyber Security", "Computer Security",
            "Information Security", "Network Security", "Data Security",
            "Chips", "Semiconductor",
        ],
    },
    {
        "id": "ai",
        "label": "Artificial intelligence",
        "keywords": [
            "AI", "Artificial Intelligence", "Machine Learning", "Neural",
            "LLM", "Chatbot", "Deep Learning", "Generative",
            "A.I.", "LLMs", "Chatbots", "OpenAI", "ChatGPT", "GPT",
        ],
    },
    {
        "id": "science",
        "label": "Science",
        "keywords": [
            "Science", "Research", "Space", "NASA", "Physics", "Climate",
            "Biology", "Astronomy", "Study",
        ],
    },
    {
        "id": "health",
        "label": "Health",
        "keywords": [
            "Health", "Medicine", "Medical", "Disease", "Vaccine",
            "Hospital", "Mental Health", "Public Health",
        ],
    },
    {
        "id": "sports",
        "label": "Sports",
        "keywords": [
            "Sports", "Football", "Soccer", "Basketball", "Baseball",
            "Olympics", "NFL", "NBA", "World Cup", "Tennis",
        ],
    },
    {
        "id": "linux",
        "label": "Linux & open source",
        "keywords": [
            "Linux", "Fedora", "GNOME", "KDE", "Open Source", "Kernel",
            "Ubuntu", "Debian", "Red Hat",
        ],
    },
]

NEWS_TOPIC_IDS = [preset["id"] for preset in NEWS_TOPIC_PRESETS]

# Widget types, and the provider each one renders. Several widgets can share a
# provider (two stock tickers, one stocks provider), so the daemon polls per
# provider, not per widget.
WIDGET_TYPES = {
    "clive": {"provider": None, "title": "CLIVE", "size": (420, 480)},
    "clock": {"provider": None, "title": "Clock", "size": (280, 130)},
    "weather": {"provider": "weather", "title": "Weather", "size": (300, 170)},
    "stocks": {"provider": "stocks", "title": "Stocks", "size": (300, 210)},
    "calendar": {"provider": "calendar", "title": "Calendar", "size": (320, 260)},
    "reminders": {"provider": "reminders", "title": "Reminders", "size": (300, 200)},
    "todos": {"provider": "todos", "title": "To-Do", "size": (420, 320)},
    "news": {"provider": "news", "title": "News", "size": (420, 340)},
    "system": {"provider": "system", "title": "System Monitor", "size": (300, 190)},
    "terminal": {"provider": None, "title": "Terminal", "size": (560, 360)},
}

# Bright system-style accents give each card an identity while retaining a
# single-accent override for people who prefer a quieter desktop.
WIDGET_ACCENTS = {
    "clive": "#32ade6",
    "clock": "#bf5af2",
    "weather": "#32ade6",
    "stocks": "#0a84ff",
    "calendar": "#ff9f0a",
    "reminders": "#ff375f",
    "todos": "#5e5ce6",
    "news": "#af52de",
    "system": "#30d158",
    "terminal": "#64d2ff",
}


def state_path(provider: str) -> str:
    return os.path.join(STATE_DIR, f"{provider}.json")


@dataclass
class Widget:
    type: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    enabled: bool = True
    monitor: int = 0
    x: int = 40
    y: int = 40
    width: int = 0
    height: int = 0
    options: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Zero means "unset" rather than "invisible" -- fill from the type's
        # natural size so a widget added from the GUI or hand-edited into the
        # JSON always has usable dimensions.
        spec = WIDGET_TYPES.get(self.type)
        if spec and (not self.width or not self.height):
            self.width, self.height = spec["size"]

    @property
    def provider(self) -> str | None:
        spec = WIDGET_TYPES.get(self.type)
        return spec["provider"] if spec else None


DEFAULT_PROVIDER_OPTIONS: dict[str, dict[str, Any]] = {
    "weather": {
        "place": "",
        "latitude": None,
        "longitude": None,
        "units": "celsius",
        "interval": 300,
        "forecast_mode": "hourly",
    },
    "stocks": {"symbols": ["AAPL", "MSFT", "GOOGL"], "interval": 300},
    "calendar": {"days_ahead": 14, "max_events": 8, "interval": 300},
    "reminders": {"interval": 30},
    "todos": {"interval": 30},
    "news": {
        "feeds": DEFAULT_NEWS_FEEDS,
        "interval": 900,
        "max_items": 40,
        "topic_presets": NEWS_TOPIC_IDS,
        "topics": [],
    },
    "system": {"interval": 5, "disk_path": "/"},
}

DEFAULT_STYLE = {
    "opacity": 0.58,
    "corner_radius": 24,
    "accent": "#0a84ff",
    "font_scale": 1.0,
    "theme_mode": "system",
    "colorful_accents": True,
    "blur_radius": 24,
}

# GNOME Shell chrome is intentionally independent from widget appearance.  A
# missing per-surface value means "use the adaptive light/dark default", while
# the dictionaries stored in config.json contain only the user's overrides.
DEFAULT_CHROME: dict[str, dict[str, Any]] = {
    "top_bar": {
        "visibility": "always",
        "position": "top",
        "height": 32,
        "opacity": 0.96,
        "foreground_mode": "auto",
        # Behaviour of the hiding modes, in seconds where timed. Mirrored by
        # CHROME_DEFAULTS in extension/chromeLogic.js.
        "reveal_delay": 0.1,
        "hide_delay": 0.5,
        "animation_time": 0.2,
        "reveal_method": "hover",
        "sensitivity": "medium",
        "hide_when": "any",
        "reveal_in_fullscreen": False,
    },
    "dock": {
        "opacity": 0.92,
        "foreground_mode": "auto",
    },
}

TOP_BAR_MODES = ("always", "intelligent", "auto")
TOP_BAR_HIDE_WHEN = ("any", "focused", "maximized")
TOP_BAR_REVEAL_METHODS = ("hover", "pressure")
TOP_BAR_SENSITIVITIES = ("low", "medium", "high")
# Inclusive (minimum, maximum) seconds for the timed top-bar settings.
TOP_BAR_TIMINGS = {"reveal_delay": (0.0, 2.0), "hide_delay": (0.0, 5.0),
                   "animation_time": (0.0, 1.0)}

CHROME_PALETTES = {
    "light": {
        "top_bar": {"background": "#fafafb", "foreground": "#222226"},
        "dock": {"background": "#f8fbff", "foreground": "#172033"},
    },
    "dark": {
        "top_bar": {"background": "#18181b", "foreground": "#ffffff"},
        "dock": {"background": "#18202c", "foreground": "#f7faff"},
    },
}

# DING owns icon geometry and GNOME owns the installed icon theme.  The values
# here are the appearance layer Desktop Forge adds on top of those native
# settings.  "system" deliberately produces no managed CSS, so an upgrade
# never changes an existing desktop until the user chooses a finish.
DEFAULT_DESKTOP_ICONS: dict[str, Any] = {
    "material": "system",
    "tint": "#7fc8ff",
    "opacity": 0.28,
    "foreground_mode": "auto",
    "foreground": "#ffffff",
    "artwork": "original",
}


@dataclass
class Config:
    version: int = SCHEMA_VERSION
    widgets: list[Widget] = field(default_factory=list)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)
    chrome: dict[str, dict[str, Any]] = field(default_factory=dict)
    desktop_icons: dict[str, Any] = field(default_factory=dict)
    # Set by the GUI to put the extension into layout-editing mode, and
    # cleared when the user finishes editing. Like everything the app tells
    # the extension, it lives in files under ~/.config/desktop-forge
    # (config.json here, desktop.json for customization). The extension's one
    # GSettings schema holds only keyboard shortcuts, because Shell's
    # keybinding API reads nothing else.
    edit_layout: bool = False
    # Saved widget arrangements: name -> [{id, enabled, monitor, x, y, width, height}].
    layouts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def provider_options(self, name: str) -> dict[str, Any]:
        """Defaults merged with the user's overrides.

        Merging on read rather than on write means a key added in a later
        version is picked up by an existing config file without a migration.
        """
        merged = copy.deepcopy(DEFAULT_PROVIDER_OPTIONS.get(name, {}))
        merged.update(self.providers.get(name, {}))
        return merged

    def widget_style(self, widget: Widget) -> dict[str, Any]:
        merged = dict(DEFAULT_STYLE)
        merged.update(self.style)
        # A pre-v2 custom accent meant "use this colour everywhere". Preserve
        # that intent unless the user has explicitly selected colourful mode.
        colourful = self.style.get("colorful_accents", "accent" not in self.style)
        merged["colorful_accents"] = colourful
        if colourful:
            merged["accent"] = WIDGET_ACCENTS.get(widget.type, merged["accent"])
        merged.update(widget.style)
        return merged

    def chrome_options(self, surface: str) -> dict[str, Any]:
        """Return one shell surface's defaults merged with user overrides."""
        merged = copy.deepcopy(DEFAULT_CHROME.get(surface, {}))
        overrides = self.chrome.get(surface, {})
        if isinstance(overrides, dict):
            merged.update(overrides)
        opacity = merged.get("opacity", DEFAULT_CHROME.get(surface, {}).get("opacity", 1))
        merged["opacity"] = max(0.0, min(1.0, float(opacity))) \
            if isinstance(opacity, (int, float)) else DEFAULT_CHROME[surface]["opacity"]
        merged["foreground_mode"] = (
            "custom" if merged.get("foreground_mode") == "custom" else "auto"
        )
        for key in ("background", "foreground"):
            if key in merged and not (
                isinstance(merged[key], str)
                and re.fullmatch(r"#[0-9a-fA-F]{6}", merged[key])
            ):
                merged.pop(key)
        if surface == "top_bar":
            defaults = DEFAULT_CHROME[surface]
            for key, allowed in (("visibility", TOP_BAR_MODES), ("position", ("top", "bottom")),
                                 ("hide_when", TOP_BAR_HIDE_WHEN),
                                 ("reveal_method", TOP_BAR_REVEAL_METHODS),
                                 ("sensitivity", TOP_BAR_SENSITIVITIES)):
                if merged.get(key) not in allowed:
                    merged[key] = defaults[key]
            height = merged.get("height")
            merged["height"] = max(24, min(64, round(height))) \
                if _is_number(height) else defaults["height"]
            for key, (low, high) in TOP_BAR_TIMINGS.items():
                value = merged.get(key)
                merged[key] = max(low, min(high, float(value))) \
                    if _is_number(value) else defaults[key]
            if not isinstance(merged.get("reveal_in_fullscreen"), bool):
                merged["reveal_in_fullscreen"] = defaults["reveal_in_fullscreen"]
        return merged

    def desktop_icon_options(self) -> dict[str, Any]:
        """Return safe desktop-icon material options with defaults filled."""
        merged = copy.deepcopy(DEFAULT_DESKTOP_ICONS)
        if isinstance(self.desktop_icons, dict):
            merged.update(self.desktop_icons)
        if merged.get("material") not in ("system", "solid", "frosted", "liquid"):
            merged["material"] = DEFAULT_DESKTOP_ICONS["material"]
        if merged.get("artwork") not in ("original", "monochrome"):
            merged["artwork"] = DEFAULT_DESKTOP_ICONS["artwork"]
        merged["foreground_mode"] = (
            "custom" if merged.get("foreground_mode") == "custom" else "auto"
        )
        opacity = merged.get("opacity")
        merged["opacity"] = max(0.0, min(1.0, float(opacity))) \
            if isinstance(opacity, (int, float)) else DEFAULT_DESKTOP_ICONS["opacity"]
        for key in ("tint", "foreground"):
            if not (
                isinstance(merged.get(key), str)
                and re.fullmatch(r"#[0-9a-fA-F]{6}", merged[key])
            ):
                merged[key] = DEFAULT_DESKTOP_ICONS[key]
        return merged

    def active_providers(self) -> set[str]:
        return {
            w.provider for w in self.widgets if w.enabled and w.provider is not None
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "widgets": [asdict(w) for w in self.widgets],
            "providers": self.providers,
            "style": self.style,
            "chrome": self.chrome,
            "desktop_icons": self.desktop_icons,
            "edit_layout": self.edit_layout,
            "layouts": self.layouts,
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def default_config() -> Config:
    """A first-run desktop that is worth looking at without any setup.

    Clock and system monitor need no network and no credentials, so a brand
    new install shows something real immediately; weather and stocks are added
    by the user once they've set a location and symbols.
    """
    return Config(
        widgets=[
            Widget(type="clock", x=60, y=60),
            Widget(type="system", x=60, y=220),
        ],
        providers={},
        style={},
    )


def load() -> Config:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return default_config()
    except (OSError, json.JSONDecodeError):
        # A corrupt config must not leave the user with no desktop at all.
        return default_config()

    widgets = []
    for item in raw.get("widgets", []):
        if not isinstance(item, dict) or item.get("type") not in WIDGET_TYPES:
            continue
        known = {f: item[f] for f in Widget.__dataclass_fields__ if f in item}
        widgets.append(Widget(**known))

    providers = raw.get("providers", {}) or {}
    if raw.get("version", 1) in (1, 2):
        news = providers.setdefault("news", {})
        # Before v3 an empty checklist meant unrestricted news. Preserve that
        # intent once; subsequent empty v3 selections really turn news off.
        if not news.get("topic_presets"):
            news["topic_presets"] = list(NEWS_TOPIC_IDS)

    return Config(
        # Loading is the migration boundary. Unknown legacy widget fields such
        # as always_on_top were already discarded above; the next save writes
        # the normalized current shape.
        version=SCHEMA_VERSION,
        widgets=widgets,
        providers=providers,
        style=raw.get("style", {}) or {},
        chrome=raw.get("chrome", {}) if isinstance(raw.get("chrome", {}), dict) else {},
        desktop_icons=(raw.get("desktop_icons", {})
                       if isinstance(raw.get("desktop_icons", {}), dict) else {}),
        edit_layout=bool(raw.get("edit_layout", False)),
        layouts=_clean_layouts(raw.get("layouts")),
    )


# -- saved widget layouts ------------------------------------------------------

LAYOUT_NAME_LIMIT = 40
LAYOUT_LIMIT = 20
_PLACE_FIELDS = ("monitor", "x", "y", "width", "height")


def _clean_layouts(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """Saved layouts from the file, dropping anything malformed."""
    if not isinstance(raw, dict):
        return {}
    layouts = {}
    for name, entries in raw.items():
        if len(layouts) >= LAYOUT_LIMIT:
            break
        if not isinstance(name, str) or not name.strip() or len(name) > LAYOUT_NAME_LIMIT:
            continue
        if not isinstance(entries, list):
            continue
        clean = []
        for entry in entries:
            if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                    and all(isinstance(entry.get(f), int) and not isinstance(entry.get(f), bool)
                            for f in _PLACE_FIELDS)):
                clean.append({"id": entry["id"], "enabled": entry.get("enabled") is not False,
                              **{f: entry[f] for f in _PLACE_FIELDS}})
        layouts[name] = clean
    return layouts


def save_layout(config: Config, name: str) -> None:
    """Remember where every widget is now, and which are shown, as `name`."""
    name = name.strip()[:LAYOUT_NAME_LIMIT]
    if not name:
        raise ValueError("A layout needs a name")
    if name not in config.layouts and len(config.layouts) >= LAYOUT_LIMIT:
        raise ValueError(f"At most {LAYOUT_LIMIT} layouts can be saved")
    config.layouts[name] = [{"id": w.id, "enabled": w.enabled, **{f: getattr(w, f) for f in _PLACE_FIELDS}}
                            for w in config.widgets]


def apply_layout(config: Config, name: str) -> bool:
    """Put widgets back where layout `name` had them. Widgets added since
    keep their place; widgets removed since are ignored."""
    entries = config.layouts.get(name)
    if entries is None:
        return False
    by_id = {entry["id"]: entry for entry in entries}
    for widget in config.widgets:
        entry = by_id.get(widget.id)
        if entry:
            widget.enabled = entry["enabled"]
            for f in _PLACE_FIELDS:
                setattr(widget, f, entry[f])
    return True


def delete_layout(config: Config, name: str) -> None:
    config.layouts.pop(name, None)


def save(config: Config) -> None:
    write_json(CONFIG_PATH, config.to_dict())


def migrate() -> None:
    """Persist an existing legacy config before the upgraded service starts."""
    raw = read_json(CONFIG_PATH)
    if not raw or raw.get("version", 1) >= SCHEMA_VERSION:
        return
    # Keep a one-time backup, including any fields removed by migration.
    try:
        with open(f"{CONFIG_PATH}.pre-v5", "x", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=2)
    except FileExistsError:
        pass
    save(load())


# Read once, at import, while nothing else can be running: os.umask() can only
# be queried by setting it, which is not safe to do from worker threads.
_UMASK = os.umask(0)
os.umask(_UMASK)


def write_json(path: str, payload: dict[str, Any]) -> None:
    """Write JSON atomically.

    The extension watches these files. A reader that catches a half-written
    file sees invalid JSON and blanks the widget, so every write goes to a
    temp file in the same directory and is renamed into place -- rename is
    atomic within a filesystem, so a reader sees either the old file or the
    new one, never a partial one.

    The temp file is unique per call, not per process: the CLIVE service
    writes from several request threads, and two writers sharing one temp
    name interleave their bytes into a corrupt file.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            mode = 0o666 & ~_UMASK
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
