"""Git repositories in the home folder, driven as argument lists -- never a shell.

Hooks are switched off for every command (a repository's hook is code CLIVE
never agreed to run), credential prompts are off, and output is bounded.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from ..policy import local_path
from .base import Capability, Integration, Tool
from .schemas import STRING, TEXT

ID = "git"
TIMEOUT = 60
MAX_OUTPUT = 20_000

CAPABILITIES = (
    Capability("read", "Read status, history and changes", "read", "low"),
    Capability("commit", "Commit changes", "modify", "normal"),
    Capability("branch", "Create and switch branches", "modify", "normal"),
    Capability("pull", "Pull from remotes", "modify", "normal",
               "Uses your network and keys, so it runs outside the sandbox; only for repositories "
               "you trust."),
    Capability("push", "Push to remotes", "execute", "high",
               "Publishes commits; always shows what will be pushed and asks first."),
)


def _status():
    if shutil.which("git") is None:
        return {"state": "unavailable", "detail": "Git is not installed"}
    return {"state": "ready", "detail": ""}


def integration() -> Integration:
    return Integration(ID, "Git", "development", "git-symbolic",
                       "Git repositories in your home folder: status, history, commits and branches.",
                       CAPABILITIES, default_enabled=False, status=_status)


def _repo(path: str) -> str:
    root = local_path(path)
    if not root.is_dir():
        raise ValueError("Name a repository folder in your home folder")
    return str(root)


# A repository's own .git/config can name programs git runs: an fsmonitor on
# every status, external diff and textconv drivers, an ssh command, "ext::"
# remotes. Command-line settings win over the repository's, so these switch
# the ones that can be switched off globally.
SAFE_CONFIG = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "diff.external=", "-c", "core.sshCommand=ssh",
               "-c", "protocol.ext.allow=never", "-c", "credential.interactive=never")


def run_git(repo: str, *arguments: str, timeout: int = TIMEOUT, sandbox: str = "read") -> dict:
    """Run git. sandbox: "read" (nothing writable), "repo" (only the repository
    writable), or "none" (network and credentials, for pull and push).

    Filter and diff drivers a repository configures can still run; inside the
    sandbox they see a read-only system with no network, session bus or keys.
    """
    root = _repo(repo)
    command = ["git", "-C", root, *SAFE_CONFIG, *arguments]
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": "",
                   "GIT_EDITOR": "true", "GIT_PAGER": "cat", "LC_ALL": "C.UTF-8"}
    if sandbox != "none" and shutil.which("bwrap"):
        from . import terminal
        home = str(Path.home())
        # The sandbox starts from an empty environment; git's own settings
        # travel with the command.
        prefixed = ["env", "GIT_TERMINAL_PROMPT=0", "GIT_EDITOR=true", "GIT_PAGER=cat",
                    "LC_ALL=C.UTF-8", *command]
        command = terminal.sandbox_arguments(
            shlex.join(prefixed), root, home=home,
            runtime=os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}",
            write=False, network=False, hide_home=False,
            hidden=[str(Path(home, p)) for p in terminal.PRIVATE_PATHS if p != ".gitconfig"],
            writable=[root] if sandbox == "repo" else [])
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env=environment, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {arguments[0]} took longer than {timeout} seconds") from None
    except FileNotFoundError:
        raise RuntimeError("Git is not installed") from None
    output = (done.stdout + done.stderr).strip()
    if done.returncode != 0 and "bwrap:" in output:
        raise RuntimeError("The Git sandbox could not start: " + output[-300:])
    if done.returncode != 0:
        # Git's own message is the useful part, bounded and without the command.
        raise ValueError(f"git {arguments[0]} failed: {output[-500:] or 'no output'}")
    return {"output": output[:MAX_OUTPUT], "truncated": len(output) > MAX_OUTPUT}


def _status_tool(_ctx, a):
    return run_git(a["repo"], "status", "--short", "--branch")


def _log(_ctx, a):
    return run_git(a["repo"], "log", f"-{int(a.get('limit', 20))}",
                   "--pretty=format:%h %ad %an %s", "--date=short")


def _diff(_ctx, a):
    arguments = ["diff", "--no-ext-diff", "--no-textconv", "--stat", "--patch"]
    if a.get("staged"):
        arguments.append("--cached")
    if a.get("path"):
        arguments += ["--", a["path"]]
    return run_git(a["repo"], *arguments)


def _show(_ctx, a):
    revision = a["revision"]
    if revision.startswith("-"):
        raise ValueError("Name a commit, not an option")
    return run_git(a["repo"], "show", "--no-ext-diff", "--no-textconv", "--stat", "--patch", revision)


def _commit(_ctx, a):
    paths = a.get("paths") or []
    if any(p.startswith("-") for p in paths):
        raise ValueError("Paths must not look like options")
    if paths:
        run_git(a["repo"], "add", "--", *paths, sandbox="repo")
    else:
        run_git(a["repo"], "add", "--all", sandbox="repo")
    return run_git(a["repo"], "commit", "--message", a["message"], sandbox="repo")


def _branch(_ctx, a):
    name = a["name"]
    if name.startswith("-"):
        raise ValueError("Name a branch, not an option")
    return run_git(a["repo"], "switch", *(["--create"] if a.get("create") else []), name, sandbox="repo")


def _pull(_ctx, a):
    return run_git(a["repo"], "pull", "--ff-only", timeout=120, sandbox="none")


def _push(_ctx, a):
    return run_git(a["repo"], "push", timeout=120, sandbox="none")


REPO = {"repo": STRING}

TOOLS = (
    Tool("git_status", "Show a repository's branch and changed files.", REPO, "read", _status_tool, ID,
         "Checking Git…", describe=lambda a: a["repo"], path_keys=("repo",)),
    Tool("git_log", "Show recent commits.", {**REPO, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
         "read", _log, ID, "Reading Git history…", required=["repo"], describe=lambda a: a["repo"],
         path_keys=("repo",)),
    Tool("git_diff", "Show uncommitted changes, optionally staged only or for one path.",
         {**REPO, "path": STRING, "staged": {"type": "boolean"}}, "read", _diff, ID, "Reading changes…",
         required=["repo"], describe=lambda a: a["repo"], path_keys=("repo",)),
    Tool("git_show", "Show one commit.", {**REPO, "revision": STRING}, "read", _show, ID,
         "Reading a commit…", describe=lambda a: f"{a['revision']} in {a['repo']}", path_keys=("repo",)),
    Tool("git_commit", "Stage the given paths (or every change) and commit them with a message.",
         {**REPO, "message": TEXT, "paths": {"type": "array", "items": STRING, "maxItems": 100}},
         "commit", _commit, ID, "Committing…", required=["repo", "message"],
         describe=lambda a: f"{a['repo']}: {a['message'][:80]}", path_keys=("repo",)),
    Tool("git_branch", "Switch to a branch, creating it when create is true.",
         {**REPO, "name": STRING, "create": {"type": "boolean"}}, "branch", _branch, ID,
         "Switching branch…", required=["repo", "name"], describe=lambda a: a["name"], path_keys=("repo",)),
    Tool("git_pull", "Pull the current branch, fast-forward only.", REPO, "pull", _pull, ID,
         "Pulling…", describe=lambda a: a["repo"], path_keys=("repo",)),
    Tool("git_push", "Push the current branch to its remote.", REPO, "push", _push, ID, "Pushing…",
         describe=lambda a: a["repo"], path_keys=("repo",)),
)
