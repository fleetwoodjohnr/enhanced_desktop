"""Notes: Markdown files in one folder (default ~/Notes; an Obsidian vault works).

A note is named by its path inside the folder, such as "Groceries.md" or
"Work/Standup.md". Nothing outside the folder can be named, and while Notes is
switched off the folder is closed to every other tool as well.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..policy import local_path
from .base import Capability, Guards, Integration, Tool
from .schemas import STRING, TEXT

ID = "notes"
SUFFIXES = (".md", ".markdown", ".txt")
MAX_NOTE = 200_000
MAX_RESULTS = 50

CAPABILITIES = (
    Capability("read", "Read notes", "read", "low"),
    Capability("search", "Search notes", "search", "low"),
    Capability("create", "Create notes", "create", "normal"),
    Capability("modify", "Change notes", "modify", "normal",
               "Replacing a note moves the previous version to Trash."),
    Capability("delete", "Move notes to Trash", "delete", "high"),
)


def folder() -> Path:
    from ..settings import load_settings
    configured = load_settings().get("notes_folder") or ""
    return Path(configured).expanduser() if configured else Path.home() / "Notes"


def _status():
    root = folder()
    if not root.is_dir():
        return {"state": "ready", "detail": f"{root} is created when CLIVE writes the first note"}
    count = sum(1 for _ in _notes(root))
    return {"state": "ready", "detail": f"{count} note{'s' if count != 1 else ''} in {root}"}


def _options():
    return [{"key": "notes_folder", "label": "Notes folder", "kind": "folder",
             "value": str(folder()), "detail": "Any folder in your home folder, such as an Obsidian vault"}]


def _set_option(key, value):
    from ..settings import save_settings
    if key != "notes_folder":
        raise ValueError("Unknown setting for Notes")
    _settings, errors = save_settings({"notes_folder": value if isinstance(value, str) else ""})
    if errors:
        raise ValueError(next(iter(errors.values())))


def integration() -> Integration:
    return Integration(
        ID, "Notes", "productivity", "accessories-text-editor-symbolic",
        "Markdown notes in one folder you choose; works with an Obsidian vault.",
        CAPABILITIES, default_enabled=False, status=_status,
        guards=Guards(paths=lambda: [str(folder())]),
        options=_options, set_option=_set_option)


def _notes(root: Path):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if not name.startswith(".") and name.lower().endswith(SUFFIXES):
                yield Path(directory, name)


def _resolve(name: str, must_exist: bool = True) -> Path:
    """A note's path, refusing anything that would leave the notes folder."""
    root = local_path(str(folder()))
    if not name or name.startswith("/") or any(part in ("", ".", "..") or part.startswith(".")
                                               for part in name.split("/")):
        raise ValueError("Name a note inside the notes folder, such as Groceries.md")
    if not name.lower().endswith(SUFFIXES):
        name += ".md"
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("That note is outside the notes folder")
    if must_exist and not path.is_file():
        raise ValueError(f"There is no note called {name}")
    return path


def _relative(path: Path) -> str:
    return str(path.relative_to(folder().resolve()))


def _list(_ctx, _a):
    root = folder()
    if not root.is_dir():
        return {"folder": str(root), "notes": []}
    notes = [{"name": _relative(p.resolve()), "modified": int(p.stat().st_mtime)} for p in _notes(root)]
    notes.sort(key=lambda n: n["modified"], reverse=True)
    return {"folder": str(root), "notes": notes[:200], "truncated": len(notes) > 200}


def _search(_ctx, a):
    root = folder()
    query = a["query"].casefold()
    results = []
    if root.is_dir():
        for path in _notes(root):
            name = _relative(path.resolve())
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:MAX_NOTE]
            except OSError:
                continue
            lines = [line.strip() for line in text.splitlines() if query in line.casefold()]
            if query in name.casefold() or lines:
                results.append({"name": name, "matches": lines[:3]})
                if len(results) >= MAX_RESULTS:
                    break
    return {"results": results, "truncated": len(results) >= MAX_RESULTS}


def _read(_ctx, a):
    path = _resolve(a["name"])
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"name": _relative(path), "text": text[:MAX_NOTE], "truncated": len(text) > MAX_NOTE}


def _file_name(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]+', " ", title)).strip().strip(".")
    return (cleaned[:120] or "Untitled") + ".md"


def _create(_ctx, a):
    root = folder()
    local_path(str(root))  # checked before anything is created: inside home, not hidden
    root.mkdir(parents=True, exist_ok=True)
    name = a.get("name") or _file_name(a["title"])
    path = _resolve(name, must_exist=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = a.get("text", "")
    with path.open("x", encoding="utf-8") as handle:
        handle.write(f"# {a['title']}\n\n{body}".rstrip() + "\n")
    return {"created": _relative(path)}


def _update(_ctx, a):
    path = _resolve(a["name"])
    if a.get("mode", "append") == "append":
        existing = path.read_text(encoding="utf-8", errors="replace")
        separator = "\n" if existing and not existing.endswith("\n") else ""
        with path.open("a", encoding="utf-8") as handle:
            handle.write(separator + a["text"].rstrip() + "\n")
        return {"appended": _relative(path)}
    from gi.repository import Gio
    staged = path.with_name(f".{path.name}.clive-edit")
    staged.write_text(a["text"], encoding="utf-8")
    try:
        if not Gio.File.new_for_path(str(path)).trash(None):
            raise RuntimeError("Could not move the previous version to Trash")
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return {"replaced": _relative(path), "previous_version": "in Trash"}


def _delete(_ctx, a):
    from gi.repository import Gio
    path = _resolve(a["name"])
    if not Gio.File.new_for_path(str(path)).trash(None):
        raise RuntimeError("Could not move the note to Trash")
    return {"trashed": a["name"], "verified": not path.exists()}


MODE = {"type": "string", "enum": ["append", "replace"]}

TOOLS = (
    Tool("note_list", "List notes, most recently changed first.", {}, "read", _list, ID, "Listing notes…"),
    Tool("note_search", "Find notes whose name or text contains the query.", {"query": STRING},
         "search", _search, ID, "Searching Notes…", describe=lambda a: a["query"]),
    Tool("note_read", "Read a note by name, such as Groceries.md.", {"name": STRING}, "read", _read, ID,
         "Reading a note…", describe=lambda a: a["name"]),
    Tool("note_create", "Create a new note. name is optional, such as Work/Standup.md.",
         {"title": STRING, "text": TEXT, "name": STRING}, "create", _create, ID, "Creating a note…",
         required=["title", "text"], describe=lambda a: a.get("name") or a["title"]),
    Tool("note_update", "Append to a note, or replace its text (mode replace; the old version goes to Trash).",
         {"name": STRING, "text": TEXT, "mode": MODE}, "modify", _update, ID, "Updating a note…",
         required=["name", "text"], describe=lambda a: a["name"]),
    Tool("note_delete", "Move a note to Trash.", {"name": STRING}, "delete", _delete, ID,
         "Moving a note to Trash…", describe=lambda a: a["name"]),
)
