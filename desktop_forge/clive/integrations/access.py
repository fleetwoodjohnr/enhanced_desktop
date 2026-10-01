"""The App Access switches, as stored on disk.

One small JSON file holds only what the user changed; the defaults live with
each integration. The service is the one writer. Every read re-checks the
file's size and timestamp, so a switch turned off takes effect on the very
next tool call, including one in the middle of a running task.
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable

from ... import config

ACCESS_PATH = Path(config.CONFIG_DIR) / "clive-access.json"
VERSION = 1


def _normalize(raw) -> dict:
    """Keep only well-formed entries; an unexpected shape grants nothing."""
    data = {"version": VERSION, "paused": False, "known_apps": [], "apps": {}}
    if not isinstance(raw, dict):
        return data
    data["paused"] = raw.get("paused") is True
    known = raw.get("known_apps")
    if isinstance(known, list):
        data["known_apps"] = sorted({item for item in known if isinstance(item, str)})
    apps = raw.get("apps")
    if isinstance(apps, dict):
        for key, entry in apps.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            clean = {}
            if isinstance(entry.get("enabled"), bool):
                clean["enabled"] = entry["enabled"]
            for field in ("capabilities", "confirm"):
                values = entry.get(field)
                if isinstance(values, dict):
                    clean[field] = {k: v for k, v in values.items()
                                    if isinstance(k, str) and isinstance(v, bool)}
            data["apps"][key] = clean
    return data


class AccessStore:
    """Thread-safe persistence for the App Access switches."""

    def __init__(self, path: Path = ACCESS_PATH, known_apps: Callable[[], list[str]] = lambda: []):
        self.path = Path(path)
        self.known_apps = known_apps
        self.lock = threading.RLock()
        self._data: dict | None = None
        self._key = None
        self.error = ""

    def _stat_key(self):
        status = self.path.stat()
        return (status.st_mtime_ns, status.st_size)

    def _current(self) -> dict:
        with self.lock:
            try:
                key = self._stat_key()
            except FileNotFoundError:
                # First run (or a reset): the apps installed right now keep
                # the access CLIVE already had; anything installed later
                # starts switched off until the user turns it on.
                self._data = _normalize({"known_apps": list(self.known_apps())})
                self._write(self._data)
                return self._data
            if key != self._key:
                raw = config.read_json(str(self.path))
                if not isinstance(raw, dict):
                    # A damaged file fails closed. Keep the last good switches
                    # if there were any; otherwise pause everything.
                    self.error = "The App Access file could not be read, so access is paused."
                    if self._data is None:
                        self._data = {**_normalize({}), "paused": True}
                else:
                    self.error = ""
                    self._data = _normalize(raw)
                self._key = key
            return self._data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config.write_json(str(self.path), data)
        self.path.chmod(0o600)
        self._key = self._stat_key()

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self._current())

    def update(self, change: Callable[[dict], None]) -> dict:
        """Apply `change` to a copy and persist it, as one locked step."""
        with self.lock:
            data = copy.deepcopy(self._current())
            change(data)
            data = _normalize(data)
            self._write(data)
            self._data = data
            return copy.deepcopy(data)
