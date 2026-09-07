"""Reminders, stored by this app rather than in EDS.

EDS has task lists, but none are configured here and creating one requires a
backend choice the user hasn't made. A local JSON store keeps reminders
working out of the box; the file is small, human-readable, and equally
readable from the daemon and the GUI.
"""
from __future__ import annotations

import datetime
import os
import uuid
from typing import Any

from .. import config
from .base import Provider

STORE_PATH = os.path.join(config.DATA_DIR, "reminders.json")
REPEATS = ("none", "daily", "weekly", "monthly")


def _now() -> datetime.datetime:
    return datetime.datetime.now().replace(microsecond=0)


def load() -> list[dict[str, Any]]:
    payload = config.read_json(STORE_PATH) or {}
    items = payload.get("reminders")
    return items if isinstance(items, list) else []


def save(reminders: list[dict[str, Any]]) -> None:
    config.write_json(STORE_PATH, {"version": 1, "reminders": reminders})


def add(text: str, due: datetime.datetime, repeat: str = "none") -> dict[str, Any]:
    reminder = {
        "id": uuid.uuid4().hex[:12],
        "text": text.strip(),
        "due": due.replace(microsecond=0).isoformat(),
        "repeat": repeat if repeat in REPEATS else "none",
        "done": False,
        "notified": False,
    }
    reminders = load()
    reminders.append(reminder)
    save(reminders)
    return reminder


def update(reminder_id: str, **changes: Any) -> None:
    reminders = load()
    for reminder in reminders:
        if reminder.get("id") == reminder_id:
            reminder.update(changes)
            break
    save(reminders)


def remove(reminder_id: str) -> None:
    save([r for r in load() if r.get("id") != reminder_id])


def advance(reminder: dict[str, Any]) -> bool:
    """Roll a repeating reminder forward past now. Returns False for one-offs.

    Stepping in a loop rather than adding a single interval matters when the
    machine has been asleep: a daily reminder that missed four days should
    land on the next real occurrence, not four days in the past, and should
    fire once rather than four times.
    """
    repeat = reminder.get("repeat", "none")
    if repeat == "none":
        return False

    due = parse_due(reminder)
    if due is None:
        return False

    step = {
        "daily": datetime.timedelta(days=1),
        "weekly": datetime.timedelta(weeks=1),
        "monthly": datetime.timedelta(days=30),
    }.get(repeat)
    if step is None:
        return False

    now = _now()
    while due <= now:
        due += step

    reminder["due"] = due.isoformat()
    reminder["notified"] = False
    reminder["done"] = False
    return True


def parse_due(reminder: dict[str, Any]) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(reminder["due"])
    except (KeyError, TypeError, ValueError):
        return None


def due_now() -> list[dict[str, Any]]:
    """Reminders that have come due and not yet been notified."""
    now = _now()
    ready = []
    for reminder in load():
        if reminder.get("done") or reminder.get("notified"):
            continue
        due = parse_due(reminder)
        if due is not None and due <= now:
            ready.append(reminder)
    return ready


class RemindersProvider(Provider):
    name = "reminders"

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        items = []
        for reminder in load():
            if reminder.get("done"):
                continue
            due = parse_due(reminder)
            items.append(
                {
                    "id": reminder.get("id"),
                    "text": reminder.get("text", ""),
                    "due": reminder.get("due"),
                    "repeat": reminder.get("repeat", "none"),
                    "overdue": bool(due and due < now),
                    "due_in_seconds": int((due - now).total_seconds()) if due else None,
                }
            )

        items.sort(key=lambda r: r["due"] or "")
        return {
            "reminders": items,
            "overdue": sum(1 for r in items if r["overdue"]),
            "total": len(items),
        }
