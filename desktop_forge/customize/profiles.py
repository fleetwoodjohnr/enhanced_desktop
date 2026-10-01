"""Saved profiles: a name and a set of setting values, switched in one step.

Built-in presets are read-only profiles; duplicating one gives an editable
copy. User profiles are files in ~/.config/desktop-forge/profiles/, and a
profile exported to share is the same JSON with a format marker:

    {"format": "desktop-forge-profile", "version": 1, "name": "...", "values": {...}}

Importing checks every value against the registry: numbers are clamped to
their range, unknown or invalid settings are dropped and reported, and
settings tied to this computer (picture paths) are flagged.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field

from .. import config
from . import presets
from .registry import BY_ID, SETTINGS, validate

PROFILES_DIR = os.path.join(config.CONFIG_DIR, "profiles")
FORMAT = "desktop-forge-profile"
VERSION = 1
MAX_FILE = 512 * 1024
MAX_NAME = 60
EXTENSION = ".dfprofile"

# Everything a user profile records when saving the current desktop.
PROFILE_IDS = tuple(s.id for s in SETTINGS if s.profile)


@dataclass
class Profile:
    id: str
    name: str
    values: dict = field(default_factory=dict)
    builtin: bool = False
    description: str = ""
    icon: str = "preferences-desktop-appearance-symbolic"


@dataclass
class ImportReport:
    profile: Profile | None = None
    clamped: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    machine: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.clamped:
            parts.append(f"{len(self.clamped)} adjusted to fit the allowed range")
        if self.dropped:
            parts.append(f"{len(self.dropped)} not recognised and left out")
        if self.machine:
            parts.append(f"{len(self.machine)} refer to files on the computer it came from")
        return "; ".join(parts)


def clean_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Give the profile a name")
    return re.sub(r"\s+", " ", name).strip()[:MAX_NAME]


def check_values(values) -> tuple[dict, ImportReport]:
    """Values validated against the registry, and what had to change."""
    report = ImportReport()
    if not isinstance(values, dict):
        raise ValueError("The profile has no settings")
    clean = {}
    for setting_id, value in values.items():
        setting = BY_ID.get(setting_id)
        if setting is None or not setting.profile:
            report.dropped.append(str(setting_id)[:80])
            continue
        try:
            checked = validate(setting, value)
        except ValueError:
            report.dropped.append(setting_id)
            continue
        if checked != value:
            report.clamped.append(setting_id)
        if setting.machine and checked:
            report.machine.append(setting_id)
        clean[setting_id] = checked
    return clean, report


def parse(text: str) -> ImportReport:
    """A shared profile file, validated. Raises ValueError when it is not one."""
    if len(text) > MAX_FILE:
        raise ValueError("That file is too large to be a profile")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("That file is not a Desktop Forge profile") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("That file is not a Desktop Forge profile")
    version = data.get("version")
    if not isinstance(version, int) or version > VERSION:
        raise ValueError("That profile was made by a newer version of Desktop Forge")
    name = clean_name(data.get("name"))
    values, report = check_values(data.get("values"))
    report.profile = Profile(id="", name=name, values=values)
    return report


def serialize(profile: Profile) -> str:
    return json.dumps({"format": FORMAT, "version": VERSION, "name": profile.name,
                       "values": profile.values}, indent=2, sort_keys=True) + "\n"


class ProfileStore:
    def __init__(self, directory: str = PROFILES_DIR):
        self.directory = directory
        self._lock = threading.RLock()

    # -- reading ----------------------------------------------------------

    def _path(self, profile_id: str) -> str:
        if not re.fullmatch(r"[a-z0-9-]{1,40}", profile_id):
            raise ValueError("Unknown profile")
        return os.path.join(self.directory, f"{profile_id}.json")

    def _state_path(self) -> str:
        return os.path.join(self.directory, "state.json")

    def user_profiles(self) -> list[Profile]:
        found = []
        try:
            names = sorted(os.listdir(self.directory))
        except FileNotFoundError:
            return []
        for filename in names:
            profile_id, extension = os.path.splitext(filename)
            if extension != ".json" or profile_id == "state":
                continue
            profile = self._load(profile_id)
            if profile is not None:
                found.append(profile)
        return sorted(found, key=lambda p: p.name.casefold())

    def _load(self, profile_id: str) -> Profile | None:
        try:
            with open(self._path(profile_id), encoding="utf-8") as handle:
                report = parse(handle.read())
        except (OSError, ValueError):
            return None
        profile = report.profile
        profile.id = profile_id
        return profile

    @staticmethod
    def presets() -> list[Profile]:
        return [Profile(id=f"preset-{p['id']}", name=p["name"], values=dict(p["values"]), builtin=True,
                        description=p["description"], icon=p["icon"]) for p in presets.PRESETS]

    def all(self) -> list[Profile]:
        return self.presets() + self.user_profiles()

    def get(self, profile_id: str) -> Profile:
        if profile_id.startswith("preset-"):
            for profile in self.presets():
                if profile.id == profile_id:
                    return profile
            raise ValueError("Unknown profile")
        profile = self._load(profile_id)
        if profile is None:
            raise ValueError("That profile no longer exists")
        return profile

    # -- what a profile sets ----------------------------------------------

    @staticmethod
    def target(profile: Profile, defaults) -> dict:
        """Every value switching to this profile sets.

        A preset resets the look to neutral first; a user profile sets
        exactly what it saved.
        """
        if profile.builtin:
            return {**presets.neutral(defaults), **profile.values}
        return dict(profile.values)

    def modified(self, profile: Profile, current: dict, defaults) -> list[str]:
        """Settings whose current value differs from what the profile sets."""
        return [i for i, value in self.target(profile, defaults).items()
                if i in current and current[i] != value]

    # -- writing ----------------------------------------------------------

    def _write(self, profile: Profile) -> None:
        os.makedirs(self.directory, exist_ok=True)
        data = json.loads(serialize(profile))
        config.write_json(self._path(profile.id), data)

    def _new_id(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:28] or "profile"
        return f"{slug}-{uuid.uuid4().hex[:6]}"

    def create(self, name: str, values: dict) -> Profile:
        with self._lock:
            values, _report = check_values(values)
            name = clean_name(name)
            profile = Profile(id=self._new_id(name), name=name, values=values)
            self._write(profile)
            return profile

    def save_values(self, profile_id: str, values: dict) -> Profile:
        """Replace a user profile's values, as in "Save current desktop"."""
        with self._lock:
            profile = self.get(profile_id)
            if profile.builtin:
                raise ValueError("Built-in presets cannot be changed; duplicate it first")
            profile.values, _report = check_values(values)
            self._write(profile)
            return profile

    def duplicate(self, profile_id: str, name: str | None = None, defaults=None) -> Profile:
        source = self.get(profile_id)
        values = self.target(source, defaults) if source.builtin and defaults else source.values
        return self.create(name or f"{source.name} (copy)", copy.deepcopy(values))

    def rename(self, profile_id: str, name: str) -> Profile:
        with self._lock:
            profile = self.get(profile_id)
            if profile.builtin:
                raise ValueError("Built-in presets cannot be renamed; duplicate it first")
            profile.name = clean_name(name)
            self._write(profile)
            return profile

    def delete(self, profile_id: str) -> None:
        with self._lock:
            if profile_id.startswith("preset-"):
                raise ValueError("Built-in presets cannot be deleted")
            try:
                os.unlink(self._path(profile_id))
            except FileNotFoundError:
                pass
            if self.active() == profile_id:
                self.set_active("")

    def export(self, profile_id: str, path: str, defaults=None) -> None:
        profile = self.get(profile_id)
        if profile.builtin and defaults:
            profile = Profile(id=profile.id, name=profile.name, values=self.target(profile, defaults))
        from pathlib import Path
        from .backends import _atomic_text
        _atomic_text(Path(path), serialize(profile))

    def import_text(self, text: str) -> ImportReport:
        report = parse(text)
        imported = report.profile
        existing = {p.name.casefold() for p in self.all()}
        name = imported.name
        if name.casefold() in existing:
            name = clean_name(f"{name} (imported)")
        report.profile = self.create(name, imported.values)
        return report

    def import_file(self, path: str) -> ImportReport:
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read(MAX_FILE + 1)
        except (OSError, UnicodeDecodeError):
            raise ValueError("That file could not be read") from None
        return self.import_text(text)

    # -- which profile is in use ---------------------------------------------

    def active(self) -> str:
        data = config.read_json(self._state_path())
        value = data.get("active") if isinstance(data, dict) else ""
        return value if isinstance(value, str) else ""

    def set_active(self, profile_id: str) -> None:
        with self._lock:
            os.makedirs(self.directory, exist_ok=True)
            config.write_json(self._state_path(), {"active": profile_id})
