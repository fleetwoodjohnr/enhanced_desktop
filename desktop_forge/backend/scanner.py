"""Finds the desktop entries the user owns, and diagnoses what's wrong with them.

Scope is deliberately the two directories a user can actually fix: the Desktop
itself, and ~/.local/share/applications. System entries under /usr are owned by
packages and are not ours to edit.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import desktop_entry as de
from . import icons


@dataclass
class EntryIssue:
    severity: str  # "error" | "warning"
    message: str
    hint: str = ""


@dataclass
class ScannedEntry:
    path: str
    location: str  # "Desktop" | "Applications"
    name: str
    exec_line: str
    icon: str
    comment: str
    is_symlink: bool
    symlink_target: str
    executable: bool
    trusted: bool
    issues: list[EntryIssue] = field(default_factory=list)

    @property
    def basename(self) -> str:
        return os.path.basename(self.path)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def _is_trusted(path: str) -> bool:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    gfile = Gio.File.new_for_path(path)
    try:
        info = gfile.query_info("metadata::trusted", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False
    return info.get_attribute_string("metadata::trusted") == "true"


def scan_dir(directory: str, location: str) -> list[ScannedEntry]:
    entries: list[ScannedEntry] = []
    if not os.path.isdir(directory):
        return entries

    for name in sorted(os.listdir(directory)):
        if not name.endswith(".desktop"):
            continue
        path = os.path.join(directory, name)
        kf = de.load_keyfile(path)
        if kf is None:
            entries.append(
                ScannedEntry(
                    path=path, location=location, name=name, exec_line="", icon="",
                    comment="", is_symlink=os.path.islink(path), symlink_target="",
                    executable=os.access(path, os.X_OK), trusted=False,
                    issues=[EntryIssue("error", "File is not a valid desktop entry",
                                       "It may be truncated or missing its [Desktop Entry] group.")],
                )
            )
            continue

        entry = ScannedEntry(
            path=path,
            location=location,
            name=de.get_string(kf, "Name", name),
            exec_line=de.get_string(kf, "Exec"),
            icon=de.get_string(kf, "Icon"),
            comment=de.get_string(kf, "Comment"),
            is_symlink=os.path.islink(path),
            symlink_target=os.path.realpath(path) if os.path.islink(path) else "",
            executable=os.access(path, os.X_OK),
            trusted=_is_trusted(path),
        )
        entry.issues = diagnose(entry, location)
        entries.append(entry)

    return entries


def diagnose(entry: ScannedEntry, location: str) -> list[EntryIssue]:
    issues: list[EntryIssue] = []

    program = de.exec_program(entry.exec_line)
    if not entry.exec_line:
        issues.append(EntryIssue("error", "No Exec line", "This entry cannot launch anything."))
    elif not de.program_exists(program):
        issues.append(
            EntryIssue(
                "error",
                f"Exec target not found: {program}",
                "The program was moved, renamed, or uninstalled.",
            )
        )

    if entry.icon and not icons.icon_exists(entry.icon):
        issues.append(
            EntryIssue(
                "warning",
                f"Icon not found: {entry.icon}",
                "The entry still launches, but shows a generic icon.",
            )
        )
    elif not entry.icon:
        issues.append(EntryIssue("warning", "No icon set", "Shows a generic icon."))

    # Executability and trust only matter for icons sitting on the desktop --
    # GNOME's Desktop Icons extension refuses to launch an entry that lacks
    # either. Entries in ~/.local/share/applications need neither.
    if location == "Desktop":
        if not entry.executable:
            issues.append(
                EntryIssue("error", "Not executable",
                           "GNOME will not launch a desktop icon without the executable bit.")
            )
        if not entry.trusted:
            issues.append(
                EntryIssue("error", "Not marked as trusted",
                           "GNOME shows “Untrusted desktop file” until this is set.")
            )

    for complaint in de.validate(entry.path):
        # The validator reports its own path back; strip it for readability.
        text = complaint.split(": ", 1)[-1] if ": " in complaint else complaint
        severity = "error" if "error" in complaint.lower() else "warning"
        issues.append(EntryIssue(severity, text))

    return issues


def scan_all() -> list[ScannedEntry]:
    return scan_dir(de.desktop_dir(), "Desktop") + scan_dir(
        de.applications_dir(), "Applications"
    )


def repair(entry: ScannedEntry) -> list[str]:
    """Fix what can be fixed without guessing. Returns what was done.

    Only the mechanical problems are repaired here -- the executable bit and
    the trusted flag. A missing Exec target is NOT auto-fixed: the correct new
    path is a question only the user can answer, so that stays a manual edit.
    """
    actions: list[str] = []

    if not entry.executable:
        try:
            os.chmod(entry.path, 0o755)
            actions.append("Made executable")
        except OSError as exc:
            actions.append(f"Could not set executable bit: {exc}")

    if not entry.trusted and entry.location == "Desktop":
        if de.mark_trusted(entry.path):
            os.chmod(entry.path, 0o755)
            actions.append("Marked as trusted")
        else:
            actions.append(
                "Could not mark as trusted — " + de.TRUST_HINT
            )

    return actions


def remove(entry: ScannedEntry) -> None:
    """Delete an entry.

    os.remove on a symlink unlinks the link itself, never the target -- which
    is exactly what's wanted for entries like org.jrf.FirewallGui.desktop that
    point into a project checkout. Deleting the source file there would damage
    an unrelated repository.
    """
    os.remove(entry.path)
