"""The tool surface CLIVE's agent calls. Definitions live with their integrations.

Every call is checked twice, in this order: App Access (is this app, and this
capability of it, switched on right now?) and then the approved task scope.
The first can only refuse; the second may widen the task, visibly. Nothing a
user switched off is ever widened past.
"""
from __future__ import annotations

import threading

from .integrations import ALL_TOOLS, declared_risk
from .models import Cancelled
from .policy import check_scope

TOOLS = {tool.name: tool for tool in ALL_TOOLS}
SPECS = [tool.spec() for tool in ALL_TOOLS]
BY_NAME = {spec["function"]["name"]: spec for spec in SPECS}
# Tools that only observe. Everything absent from this set writes a file, moves
# or trashes one, launches or drives an application, or changes a list -- so the
# read-only approval mode still stops for them. desktop_screenshot is left out
# on purpose: it is a read, but it sends screen pixels to a possibly-cloud model
# and asks GNOME for a screen share, which is not something to do unattended.
READ_ONLY = frozenset(tool.name for tool in ALL_TOOLS if declared_risk(tool) == "low")


class InvalidTool(ValueError):
    """A model formatting error detected before any action has run."""


def installed_apps():
    from gi.repository import Gio
    result, seen = [], set()
    for app in Gio.AppInfo.get_all():
        if (not isinstance(app, Gio.DesktopAppInfo) or not app.should_show() or
                terminal_app(app) or blocked_surface(app.get_id() or "")):
            continue
        desktop_id = app.get_id()
        if not desktop_id or desktop_id in seen:
            continue
        seen.add(desktop_id)
        icon = app.get_icon()
        result.append({
            "desktop_id": desktop_id,
            "name": app.get_display_name() or desktop_id,
            "description": app.get_description() or "",
            "categories": app.get_categories() or "",
            "startup_wm_class": app.get_startup_wm_class() or "",
            "icon": icon.to_string() if icon else "",
        })
    return sorted(result, key=lambda a: a["name"].casefold())


def terminal_app(app):
    categories = app.get_categories() or ""
    return "TerminalEmulator" in categories or app.get_boolean("Terminal")


def blocked_surface(desktop_id):
    lowered = desktop_id.casefold()
    return any(term in lowered for term in (
        "gnome-shell", "org.gnome.shell", "polkit", "authentication-agent",
        "screen-lock", "screenshield"
    ))


def controllable_app(desktop_id):
    """Resolve a user-facing graphical app and reject privileged surfaces."""
    from gi.repository import Gio
    if blocked_surface(desktop_id):
        raise ValueError("Authentication and lock-screen surfaces are unavailable")
    try:
        app = Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:
        app = None
    if not app or not app.should_show() or terminal_app(app):
        raise ValueError("Application is unavailable or is a terminal")
    return app


class Tools:
    """What a tool handler receives: the model transport, the desktop, the switches."""

    def __init__(self, models, desktop, cancel: threading.Event, registry, apps=installed_apps):
        self.models, self.desktop, self.cancel = models, desktop, cancel
        self.registry = registry
        self.apps = apps

    def _window_titles(self, desktop_id):
        titles = getattr(self.desktop, "window_titles", None)
        return titles(desktop_id) if callable(titles) else []

    def validate(self, name: str, arguments: dict, plan: dict):
        from jsonschema import ValidationError, validate
        if name not in TOOLS:
            raise InvalidTool("Unknown tool. Use a listed tool or reply normally if finished.")
        try:
            validate(arguments, BY_NAME[name]["function"]["parameters"])
        except ValidationError:
            raise InvalidTool(f"Arguments for {name} do not match its schema.") from None
        # App Access first: a switched-off app must be refused, never turned
        # into a scope change that unattended mode would approve silently.
        self.registry.check(name, arguments, self._window_titles)
        check_scope(name, arguments, plan)

    def call(self, name: str, arguments: dict, plan: dict) -> dict:
        self.validate(name, arguments, plan)
        if self.cancel.is_set():
            raise Cancelled()
        return TOOLS[name].handler(self, arguments)
