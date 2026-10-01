"""Shell commands, run inside a bubblewrap sandbox built from the other switches.

The sandbox is the enforcement, not the command's text: whatever a command
tries, it sees a read-only system, no session bus, keyring, Wayland or X11
socket, no other processes' environment, and -- unless allowed -- no network
and no writable home. Paths of apps switched off in App Access are hidden, and
with Files off the whole home folder is.

Administrator commands are the exception, and are labelled so: they cannot be
sandboxed, so CLIVE opens them in a Terminal window for you to read and run,
and never sees your password or their output.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from ..policy import local_path
from .base import Capability, Integration, Tool
from .schemas import STRING

ID = "terminal"
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
MAX_OUTPUT = 20_000
ADMIN_COMMANDS = ("sudo", "pkexec", "doas", "su", "run0")
# Package managers and the subcommands that change what is installed.
PACKAGE_COMMANDS = {
    "dnf": {"install", "remove", "erase", "upgrade", "update", "reinstall", "downgrade", "autoremove",
            "group", "swap", "distro-sync"},
    "yum": {"install", "remove", "erase", "upgrade", "update", "reinstall", "downgrade", "autoremove"},
    "rpm-ostree": {"install", "uninstall", "upgrade", "override", "rebase"},
    "rpm": {"-i", "-U", "-e", "-F", "--install", "--upgrade", "--erase", "--freshen"},
    "flatpak": {"install", "uninstall", "update", "remove"},
    "pip": {"install", "uninstall"}, "pip3": {"install", "uninstall"},
    "pipx": {"install", "uninstall", "upgrade", "reinstall"},
    "npm": {"install", "i", "uninstall", "remove", "update"},
    "cargo": {"install", "uninstall"}, "gem": {"install", "uninstall", "update"},
    "snap": {"install", "remove", "refresh"}, "brew": {"install", "uninstall", "upgrade"},
}
# Never visible to a sandboxed command, whatever else is allowed: credentials,
# browser and mail profiles, and CLIVE's own records and switches.
PRIVATE_PATHS = (
    ".ssh", ".gnupg", ".password-store", ".local/share/keyrings", ".pki", ".netrc",
    ".git-credentials", ".docker/config.json", ".var/app", ".mozilla", ".thunderbird",
    ".config/BraveSoftware", ".config/google-chrome", ".config/chromium", ".config/Signal",
    ".config/discord", ".config/Slack", ".config/evolution", ".local/share/evolution",
    ".config/goa-1.0", ".config/desktop-forge", ".local/share/desktop-forge",
)

CAPABILITIES = (
    Capability("run", "Run commands", "execute", "normal",
               "Inside a sandbox: the system is read-only, and nothing below is allowed unless "
               "you turn it on."),
    Capability("read_output", "See command output", "read", "low",
               "Without this, CLIVE sees only whether a command succeeded."),
    Capability("modify", "Change files", "modify", "normal",
               "Lets commands write in your visible home folders. Hidden folders and files -- "
               "startup scripts, autostart, settings -- stay read-only, and credentials, browser "
               "and mail profiles and CLIVE's own settings stay hidden.", default=False),
    Capability("network", "Use the network", "execute", "normal",
               "Lets commands reach the internet and services on your network.", default=False),
    Capability("install", "Install and remove software", "execute", "high",
               "Package-manager commands such as flatpak, pip or dnf. User-level installs are "
               "also possible whenever Change files and Use the network are on.", default=False),
    Capability("admin", "Run as administrator", "execute", "high",
               "Opens the command in a Terminal window for you to read and run with your "
               "password. It can bypass every other restriction, so it always asks first.",
               default=False, confirm_locked=True),
)


def _status():
    if shutil.which("bwrap") is None:
        return {"state": "unavailable", "detail": "Install bubblewrap (bwrap) to use the sandbox"}
    return {"state": "ready", "detail": "Commands run in a bubblewrap sandbox"}


def integration() -> Integration:
    return Integration(ID, "Terminal", "development", "utilities-terminal-symbolic",
                       "Run shell commands in a sandbox built from your other App Access switches.",
                       CAPABILITIES, default_enabled=False, status=_status)


def _segments(command: str) -> list[list[str]]:
    """The simple commands of a command line, split on ; && || | and newlines."""
    parts = re.split(r"\|\||&&|[;|\n]", command)
    result = []
    for part in parts:
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        # Skip leading environment assignments (FOO=1 command).
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words = words[1:]
        if words:
            result.append(words)
    return result


def classify(command: str) -> str:
    """Which capability a command needs: admin, install, or plain run.

    Best effort by design: the sandbox, not this, is what keeps a plain run
    from writing or reaching the network. This decides whether to ask first.
    """
    kind = "run"
    for words in _segments(command):
        program = os.path.basename(words[0])
        if program in ADMIN_COMMANDS:
            return "admin"
        rest = words[1:]
        if program.startswith("python") and rest[:2] == ["-m", "pip"]:
            program, rest = "pip", rest[2:]
        subcommands = PACKAGE_COMMANDS.get(program)
        if subcommands and any(word in subcommands for word in rest[:3]):
            kind = "install"
    return kind


def home_dot_entries(home: str) -> list[str]:
    """Every hidden entry directly in the home folder: shell startup files,
    autostart, systemd user units, desktop entries -- anything that runs later,
    outside any sandbox, if a sandboxed command could write it."""
    try:
        return sorted(str(Path(home, name)) for name in os.listdir(home) if name.startswith("."))
    except OSError:
        return []


def sandbox_arguments(command: str, cwd: str, *, home: str, runtime: str, write: bool, network: bool,
                      hide_home: bool, hidden=(), readonly=(), writable=()) -> list[str]:
    """The complete bwrap command line; a pure function so it can be tested.

    `readonly` is re-mounted read-only after a writable home, so "Change files"
    never reaches what runs at the next login. `writable` opens single folders
    (a repository) inside an otherwise read-only home.
    """
    arguments = ["bwrap", "--die-with-parent", "--new-session",
                 "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
                 "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                 # A private /tmp and runtime directory: no X11, Wayland, PipeWire,
                 # keyring or session-bus sockets to connect to.
                 "--tmpfs", "/tmp", "--tmpfs", runtime]
    if not network:
        # Also removes abstract-namespace sockets such as Xwayland's.
        arguments.append("--unshare-net")
    if hide_home:
        arguments += ["--tmpfs", home]
    elif write:
        arguments += ["--bind", home, home]
    else:
        # Re-exposed read-only: a home folder under /tmp would otherwise be
        # hidden by the private /tmp above.
        arguments += ["--ro-bind", home, home]
    if write and not hide_home:
        for path in readonly:
            if os.path.exists(path):
                arguments += ["--ro-bind", path, path]
    for path in writable:
        if not hide_home and os.path.isdir(path):
            arguments += ["--bind", path, path]
    for path in hidden:
        if hide_home and Path(path).is_relative_to(home):
            continue
        if os.path.isdir(path):
            arguments += ["--tmpfs", path]
        elif os.path.exists(path):
            arguments += ["--ro-bind", "/dev/null", path]
    arguments += ["--chdir", cwd if not hide_home else "/", "--clearenv",
                  "--setenv", "HOME", home, "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                  "--setenv", "LANG", os.environ.get("LANG", "C.UTF-8"), "--setenv", "TERM", "dumb",
                  "--", "/bin/bash", "--noprofile", "--norc", "-c", command]
    return arguments


def _hidden_paths(ctx) -> list[str]:
    home = Path.home()
    paths = [str(home / relative) for relative in PRIVATE_PATHS]
    paths += ctx.registry.closed_paths()
    return paths


def _open_in_terminal(command: str) -> str:
    script = f"{command}\nstatus=$?\necho\necho \"Finished with status $status. Press Enter to close.\"\nread -r _"
    for candidate in (["ptyxis", "--new-window", "--"], ["kgx", "--"], ["gnome-terminal", "--"],
                      ["konsole", "-e"], ["xterm", "-e"]):
        if shutil.which(candidate[0]):
            subprocess.Popen([*candidate, "/bin/bash", "-c", script], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return candidate[0]
    raise RuntimeError("No terminal application was found to run the command in")


def _run(ctx, a):
    command = a["command"]
    kind = classify(command)
    if kind == "admin":
        terminal = _open_in_terminal(command)
        return {"opened_in": terminal, "command": command,
                "note": "The command is waiting in a Terminal window for the user to read and run. "
                        "CLIVE cannot see its output or the password."}
    if shutil.which("bwrap") is None:
        raise RuntimeError("The sandbox (bubblewrap) is not installed, so commands cannot run")
    home = str(Path.home())
    cwd = str(local_path(a.get("cwd") or home))
    allows = lambda capability: ctx.registry.allows(ID, capability)  # noqa: E731
    install = kind == "install"
    arguments = sandbox_arguments(
        command, cwd, home=home,
        runtime=os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}",
        # A confirmed install needs to write where packages go and download them.
        write=allows("modify") or install, network=allows("network") or install,
        hide_home=not ctx.registry.enabled("files"), hidden=_hidden_paths(ctx),
        readonly=home_dot_entries(home))
    timeout = max(1, min(int(a.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT))
    try:
        done = subprocess.run(arguments, capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or "")) if isinstance(exc.stdout, str) else ""
        result = {"exit_code": None, "timed_out": True}
        if allows("read_output"):
            result["output"] = output[-MAX_OUTPUT:]
        return result
    result = {"exit_code": done.returncode}
    if allows("read_output"):
        output = done.stdout + (("\n" + done.stderr) if done.stderr else "")
        result.update(output=output[:MAX_OUTPUT], truncated=len(output) > MAX_OUTPUT)
    else:
        result["note"] = "Output is hidden: See command output is off in App Access."
    return result


TOOLS = (
    Tool("terminal_run", "Run a bash command in a sandbox. cwd is an absolute folder in the home "
         "folder. Output is returned when allowed. Commands starting with sudo open in a Terminal "
         "window for the user instead.",
         {"command": {"type": "string", "minLength": 1, "maxLength": 8000}, "cwd": STRING,
          "timeout": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT}},
         lambda a: classify(a.get("command", "")), _run, ID, "Running a terminal command…",
         required=["command"], describe=lambda a: a["command"], path_keys=("cwd",),
         possible=("run", "install", "admin")),
)
