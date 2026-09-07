"""Installs and enables the GNOME Shell extension, and manages the daemon.

Two moving parts have to be alive for widgets to appear: the extension (draws
them) and desktop-forged (feeds them). Neither is much use alone, so both are
handled here and reported together.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

UUID = "desktop-forge@jrf.local"
SERVICE = "desktop-forged.service"

# GNOME Shell's ExtensionState (js/misc/extensionUtils.js), unchanged across 45-50.
# Worth spelling out: INITIALIZED means the shell has *scanned* the extension,
# not that the user turned it on -- reading it as "enabled" is what made the
# Widgets page offer a re-login instead of the Enable button.
ACTIVE = 1
INACTIVE = 2
ERROR = 3
OUT_OF_DATE = 4
DOWNLOADING = 5
INITIALIZED = 6
DEACTIVATING = 7
ACTIVATING = 8

EXTENSIONS_DIR = os.path.join(
    GLib.get_user_data_dir(), "gnome-shell", "extensions"
)
INSTALL_PATH = os.path.join(EXTENSIONS_DIR, UUID)

# The extension source ships inside the checkout, one level up from the package.
SOURCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "extension"
)


@dataclass
class Status:
    installed: bool
    known: bool
    enabled: bool
    running: bool
    daemon_active: bool
    daemon_enabled: bool
    needs_relogin: bool
    shell_version: str
    # False when the shell refuses to let this extension be switched on or off
    # -- a distro-shipped extension, or a lockdown policy. Nothing the app does
    # can change that, so the UI must not offer a button for it.
    can_change: bool = True
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.enabled and self.running and self.daemon_active


def _shell_proxy() -> Gio.DBusProxy | None:
    try:
        return Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Shell",
            "/org/gnome/Shell",
            "org.gnome.Shell.Extensions",
            None,
        )
    except GLib.Error:
        return None


def status() -> Status:
    installed = os.path.isdir(INSTALL_PATH)
    proxy = _shell_proxy()

    known = enabled = running = False
    can_change = True
    # 0 is not a real ExtensionState. It stands for "the shell never told us",
    # which is the case whenever the proxy or the info lookup failed below.
    state = 0
    shell_version = ""
    detail = ""

    if proxy is not None:
        version = proxy.get_cached_property("ShellVersion")
        shell_version = version.get_string() if version is not None else ""
        try:
            info = proxy.call_sync(
                "GetExtensionInfo", GLib.Variant("(s)", (UUID,)),
                Gio.DBusCallFlags.NONE, 3000, None,
            ).unpack()[0]
        except GLib.Error as exc:
            detail = exc.message
            info = {}

        if info:
            # The shell only returns info for extensions it has scanned. An
            # empty reply means the directory exists on disk but this running
            # shell has never seen it -- the normal state after a first install
            # on Wayland, where the shell cannot rescan without a new session.
            known = True
            state = int(info.get("state", 0))
            # "Has the user turned it on" and "has the shell loaded it" are two
            # different facts, and the shell reports both. Take the first from
            # the `enabled` flag rather than inferring it from the state, which
            # cannot distinguish "scanned but off" from "on but not yet loaded".
            enabled = bool(info.get("enabled", False))
            running = state == ACTIVE
            can_change = info.get("canChange") is not False
            if state == ERROR:
                detail = str(info.get("error") or "The extension reported an error")
            elif state == OUT_OF_DATE:
                detail = "The extension is marked out of date for this shell version"
            elif not can_change:
                # Locked by system policy (an extension shipped by the distro,
                # or one disabled through a lockdown setting). Offering an
                # Enable button here would promise something that cannot work.
                detail = "GNOME Shell will not let this extension be changed"
            elif not enabled:
                detail = "Enable them to show them on the desktop."

    return Status(
        installed=installed,
        known=known,
        enabled=enabled,
        running=running,
        daemon_active=_systemctl_is("is-active"),
        daemon_enabled=_systemctl_is("is-enabled"),
        # Wayland cannot reload gnome-shell in place, so a freshly installed
        # extension genuinely cannot start until the session restarts. That is
        # true in exactly two cases: the shell has never seen the extension, or
        # it has seen it and the user has switched it on but it is still sitting
        # in INITIALIZED. An extension the shell knows about but that is simply
        # switched off needs a button press, not a new session.
        needs_relogin=installed and (
            not known or (enabled and state == INITIALIZED)
        ),
        shell_version=shell_version,
        can_change=can_change,
        detail=detail,
    )


def install() -> None:
    """Copy the extension into place, replacing any previous copy."""
    if not os.path.isdir(SOURCE_PATH):
        raise FileNotFoundError(f"Extension source not found at {SOURCE_PATH}")

    os.makedirs(EXTENSIONS_DIR, exist_ok=True)
    if os.path.isdir(INSTALL_PATH):
        shutil.rmtree(INSTALL_PATH)
    shutil.copytree(SOURCE_PATH, INSTALL_PATH)


def enable() -> tuple[bool, str]:
    """Ask the shell to enable the extension.

    Returns (ok, message). The shell refuses to enable an extension it has not
    scanned yet, which is normal immediately after a first install -- the
    caller turns that into the re-login prompt rather than an error.
    """
    proxy = _shell_proxy()
    if proxy is None:
        return False, "Could not talk to GNOME Shell"

    try:
        result = proxy.call_sync(
            "EnableExtension", GLib.Variant("(s)", (UUID,)),
            Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
    except GLib.Error as exc:
        return False, exc.message

    if result:
        return True, "Extension enabled"
    return False, "GNOME Shell has not loaded the extension yet"


def disable() -> None:
    proxy = _shell_proxy()
    if proxy is None:
        return
    try:
        proxy.call_sync(
            "DisableExtension", GLib.Variant("(s)", (UUID,)),
            Gio.DBusCallFlags.NONE, 5000, None,
        )
    except GLib.Error:
        pass


# -- daemon ---------------------------------------------------------------


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return None
    try:
        return subprocess.run(
            [systemctl, "--user", *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _systemctl_is(query: str) -> bool:
    proc = _systemctl(query, "--quiet", SERVICE)
    return proc is not None and proc.returncode == 0


def start_daemon() -> tuple[bool, str]:
    proc = _systemctl("enable", "--now", SERVICE)
    if proc is None:
        return False, "systemctl is not available"
    if proc.returncode == 0:
        return True, "Widget data service started"
    return False, (proc.stderr or proc.stdout or "systemctl failed").strip()


def stop_daemon() -> tuple[bool, str]:
    proc = _systemctl("disable", "--now", SERVICE)
    if proc is None:
        return False, "systemctl is not available"
    return proc.returncode == 0, (proc.stderr or "").strip()


def restart_daemon() -> None:
    _systemctl("restart", SERVICE)
