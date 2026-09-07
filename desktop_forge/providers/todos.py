"""Persistent, manually ordered To-Do items for the desktop widget."""
from __future__ import annotations

import os
import uuid
from typing import Any

from .. import config
from .base import Provider

STORE_PATH = os.path.join(config.DATA_DIR, "todos.json")
STATUSES = ("todo", "in_progress", "blocked", "done")


def load() -> list[dict[str, Any]]:
    payload = config.read_json(STORE_PATH) or {}
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def save(items: list[dict[str, Any]]) -> None:
    config.write_json(STORE_PATH, {"version": 1, "items": items})


def add(text: str) -> dict[str, Any]:
    clean = text.strip()
    if not clean:
        raise ValueError("To-Do text cannot be empty")
    item = {
        "id": uuid.uuid4().hex[:12],
        "text": clean,
        "status": "todo",
    }
    items = load()
    items.append(item)
    save(items)
    return item


def update(item_id: str, **changes: Any) -> bool:
    items = load()
    item = next((candidate for candidate in items if candidate.get("id") == item_id), None)
    if item is None:
        return False
    if "text" in changes:
        text = str(changes["text"]).strip()
        if not text:
            return False
        item["text"] = text
    if changes.get("status") in STATUSES:
        item["status"] = changes["status"]
    save(items)
    return True


def remove(item_id: str) -> bool:
    items = load()
    remaining = [item for item in items if item.get("id") != item_id]
    if len(remaining) == len(items):
        return False
    save(remaining)
    return True


def move(item_id: str, target_index: int) -> bool:
    items = load()
    source_index = next(
        (index for index, item in enumerate(items) if item.get("id") == item_id), None
    )
    if source_index is None:
        return False
    item = items.pop(source_index)
    target_index = max(0, min(int(target_index), len(items)))
    items.insert(target_index, item)
    save(items)
    return True


class TodosProvider(Provider):
    name = "todos"

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        del options
        items = []
        counts = {status: 0 for status in STATUSES}
        for stored in load():
            status = stored.get("status")
            if status not in STATUSES:
                status = "todo"
            item = {
                "id": stored.get("id"),
                "text": stored.get("text", ""),
                "status": status,
            }
            items.append(item)
            counts[status] += 1
        return {"items": items, "counts": counts, "total": len(items)}
