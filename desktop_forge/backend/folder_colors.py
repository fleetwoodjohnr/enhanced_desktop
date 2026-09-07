"""Individual folder icons, using the same GIO metadata as GNOME Files."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
from string import Template

from gi.repository import Gio

from .. import config

CUSTOM_ICON = "metadata::custom-icon"
PRESETS = (
    ("Blue", "#3584e4"), ("Teal", "#2190a4"), ("Green", "#33a65c"),
    ("Yellow", "#e5b52f"), ("Orange", "#ed8733"), ("Red", "#e35151"),
    ("Pink", "#df69a0"), ("Purple", "#9462c9"), ("Gray", "#92969d"),
)
TEMPLATE = Path(__file__).parent.parent / "assets" / "folder.svg"


def normalize_color(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()):
        raise ValueError("Enter a color as #RRGGBB, for example #3584e4.")
    return value.strip().lower()


def render_icon(color: str) -> bytes:
    color = normalize_color(color)
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    def mix(target: int, amount: float) -> str:
        return "#" + "".join(f"{round(c * (1 - amount) + target * amount):02x}" for c in rgb)

    return Template(TEMPLATE.read_text(encoding="utf-8")).substitute(
        color=color, highlight=mix(255, 0.24), back=mix(0, 0.25), edge=mix(0, 0.16),
    ).encode("utf-8")


@dataclass(frozen=True)
class FolderColor:
    uri: str
    color: str
    icon_uri: str
    previous_icon: str | None

    @property
    def path(self) -> str:
        return Gio.File.new_for_uri(self.uri).get_path()


class FolderColors:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.store_path = self.data_dir / "folder-colors.json"
        self.icon_dir = self.data_dir / "folder-icons"

    def load(self) -> list[FolderColor]:
        if not self.store_path.exists():
            return []
        raw = config.read_json(str(self.store_path))
        try:
            if not isinstance(raw, dict) or raw.get("version") != 1:
                raise ValueError()
            records = []
            for item in raw["folders"]:
                entry = FolderColor(**item)
                if (not entry.uri.startswith("file://") or not entry.path or
                        not isinstance(entry.icon_uri, str) or
                        (entry.previous_icon is not None and not isinstance(entry.previous_icon, str))):
                    raise ValueError()
                normalize_color(entry.color)
                records.append(entry)
            if len({entry.uri for entry in records}) != len(records):
                raise ValueError()
            return records
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError("Could not read saved folder colors. Your existing icons have been left in place.") from exc

    def _save(self, records: list[FolderColor]) -> None:
        config.write_json(str(self.store_path), {
            "version": 1, "folders": [asdict(entry) for entry in records],
        })

    @staticmethod
    def folder(uri: str) -> Gio.File:
        selected = Gio.File.new_for_uri(uri)
        path = selected.get_path()
        if path is None:
            raise ValueError("Choose a local folder.")
        folder = Gio.File.new_for_path(os.path.realpath(path))
        info = folder.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
        if info.get_file_type() != Gio.FileType.DIRECTORY:
            raise ValueError("Choose a folder, rather than a file.")
        return folder

    @staticmethod
    def _read_icon(folder: Gio.File) -> str | None:
        info = folder.query_info(CUSTOM_ICON, Gio.FileQueryInfoFlags.NONE, None)
        return info.get_attribute_string(CUSTOM_ICON) or None

    @staticmethod
    def _write_icon(folder: Gio.File, icon: str | None) -> None:
        info = Gio.FileInfo()
        if icon is None:
            # PyGObject requires an integer for this untyped pointer argument;
            # INVALID ignores its value and removes the metadata attribute.
            info.set_attribute(CUSTOM_ICON, Gio.FileAttributeType.INVALID, 0)
        else:
            info.set_attribute_string(CUSTOM_ICON, icon)
        if not folder.set_attributes_from_info(info, Gio.FileQueryInfoFlags.NONE, None):
            raise OSError("This folder does not support custom icons.")

    def _icon(self, color: str) -> str:
        # Color-specific filenames invalidate Files/DING's image caches on edits.
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        icon = self.icon_dir / f"folder-{color[1:]}.svg"
        if not icon.exists():
            temporary = icon.with_suffix(".svg.tmp")
            temporary.write_bytes(render_icon(color))
            temporary.replace(icon)
        return icon.as_uri()

    def _commit(self, folder: Gio.File, before: str | None, after: str | None,
                records: list[FolderColor]) -> None:
        if before != after:
            self._write_icon(folder, after)
        try:
            self._save(records)
        except OSError as exc:
            if before != after:
                try:
                    self._write_icon(folder, before)
                except Exception as rollback_error:
                    raise OSError(f"Could not save folder colors or restore the previous icon: {rollback_error}") from exc
            raise

    def apply(self, uri: str, color: str) -> FolderColor:
        color = normalize_color(color)
        records = self.load()
        folder = self.folder(uri)
        uri = folder.get_uri()
        old = next((entry for entry in records if entry.uri == uri), None)
        current = self._read_icon(folder)
        previous = old.previous_icon if old and current == old.icon_uri else current
        entry = FolderColor(uri, color, self._icon(color), previous)
        remaining = [item for item in records if item.uri != uri]
        self._commit(folder, current, entry.icon_uri, [*remaining, entry])
        return entry

    def reset(self, uri: str) -> None:
        records = self.load()
        entry = next((item for item in records if item.uri == uri), None)
        if entry is None:
            return
        folder = self.folder(uri)
        current = self._read_icon(folder)
        # Never overwrite a custom icon the user subsequently set in Files.
        restored = entry.previous_icon if current == entry.icon_uri else current
        self._commit(folder, current, restored, [item for item in records if item.uri != uri])

    def forget(self, uri: str) -> None:
        if Gio.File.new_for_uri(uri).query_exists(None):
            raise ValueError("Use Reset for a folder that is still available.")
        self._save([entry for entry in self.load() if entry.uri != uri])

    def relocate(self, old_uri: str, new_uri: str) -> None:
        records = self.load()
        entry = next(item for item in records if item.uri == old_uri)
        folder = self.folder(new_uri)
        uri = folder.get_uri()
        if any(item.uri == uri for item in records):
            raise ValueError("That folder is already in the list.")
        current = self._read_icon(folder)
        # If Files moved the metadata with the directory, keep its reset history.
        previous = entry.previous_icon if current == entry.icon_uri else current
        relocated = FolderColor(uri, entry.color, self._icon(entry.color), previous)
        self._commit(folder, current, relocated.icon_uri,
                     [relocated if item.uri == old_uri else item for item in records])
