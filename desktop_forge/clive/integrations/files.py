"""Files in the home folder: find, read, create, change, move, rename, trash."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ..models import Cancelled
from ..policy import local_path
from .base import Capability, Guards, Integration, Tool
from .schemas import STRING, TEXT

ID = "files"

CAPABILITIES = (
    Capability("read", "Read files", "read", "low", "Open visible files in your home folder."),
    Capability("search", "Search files", "search", "low", "Find files by name."),
    Capability("create", "Create files and folders", "create", "normal",
               "New files never overwrite existing ones."),
    Capability("modify", "Change existing files", "modify", "normal",
               "Replace a text file's contents; the previous version goes to Trash.",
               default=False),
    Capability("move", "Move files", "modify", "normal"),
    Capability("rename", "Rename files", "modify", "normal"),
    Capability("delete", "Move files to Trash", "delete", "high",
               "Always shows the file and asks first."),
)


def integration() -> Integration:
    return Integration(
        ID, "Files", "files", "system-file-manager-symbolic",
        "Visible files and folders in your home folder. Hidden folders and credentials stay out of reach.",
        CAPABILITIES, default_enabled=True,
        # The Files app shows everything these tools can reach.
        guards=Guards(desktop_ids=("org.gnome.Nautilus",), categories=("FileManager",)),
    )


def _search(ctx, a):
    root = local_path(a["directory"])
    found, visited = [], 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        if ctx.cancel.is_set():
            raise Cancelled()
        dirs[:] = [d for d in dirs if not d.startswith(".") and not Path(directory, d).is_symlink()]
        for filename in files:
            visited += 1
            path = Path(directory, filename)
            if not filename.startswith(".") and not path.is_symlink() and \
                    a["query"].casefold() in filename.casefold():
                found.append(str(path))
            if len(found) >= 100 or visited >= 25000:
                return {"paths": found, "truncated": True}
    return {"paths": found, "truncated": False}


def _read(_ctx, a):
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


def _write(_ctx, a):
    path = local_path(a["path"])
    with path.open("x", encoding="utf-8") as handle:
        handle.write(a["text"])
    return {"created": str(path), "bytes": path.stat().st_size}


def _folder(_ctx, a):
    path = local_path(a["path"])
    path.mkdir(parents=False, exist_ok=False)
    return {"created": str(path), "folder": True}


def _gio():
    from gi.repository import Gio
    return Gio


def _move(_ctx, a):
    Gio = _gio()
    source, destination = local_path(a["source"]), local_path(a["destination"])
    if not source.is_file():
        raise ValueError("Only regular files can be moved")
    Gio.File.new_for_path(str(source)).move(Gio.File.new_for_path(str(destination)),
                                           Gio.FileCopyFlags.NONE, None, None)
    return {"moved": str(destination), "verified": destination.is_file() and not source.exists()}


def _rename(_ctx, a):
    Gio = _gio()
    source = local_path(a["path"])
    name = a["new_name"]
    if "/" in name or name in (".", "..") or name.startswith("."):
        raise ValueError("A new name is one visible file name, without folders")
    if not source.exists():
        raise ValueError("That file no longer exists")
    destination = local_path(str(source.with_name(name)))
    Gio.File.new_for_path(str(source)).move(Gio.File.new_for_path(str(destination)),
                                           Gio.FileCopyFlags.NONE, None, None)
    return {"renamed": str(destination), "verified": destination.exists() and not source.exists()}


def _edit(_ctx, a):
    """Replace a text file, keeping the previous version recoverable in Trash."""
    Gio = _gio()
    path = local_path(a["path"])
    if not path.is_file():
        raise ValueError("Only existing regular files can be changed")
    staged = path.with_name(f".{path.name}.clive-edit")
    staged.write_text(a["text"], encoding="utf-8")
    try:
        if not Gio.File.new_for_path(str(path)).trash(None):
            raise RuntimeError("Could not move the previous version to Trash")
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return {"changed": str(path), "bytes": path.stat().st_size, "previous_version": "in Trash"}


def _trash(_ctx, a):
    Gio = _gio()
    path = local_path(a["path"])
    if not path.is_file():
        raise ValueError("Only regular files can be trashed")
    if not Gio.File.new_for_path(str(path)).trash(None):
        raise RuntimeError("Could not move the file to Trash")
    return {"trashed": str(path), "verified": not path.exists()}


TOOLS = (
    Tool("file_search", "Find visible files by case-insensitive filename within a directory. Maximum 100 results.",
         {"directory": STRING, "query": STRING}, "search", _search, ID, "Searching Files…",
         describe=lambda a: f"“{a['query']}” in {a['directory']}", path_keys=("directory",)),
    Tool("file_read", "Read a UTF-8 text file, at most 50 KB.", {"path": STRING}, "read", _read, ID,
         "Reading a file…", describe=lambda a: a["path"], path_keys=("path",)),
    Tool("file_write", "Create a new UTF-8 file. Never overwrites an existing file.",
         {"path": STRING, "text": TEXT}, "create", _write, ID, "Creating a file…",
         describe=lambda a: a["path"], path_keys=("path",)),
    Tool("folder_create", "Create a new folder inside an existing folder.", {"path": STRING},
         "create", _folder, ID, "Creating a folder…", describe=lambda a: a["path"], path_keys=("path",)),
    Tool("file_edit", "Replace the text of an existing file. The previous version is moved to Trash.",
         {"path": STRING, "text": TEXT}, "modify", _edit, ID, "Changing a file…",
         describe=lambda a: a["path"], path_keys=("path",)),
    Tool("file_move", "Move a file without overwriting the destination.",
         {"source": STRING, "destination": STRING}, "move", _move, ID, "Moving a file…",
         describe=lambda a: f"{a['source']} → {a['destination']}", path_keys=("source", "destination")),
    Tool("file_rename", "Rename a file or folder in place. new_name is a single file name.",
         {"path": STRING, "new_name": STRING}, "rename", _rename, ID, "Renaming a file…",
         describe=lambda a: f"{a['path']} → {a['new_name']}", path_keys=("path",)),
    Tool("file_trash", "Move a file to the desktop trash.", {"path": STRING}, "delete", _trash, ID,
         "Moving a file to Trash…", describe=lambda a: a["path"], path_keys=("path",)),
)
