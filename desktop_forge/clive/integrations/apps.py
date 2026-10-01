"""Every installed graphical application, each its own App Access switch.

The tools are shared -- one app_launch serves every app -- so the arguments
name the integration: app:<desktop id>. Terminal applications, the Shell,
authentication and lock-screen surfaces are never offered at all.
"""
from __future__ import annotations

from .base import Capability, Integration, Tool
from .registry import APP_PREFIX, app_integration_id
from .schemas import COORD, INPUT_TEXT, NAVIGATION_KEYS, SHORTCUT_KEY, STRING, TEXT

APP_CAPABILITIES = (
    Capability("open", "Open and switch to it", "execute", "normal"),
    Capability("inspect", "Read its controls", "read", "low",
               "Through GNOME accessibility, when the app provides it."),
    # Not "low" on purpose: a screenshot is a read, but it sends screen pixels
    # to a possibly-cloud model and asks GNOME for a screen share.
    Capability("screenshot", "See its window", "read", "normal",
               "Screenshots of this app's window only, sent to the active model."),
    Capability("operate", "Click, type and use shortcuts", "execute", "normal"),
)

# Desktop-file categories -> App Access category.
CATEGORY_MAP = (
    (("Email", "InstantMessaging", "Chat", "Telephony", "VideoConference", "ContactManagement"),
     "communication"),
    (("Office", "Calendar", "ProjectManagement", "WordProcessor", "Spreadsheet", "Presentation"),
     "productivity"),
    (("FileManager", "FileTools", "Archiving", "Filesystem"), "files"),
    (("WebBrowser",), "web"),
    (("AudioVideo", "Audio", "Video", "Player", "Music", "Recorder"), "media"),
    (("Development", "IDE", "Building", "Debugger", "RevisionControl"), "development"),
    (("System", "Settings", "Monitor", "Utility", "Accessibility"), "system"),
)


def category_for(categories: tuple[str, ...]) -> str:
    for names, category in CATEGORY_MAP:
        if set(names).intersection(categories):
            return category
    return "apps"


def app_integrations(apps_factory):
    """Build one integration per installed app, from the Gio app list."""
    from . import media
    result = []
    for app in apps_factory():
        categories = tuple(c for c in str(app.get("categories", "")).split(";") if c)
        # Players and browsers can also be controlled through MPRIS, as extra
        # capabilities of the same switch.
        capabilities = APP_CAPABILITIES + (media.CAPABILITIES if media.is_media_app(categories) else ())
        result.append(Integration(
            app_integration_id(app["desktop_id"]), app.get("name") or app["desktop_id"],
            category_for(categories), app.get("icon") or "application-x-executable-symbolic",
            app.get("description") or "", capabilities, instance_of="app",
            desktop_id=app["desktop_id"], app_categories=categories))
    return result


def _app(a):
    return app_integration_id(a.get("desktop_id") or a.get("app") or "")


def _list(ctx, _a):
    enabled = {i.desktop_id for i in ctx.registry.enabled_apps()}
    installed = [app for app in ctx.apps() if app["desktop_id"] in enabled]
    running = [row for row in ctx.desktop.running_apps() if row.get("desktop_id") in enabled]
    return {"installed": installed, "running": running}


def _launch(ctx, a):
    from ..tools import controllable_app
    app = controllable_app(a["desktop_id"])
    if not app.launch([], None):
        raise RuntimeError("Application could not be launched")
    return {"launched": a["desktop_id"], "ready": ctx.desktop.wait_for_app(a["desktop_id"], timeout=10)}


def _focus(ctx, a):
    from ..tools import controllable_app
    controllable_app(a["desktop_id"])
    return ctx.desktop.focus(a["desktop_id"])


def _desktop(operation):
    def handler(ctx, a):
        from ..tools import controllable_app
        controllable_app(a["app"])
        return ctx.desktop.call(operation, **a)
    return handler


def _named(a):
    return a.get("desktop_id") or a.get("app") or ""


TOOLS = (
    Tool("apps_list", "List the graphical applications CLIVE may use, and their running windows, by stable desktop ID.",
         {}, "inspect", _list, "", "Listing apps…"),
    Tool("app_launch", "Launch an installed graphical app by desktop ID and wait for its window; terminal apps are excluded.",
         {"desktop_id": STRING}, "open", _launch, _app, "Opening {app}…", describe=_named),
    Tool("app_focus", "Focus a running approved graphical app by desktop ID.", {"desktop_id": STRING},
         "open", _focus, _app, "Switching to {app}…", describe=_named),
    Tool("desktop_inspect", "Read an app's accessibility tree by desktop ID. Returns availability, temporary element IDs and actions, or a recoverable screenshot fallback.",
         {"app": STRING}, "inspect", _desktop("inspect"), _app, "Reading {app}…", describe=_named),
    Tool("desktop_action", "Invoke an action named in the most recent accessibility tree. Inspect again after each action.",
         {"app": STRING, "element": STRING, "action": STRING}, "operate", _desktop("action"), _app,
         "Using {app}…", describe=lambda a: f"{a['action']} in {a['app']}"),
    Tool("desktop_type", "Set text in an editable accessibility element. Inspect again afterwards.",
         {"app": STRING, "element": STRING, "text": TEXT}, "operate", _desktop("type"), _app,
         "Typing in {app}…", describe=_named),
    Tool("desktop_screenshot", "Capture the specified app's window while it is active. Returns a transient image and screenshot ID.",
         {"app": STRING}, "screenshot", _desktop("screenshot"), _app, "Looking at {app}…", describe=_named),
    Tool("desktop_click", "Click a point from a recent screenshot of the active app. Coordinates are 0–1000 relative to that image.",
         {"app": STRING, "screenshot": STRING, "x": COORD, "y": COORD}, "operate", _desktop("click"), _app,
         "Clicking in {app}…", describe=_named),
    Tool("desktop_scroll", "Scroll in the active app following a screenshot.",
         {"app": STRING, "screenshot": STRING, "dy": {"type": "number", "minimum": -1000, "maximum": 1000}},
         "operate", _desktop("scroll"), _app, "Scrolling in {app}…", describe=_named),
    Tool("desktop_key", "Press one supported navigation key in the focused approved app.",
         {"app": STRING, "key": {"type": "string", "enum": NAVIGATION_KEYS}}, "operate",
         _desktop("key"), _app, "Pressing a key in {app}…", describe=lambda a: f"{a['key']} in {a['app']}"),
    Tool("desktop_type_text", "Type Unicode text into the focused approved app using GNOME's consented input session.",
         {"app": STRING, "text": INPUT_TEXT}, "operate", _desktop("type_text"), _app,
         "Typing in {app}…", describe=_named),
    Tool("desktop_shortcut", "Press a shortcut in the focused approved app. key is one character or a supported navigation key.",
         {"app": STRING, "key": SHORTCUT_KEY,
          "modifiers": {"type": "array", "items": {"type": "string", "enum": ["Control", "Alt", "Shift", "Super"]},
                        "uniqueItems": True, "maxItems": 4}},
         "operate", _desktop("shortcut"), _app, "Using a shortcut in {app}…", required=["app", "key"],
         describe=lambda a: "+".join([*a.get("modifiers", []), a["key"]]) + f" in {a['app']}"),
)

__all__ = ["APP_CAPABILITIES", "APP_PREFIX", "TOOLS", "app_integrations", "category_for"]
