"""Build, write, validate and diagnose XDG desktop entries.

Everything here goes through GLib.KeyFile rather than string formatting.
Desktop entries have real escaping rules (semicolon-terminated list values,
backslash escapes, localized `Name[xx]` keys), and hand-rolling them is how
you end up with a file that looks fine and silently fails to launch.

No GTK imports -- this module is used by the daemon and the CLI paths too.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

DESKTOP_GROUP = GLib.KEY_FILE_DESKTOP_GROUP  # "Desktop Entry"

# Field codes a launcher may carry (%f, %U, ...). They are meaningful to the
# launching shell, not to us, so they must be stripped before the Exec line can
# be inspected for "does this program actually exist".
_FIELD_CODES = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%i", "%c", "%k", "%v", "%m"}


def desktop_dir() -> str:
    """The user's Desktop, honouring a localized/relocated XDG setup."""
    path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    return path or os.path.join(GLib.get_home_dir(), "Desktop")


def applications_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "applications")


@dataclass
class LauncherSpec:
    """The user-facing shape of a launcher, before it becomes a file."""

    name: str
    exec_line: str
    comment: str = ""
    icon: str = ""
    categories: list[str] = field(default_factory=list)
    terminal: bool = False
    keywords: list[str] = field(default_factory=list)
    working_dir: str = ""

    def filename(self) -> str:
        """A stable, filesystem-safe basename derived from the display name."""
        safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in self.name.strip())
        safe = "-".join(part for part in safe.split("-") if part).lower()
        return f"{safe or 'launcher'}.desktop"


def build_keyfile(spec: LauncherSpec) -> GLib.KeyFile:
    kf = GLib.KeyFile()
    kf.set_string(DESKTOP_GROUP, "Type", "Application")
    kf.set_string(DESKTOP_GROUP, "Version", "1.0")
    kf.set_string(DESKTOP_GROUP, "Name", spec.name)
    if spec.comment:
        kf.set_string(DESKTOP_GROUP, "Comment", spec.comment)
    kf.set_string(DESKTOP_GROUP, "Exec", spec.exec_line)
    if spec.icon:
        kf.set_string(DESKTOP_GROUP, "Icon", spec.icon)
    if spec.working_dir:
        kf.set_string(DESKTOP_GROUP, "Path", spec.working_dir)
    kf.set_boolean(DESKTOP_GROUP, "Terminal", spec.terminal)
    if spec.categories:
        kf.set_string_list(DESKTOP_GROUP, "Categories", spec.categories)
    if spec.keywords:
        kf.set_string_list(DESKTOP_GROUP, "Keywords", spec.keywords)
    kf.set_boolean(DESKTOP_GROUP, "StartupNotify", True)
    return kf


def quote_exec(program: str, args: str = "") -> str:
    """Quote a program path for an Exec line.

    A path containing spaces must be quoted or the launcher splits it into
    argv entries -- the existing hand-written photo-video-organizer.desktop
    already does this correctly, and generated files must match.
    """
    program = program.strip()
    if program and (" " in program or '"' in program):
        escaped = program.replace("\\", "\\\\").replace('"', '\\"')
        program = f'"{escaped}"'
    return f"{program} {args}".strip()


def write_entry(kf: GLib.KeyFile, path: str, *, trust: bool = True) -> bool:
    """Write a keyfile to `path` and make it launchable from the desktop.

    Order matters. A .desktop file sitting on the GNOME desktop is only
    honoured by the Desktop Icons (DING) extension when it is BOTH executable
    and carries the GIO `metadata::trusted` attribute -- DING reads exactly
    that attribute (app/fileItem.js:344), and without it the icon prompts
    "Untrusted desktop file".

    Setting the metadata attribute can drop the executable bit, so the chmod
    is deliberately done last rather than alongside the write.

    Returns whether the trusted flag was actually stored. It can legitimately
    fail -- metadata lives in gvfsd-metadata, not the file, and a full or
    corrupt metadata journal makes every write fail until the daemon rotates
    it (observed on this machine). The entry is still written and still works
    from the app grid, so callers warn rather than treat it as an error.
    """
    data, _length = kf.to_data()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Write via a temp file in the same directory + rename, so a reader (DING
    # watches this directory) never observes a half-written entry.
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(data)
    os.replace(tmp, path)

    trusted = mark_trusted(path) if trust else True
    os.chmod(path, 0o755)
    return trusted


def mark_trusted(path: str) -> bool:
    """Set the GIO trusted flag. Returns False if the filesystem can't store it.

    Metadata lives in GVfs, not the file, so this legitimately fails on some
    mounts. That is not fatal -- the entry still works from the app grid, only
    the desktop icon is affected -- so callers report it rather than abort.
    """
    gfile = Gio.File.new_for_path(path)
    info = Gio.FileInfo()
    info.set_attribute_string("metadata::trusted", "true")
    try:
        gfile.set_attributes_from_info(info, Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False

    # set_attributes_from_info reports success even when the metadata backend
    # quietly dropped the write, so the only trustworthy check is reading it
    # back.
    try:
        back = gfile.query_info("metadata::trusted", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False
    return back.get_attribute_as_string("metadata::trusted") == "true"


def validate(path: str) -> list[str]:
    """Run desktop-file-validate, returning its complaints (empty == valid).

    Absent on a minimal system; a missing validator must not be reported as a
    broken file, so that case returns no errors.
    """
    validator = shutil.which("desktop-file-validate")
    if not validator:
        return []
    try:
        proc = subprocess.run(
            [validator, path], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode == 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def load_keyfile(path: str) -> Optional[GLib.KeyFile]:
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(path, GLib.KeyFileFlags.KEEP_COMMENTS | GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    except GLib.Error:
        return None
    return kf


def get_string(kf: GLib.KeyFile, key: str, default: str = "") -> str:
    try:
        return kf.get_string(DESKTOP_GROUP, key)
    except GLib.Error:
        return default


def exec_program(exec_line: str) -> str:
    """The bare program from an Exec line, minus field codes and arguments."""
    if not exec_line:
        return ""
    try:
        parts = shlex.split(exec_line)
    except ValueError:
        parts = exec_line.split()
    for part in parts:
        if part in _FIELD_CODES:
            continue
        return part
    return ""


def program_exists(program: str) -> bool:
    if not program:
        return False
    if "/" in program:
        return os.path.isfile(program) and os.access(program, os.X_OK)
    return shutil.which(program) is not None


def copy_to_desktop(source_path: str, *, dest_dir: Optional[str] = None) -> str:
    """Place an already-installed app's entry on the desktop.

    The source is round-tripped through a KeyFile rather than copied byte for
    byte. Loading with KEEP_TRANSLATIONS retains every localized Name[xx] and
    every extra group (Desktop Action entries, the right-click submenu), so
    nothing is lost -- but it also guarantees the result is well-formed, which
    a blind copy of a malformed system entry would not be.

    Returns (path written, whether the trusted flag stuck).
    """
    kf = load_keyfile(source_path)
    if kf is None:
        raise ValueError(f"{source_path} is not a valid desktop entry")

    dest_dir = dest_dir or desktop_dir()
    dest = os.path.join(dest_dir, os.path.basename(source_path))
    trusted = write_entry(kf, dest)
    return dest, trusted


def unique_path(directory: str, filename: str) -> str:
    """A path in `directory` that doesn't collide with an existing entry."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}-{counter}{ext}")
        counter += 1
    return candidate


# Shown whenever the trusted flag could not be stored. Right-clicking the icon
# and choosing "Allow Launching" calls into DING's own setter
# (app/fileItem.js:711), which retries the same write -- so it is a real fix
# the user can apply, not just an explanation.
TRUST_HINT = "Right-click it on the desktop and choose “Allow Launching”."
