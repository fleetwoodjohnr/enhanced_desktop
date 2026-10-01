"""The desktop's own To-Do list and reminders."""
from __future__ import annotations

import datetime

from .base import Capability, Integration, Tool
from .schemas import STRING

ID = "tasks"

CAPABILITIES = (
    Capability("read", "See tasks and reminders", "read", "low"),
    Capability("create", "Add tasks and reminders", "create", "normal"),
    Capability("update", "Update and complete them", "modify", "normal"),
    Capability("delete", "Delete tasks and reminders", "delete", "high", default=False),
)


def integration() -> Integration:
    return Integration(ID, "To-Do & Reminders", "productivity", "view-list-bullet-symbolic",
                       "The To-Do list and reminders shown on your desktop.",
                       CAPABILITIES, default_enabled=True)


def _providers():
    from ...providers import reminders, todos
    return reminders, todos


def _todos(_ctx, _a):
    return {"items": _providers()[1].load()}


def _todo_add(_ctx, a):
    return _providers()[1].add(a["text"])


def _todo_update(_ctx, a):
    if not _providers()[1].update(a["id"], **{k: v for k, v in a.items() if k != "id"}):
        raise ValueError("To-Do item no longer exists")
    return {"updated": a["id"]}


def _todo_delete(_ctx, a):
    if not _providers()[1].remove(a["id"]):
        raise ValueError("To-Do item no longer exists")
    return {"deleted": a["id"]}


def _reminders(_ctx, _a):
    return {"reminders": _providers()[0].load()}


def _reminder_add(_ctx, a):
    due = datetime.datetime.fromisoformat(a["due"])
    if due.tzinfo:
        due = due.astimezone().replace(tzinfo=None)
    return _providers()[0].add(a["text"], due, a.get("repeat", "none"))


def _reminder_complete(_ctx, a):
    reminders = _providers()[0]
    if not any(r.get("id") == a["id"] for r in reminders.load()):
        raise ValueError("Reminder no longer exists")
    reminders.update(a["id"], done=True)
    return {"completed": a["id"]}


def _reminder_delete(_ctx, a):
    reminders = _providers()[0]
    if not any(r.get("id") == a["id"] for r in reminders.load()):
        raise ValueError("Reminder no longer exists")
    reminders.remove(a["id"])
    return {"deleted": a["id"]}


STATUS = {"type": "string", "enum": ["todo", "in_progress", "blocked", "done"]}
REPEAT = {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]}

TOOLS = (
    Tool("todos_list", "Read the existing To-Do list.", {}, "read", _todos, ID, "Reading To-Dos…"),
    Tool("todo_add", "Add a To-Do item.", {"text": STRING}, "create", _todo_add, ID,
         "Adding a To-Do…", describe=lambda a: a["text"]),
    Tool("todo_update", "Update an existing To-Do item.", {"id": STRING, "text": STRING, "status": STATUS},
         "update", _todo_update, ID, "Updating a To-Do…", required=["id"],
         describe=lambda a: a.get("text") or a.get("status") or a["id"]),
    Tool("todo_delete", "Delete a To-Do item.", {"id": STRING}, "delete", _todo_delete, ID,
         "Deleting a To-Do…", describe=lambda a: a["id"]),
    Tool("reminders_list", "Read existing reminders.", {}, "read", _reminders, ID, "Reading reminders…"),
    Tool("reminder_add", "Create a reminder. due is local ISO date/time, repeat is none/daily/weekly/monthly.",
         {"text": STRING, "due": STRING, "repeat": REPEAT}, "create", _reminder_add, ID,
         "Creating a reminder…", required=["text", "due"],
         describe=lambda a: f"{a['text']} at {a['due']}"),
    Tool("reminder_complete", "Mark a reminder complete.", {"id": STRING}, "update",
         _reminder_complete, ID, "Completing a reminder…", describe=lambda a: a["id"]),
    Tool("reminder_delete", "Delete a reminder.", {"id": STRING}, "delete", _reminder_delete, ID,
         "Deleting a reminder…", describe=lambda a: a["id"]),
)
