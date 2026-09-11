"""Validate tool inputs and enforce the permissions shown in the task preview."""
from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit


class ScopeChanged(Exception):
    """The next action needs a new, visible task approval."""


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a public HTTP or HTTPS URL")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private network URLs are not supported by the web tool")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Private network URLs are not supported by the web tool")
    return value


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("File paths must be absolute")
    home = Path.home().resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(home):
        raise ValueError("CLIVE file tools work inside your home folder")
    relative = resolved.relative_to(home)
    if any(part.startswith(".") for part in relative.parts):
        raise ValueError("Hidden folders and credential files are outside CLIVE's file tools")
    return resolved


def check_scope(name: str, arguments: dict, plan: dict):
    permissions = plan["permissions"]
    if name not in permissions["tools"]:
        raise ScopeChanged(f"Add permission for {name}")
    for key in ("path", "source", "destination", "directory"):
        if key not in arguments:
            continue
        path = local_path(arguments[key])
        roots = [local_path(p) for p in permissions["folders"]]
        if not any(path == root or path.is_relative_to(root) for root in roots):
            raise ScopeChanged(f"Access to {path} was not in the approved task")
    if "app" in arguments and arguments["app"] not in permissions["apps"]:
        raise ScopeChanged(f"Control of {arguments['app']} was not in the approved task")
    if "desktop_id" in arguments and arguments["desktop_id"] not in permissions["apps"]:
        raise ScopeChanged(f"Use of {arguments['desktop_id']} was not in the approved task")
    if name in ("web_search", "web_fetch", "open_url"):
        if not permissions["web"]:
            raise ScopeChanged("Web access was not in the approved task")
    if "url" in arguments:
        public_url(arguments["url"])


# Ordered loosest-last, so the settings dialog can present them in this order.
APPROVAL_MODES = ("always", "read_only", "never")


def auto_approved(plan: dict, mode: str, read_only) -> bool:
    """Whether `mode` lets this plan run without the visible task approval.

    `read_only` is passed in rather than imported: tools.py already imports this
    module, so naming it here would be a cycle. An unrecognised mode falls
    through to False -- a mode CLIVE does not understand asks, it never assumes.

    This only removes the human stop. check_scope still runs on every call, so a
    tool outside the approved plan is still caught and still widens it.
    """
    if mode == "never":
        return True
    if mode == "read_only":
        return all(name in read_only for name in plan["permissions"]["tools"])
    return False


def plan_defaults() -> dict:
    return {"answer": "", "summary": "", "steps": [],
            "permissions": {"tools": [], "folders": [], "apps": [], "web": False}}


def normalize_plan(value) -> dict:
    """Fill in what the model left out, always with the narrowest value.

    Ollama does not enforce the schema's `required` list on every model: an
    ordinary greeting comes back as {"answer": "..."} on its own. A missing
    permission becomes an empty one, so a plan can only ever ask for less this
    way -- whatever it needs, it still has to name -- and an unrecognised key is
    dropped rather than carried into the approval preview.
    """
    if not isinstance(value, dict):
        raise ValueError("The task plan does not match CLIVE's required format")
    plan = plan_defaults()
    for key in ("answer", "summary", "steps"):
        if key in value:
            plan[key] = value[key]
    permissions = value.get("permissions")
    if isinstance(permissions, dict):
        for key in plan["permissions"]:
            if key in permissions:
                plan["permissions"][key] = permissions[key]
    return plan


def validate_plan(plan: dict, names: list[str]) -> dict:
    from jsonschema import ValidationError, validate
    plan = normalize_plan(plan)
    try:
        validate(plan, plan_schema(names))
    except ValidationError:
        # A ValidationError message quotes the failing value and the whole
        # schema, and this text is shown to the user. Say it plainly instead.
        raise ValueError("The task plan does not match CLIVE's required format") from None
    for root in plan["permissions"]["folders"]:
        local_path(root)
    return plan


def plan_schema(names: list[str]) -> dict:
    strings = {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 20}
    return {"type": "object", "additionalProperties": False,
        "required": ["answer", "summary", "steps", "permissions"],
        "properties": {
            "answer": {"type": "string", "maxLength": 12000},
            "summary": {"type": "string", "maxLength": 2000},
            "steps": strings,
            "permissions": {"type": "object", "additionalProperties": False,
                "required": ["tools", "folders", "apps", "web"], "properties": {
                    "tools": {"type": "array", "items": {"type": "string", "enum": names}, "uniqueItems": True},
                    "folders": strings, "apps": strings, "web": {"type": "boolean"}}}}}


def preview(plan: dict) -> str:
    p = plan["permissions"]
    parts = [plan["summary"], *[f"{i + 1}. {step}" for i, step in enumerate(plan["steps"])]]
    if p["folders"]:
        parts.append("Files in: " + ", ".join(p["folders"]))
    if p["apps"]:
        parts.append("Applications: " + ", ".join(p["apps"]))
    if p["web"]:
        parts.append("Access the web through Ollama search or your browser.")
    if any(t.startswith("desktop_") for t in p["tools"]):
        parts.append("Inspect and operate these applications. Screen images may be sent to the active cloud model. GNOME will ask which screen to share.")
    parts.append("Allowed tools: " + ", ".join(p["tools"]))
    return "\n\n".join(p for p in parts if p)
