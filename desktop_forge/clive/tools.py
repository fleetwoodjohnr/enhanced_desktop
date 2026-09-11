"""Small, typed tools; there is deliberately no command execution tool."""
from __future__ import annotations

import datetime
import os
import stat
import threading
from pathlib import Path

from .models import Cancelled
from .policy import check_scope, local_path

STRING = {"type": "string", "minLength": 1, "maxLength": 2000}
TEXT = {"type": "string", "maxLength": 50000}
INPUT_TEXT = {"type": "string", "minLength": 1, "maxLength": 8000}
COORD = {"type": "number", "minimum": 0, "maximum": 1000}
NAVIGATION_KEYS = ["Tab", "Return", "Escape", "BackSpace", "Delete", "Home", "End",
                   "PageUp", "PageDown", "Left", "Right", "Up", "Down"]
SHORTCUT_KEY = {"anyOf": [
    {"type": "string", "enum": NAVIGATION_KEYS},
    {"type": "string", "minLength": 1, "maxLength": 1},
]}


def spec(name, description, properties, required=None):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": properties, "required": list(properties) if required is None else required}}}


SPECS = [
    spec("web_search", "Search the live web. Cite the returned URLs.", {"query": STRING}),
    spec("web_fetch", "Read a public web page.", {"url": STRING}),
    spec("apps_list", "List installed graphical applications and running windows by stable desktop ID.", {}),
    spec("app_launch", "Launch an installed graphical app by desktop ID and wait for its window; terminal apps are excluded.", {"desktop_id": STRING}),
    spec("app_focus", "Focus a running approved graphical app by desktop ID.", {"desktop_id": STRING}),
    spec("open_url", "Open a public URL in the default browser.", {"url": STRING}),
    spec("file_search", "Find visible files by case-insensitive filename within a directory. Maximum 100 results.", {"directory": STRING, "query": STRING}),
    spec("file_read", "Read a UTF-8 text file, at most 50 KB.", {"path": STRING}),
    spec("file_write", "Create a new UTF-8 file. Never overwrites an existing file.", {"path": STRING, "text": TEXT}),
    spec("file_move", "Move a file without overwriting the destination.", {"source": STRING, "destination": STRING}),
    spec("file_trash", "Move a file to the desktop trash.", {"path": STRING}),
    spec("todos_list", "Read the existing To-Do list.", {}),
    spec("todo_add", "Add a To-Do item.", {"text": STRING}),
    spec("todo_update", "Update an existing To-Do item.", {"id": STRING, "text": STRING,
        "status": {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]}}, ["id"]),
    spec("reminders_list", "Read existing reminders.", {}),
    spec("reminder_add", "Create a reminder. due is local ISO date/time, repeat is none/daily/weekly/monthly.",
         {"text": STRING, "due": STRING, "repeat": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]}}, ["text", "due"]),
    spec("reminder_complete", "Mark a reminder complete.", {"id": STRING}),
    spec("desktop_inspect", "Read an app's accessibility tree by desktop ID. Returns availability, temporary element IDs and actions, or a recoverable screenshot fallback.", {"app": STRING}),
    spec("desktop_action", "Invoke an action named in the most recent accessibility tree. Inspect again after each action.", {"app": STRING, "element": STRING, "action": STRING}),
    spec("desktop_type", "Set text in an editable accessibility element. Inspect again afterwards.", {"app": STRING, "element": STRING, "text": TEXT}),
    spec("desktop_screenshot", "Capture the shared monitor while the specified app is active. Returns a transient image and screenshot ID.", {"app": STRING}),
    spec("desktop_click", "Click a point from a recent screenshot of the active app. Coordinates are 0–1000 relative to that image.",
         {"app": STRING, "screenshot": STRING, "x": COORD, "y": COORD}),
    spec("desktop_scroll", "Scroll in the active app following a screenshot.", {"app": STRING, "screenshot": STRING,
         "dy": {"type": "number", "minimum": -1000, "maximum": 1000}}),
    spec("desktop_key", "Press one supported navigation key in the focused approved app.",
         {"app": STRING, "key": {"type": "string", "enum": NAVIGATION_KEYS}}),
    spec("desktop_type_text", "Type Unicode text into the focused approved app using GNOME's consented input session.",
         {"app": STRING, "text": INPUT_TEXT}),
    spec("desktop_shortcut", "Press a shortcut in the focused approved app. key is one character or a supported navigation key.",
         {"app": STRING,
          "key": SHORTCUT_KEY,
          "modifiers": {"type": "array", "items": {"type": "string",
              "enum": ["Control", "Alt", "Shift", "Super"]}, "uniqueItems": True, "maxItems": 4}},
         ["app", "key"]),
]
BY_NAME = {s["function"]["name"]: s for s in SPECS}
# Tools that only observe. Everything absent from this set writes a file, moves
# or trashes one, launches or drives an application, or changes a list -- so the
# read-only approval mode still stops for them. desktop_screenshot is left out
# on purpose: it is a read, but it sends screen pixels to a possibly-cloud model
# and asks GNOME for a screen share, which is not something to do unattended.
READ_ONLY = frozenset({"web_search", "web_fetch", "apps_list", "file_search",
                       "file_read", "todos_list", "reminders_list", "desktop_inspect"})


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
        result.append({
            "desktop_id": desktop_id,
            "name": app.get_display_name() or desktop_id,
            "description": app.get_description() or "",
            "categories": app.get_categories() or "",
            "startup_wm_class": app.get_startup_wm_class() or "",
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
    def __init__(self, models, desktop, cancel: threading.Event):
        self.models, self.desktop, self.cancel = models, desktop, cancel

    def validate(self, name: str, arguments: dict, plan: dict):
        from jsonschema import ValidationError, validate
        if name not in BY_NAME:
            raise InvalidTool("Unknown tool. Use a listed tool or reply normally if finished.")
        try:
            validate(arguments, BY_NAME[name]["function"]["parameters"])
        except ValidationError:
            raise InvalidTool(f"Arguments for {name} do not match its schema.") from None
        check_scope(name, arguments, plan)

    def call(self, name: str, arguments: dict, plan: dict) -> dict:
        self.validate(name, arguments, plan)
        if self.cancel.is_set():
            raise Cancelled()
        from gi.repository import Gio
        from ..providers import reminders, todos
        a = arguments
        if name.startswith("web_"):
            return self.models.web(name, a)
        if name == "apps_list":
            return {"installed": installed_apps(), "running": self.desktop.running_apps()}
        if name == "app_launch":
            app = controllable_app(a["desktop_id"])
            if not app.launch([], None):
                raise RuntimeError("Application could not be launched")
            return {"launched": a["desktop_id"],
                    "ready": self.desktop.wait_for_app(a["desktop_id"], timeout=10)}
        if name == "app_focus":
            controllable_app(a["desktop_id"])
            return self.desktop.focus(a["desktop_id"])
        if name == "open_url":
            Gio.AppInfo.launch_default_for_uri(a["url"], None)
            return {"opened": a["url"]}
        if name == "file_search":
            root = local_path(a["directory"])
            found, visited = [], 0
            for directory, dirs, files in os.walk(root, followlinks=False):
                if self.cancel.is_set():
                    raise Cancelled()
                dirs[:] = [d for d in dirs if not d.startswith(".") and not Path(directory, d).is_symlink()]
                for filename in files:
                    visited += 1
                    path = Path(directory, filename)
                    if not filename.startswith(".") and not path.is_symlink() and a["query"].casefold() in filename.casefold():
                        found.append(str(path))
                    if len(found) >= 100 or visited >= 25000:
                        return {"paths": found, "truncated": True}
            return {"paths": found, "truncated": False}
        if name == "file_read":
            path = local_path(a["path"])
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("Only regular files can be read")
                data = handle.read(50001)
            # Decode before truncating so a split UTF-8 sequence at the size
            # boundary does not make an otherwise valid text file unreadable.
            text = data.decode("utf-8", errors="replace")
            return {"path": str(path), "text": text[:50000], "truncated": len(data) > 50000}
        if name == "file_write":
            path = local_path(a["path"])
            with path.open("x", encoding="utf-8") as handle:
                handle.write(a["text"])
            return {"created": str(path), "bytes": path.stat().st_size}
        if name == "file_move":
            source, destination = local_path(a["source"]), local_path(a["destination"])
            if not source.is_file():
                raise ValueError("Only regular files can be moved")
            Gio.File.new_for_path(str(source)).move(Gio.File.new_for_path(str(destination)),
                                                   Gio.FileCopyFlags.NONE, None, None)
            return {"moved": str(destination), "verified": destination.is_file() and not source.exists()}
        if name == "file_trash":
            path = local_path(a["path"])
            if not path.is_file():
                raise ValueError("Only regular files can be trashed")
            if not Gio.File.new_for_path(str(path)).trash(None):
                raise RuntimeError("Could not move the file to Trash")
            return {"trashed": str(path), "verified": not path.exists()}
        if name == "todos_list":
            return {"items": todos.load()}
        if name == "todo_add":
            return todos.add(a["text"])
        if name == "todo_update":
            if not todos.update(a["id"], **{k: v for k, v in a.items() if k != "id"}):
                raise ValueError("To-Do item no longer exists")
            return {"updated": a["id"]}
        if name == "reminders_list":
            return {"reminders": reminders.load()}
        if name == "reminder_add":
            due = datetime.datetime.fromisoformat(a["due"])
            if due.tzinfo:
                due = due.astimezone().replace(tzinfo=None)
            return reminders.add(a["text"], due, a.get("repeat", "none"))
        if name == "reminder_complete":
            if not any(r.get("id") == a["id"] for r in reminders.load()):
                raise ValueError("Reminder no longer exists")
            reminders.update(a["id"], done=True)
            return {"completed": a["id"]}
        if name.startswith("desktop_"):
            controllable_app(a["app"])
            operation = {
                "desktop_type_text": "type_text",
                "desktop_shortcut": "shortcut",
            }.get(name, name.removeprefix("desktop_"))
            return self.desktop.call(operation, **a)
        raise ValueError("Unknown tool")
