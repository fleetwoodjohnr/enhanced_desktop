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
COORD = {"type": "number", "minimum": 0, "maximum": 1000}


def spec(name, description, properties, required=None):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "additionalProperties": False,
                       "properties": properties, "required": list(properties) if required is None else required}}}


SPECS = [
    spec("web_search", "Search the live web. Cite the returned URLs.", {"query": STRING}),
    spec("web_fetch", "Read a public web page.", {"url": STRING}),
    spec("apps_list", "List installed applications, desktop IDs, and running accessibility names.", {}),
    spec("app_launch", "Launch an installed app by its desktop ID; terminal apps are excluded.", {"desktop_id": STRING}),
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
    spec("desktop_inspect", "Read the accessibility tree of a running app. Returns temporary element IDs and available actions.", {"app": STRING}),
    spec("desktop_action", "Invoke an action named in the most recent accessibility tree. Inspect again after each action.", {"app": STRING, "element": STRING, "action": STRING}),
    spec("desktop_type", "Set text in an editable accessibility element. Inspect again afterwards.", {"app": STRING, "element": STRING, "text": TEXT}),
    spec("desktop_screenshot", "Capture the shared monitor while the specified app is active. Returns a transient image and screenshot ID.", {"app": STRING}),
    spec("desktop_click", "Click a point from a recent screenshot of the active app. Coordinates are 0–1000 relative to that image.",
         {"app": STRING, "screenshot": STRING, "x": COORD, "y": COORD}),
    spec("desktop_scroll", "Scroll in the active app following a screenshot.", {"app": STRING, "screenshot": STRING,
         "dy": {"type": "number", "minimum": -1000, "maximum": 1000}}),
    spec("desktop_key", "Press a navigation key in the active app: Tab, Return, Escape, BackSpace, or arrow keys.",
         {"app": STRING, "key": {"type": "string", "enum": ["Tab", "Return", "Escape", "BackSpace", "Left", "Right", "Up", "Down"]}}),
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
    result = []
    for app in Gio.AppInfo.get_all():
        if not isinstance(app, Gio.DesktopAppInfo) or not app.should_show() or terminal_app(app):
            continue
        result.append({"desktop_id": app.get_id(), "name": app.get_display_name()})
    return sorted(result, key=lambda a: a["name"].casefold())


def terminal_app(app):
    categories = app.get_categories() or ""
    return "TerminalEmulator" in categories or app.get_boolean("Terminal")


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
            return {"installed": installed_apps(), "running": self.desktop.app_names()}
        if name == "app_launch":
            app = Gio.DesktopAppInfo.new(a["desktop_id"])
            if not app or terminal_app(app):
                raise ValueError("Application is unavailable or is a terminal")
            if not app.launch([], None):
                raise RuntimeError("Application could not be launched")
            return {"launched": a["desktop_id"]}
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
            return self.desktop.call(name.removeprefix("desktop_"), **a)
        raise ValueError("Unknown tool")
