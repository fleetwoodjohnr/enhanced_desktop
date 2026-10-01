"""The contract every CLIVE integration implements.

An integration is one thing a person can switch on or off in App Access:
Files, Calendar, a mail account, one installed application. It declares the
capabilities it exposes (read, search, create, modify, delete, execute,
monitor), the tools that use each capability, and the indirect routes it owns
so that turning it off also closes them. The registry enforces all of it at
call time; nothing here trusts the user interface to have hidden a switch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

KINDS = ("read", "search", "create", "modify", "delete", "execute", "monitor")
# low: runs as soon as the task allows it. normal: follows the task approval
# mode. high: always shows exactly what it will do and waits for Confirm.
RISKS = ("low", "normal", "high")

CATEGORIES = (
    ("communication", "Communication"),
    ("productivity", "Productivity"),
    ("files", "Files & Documents"),
    ("web", "Web"),
    ("media", "Media"),
    ("development", "Development"),
    ("system", "System & Utilities"),
    ("apps", "Other Apps"),
)


class AccessDisabled(Exception):
    """The integration, or the capability a call needs, is switched off.

    Deliberately not a ScopeChanged: a scope change widens the approved task
    and, in unattended mode, is re-approved without anyone seeing it. A switch
    the user turned off must never be widened past.
    """

    def __init__(self, integration: str, name: str, capability: str = "", label: str = "",
                 paused: bool = False, reason: str = ""):
        self.integration, self.name, self.capability = integration, name, capability
        if paused:
            message = "All app access is paused in CLIVE. Resume it from the CLIVE menu or settings"
        elif reason:
            message = reason
        elif capability:
            message = (f"{name} is on, but “{label or capability}” is turned off "
                       "in CLIVE settings (App Access)")
        else:
            message = f"{name} access is turned off in CLIVE settings (App Access)"
        super().__init__(message)


@dataclass(frozen=True)
class Capability:
    id: str
    label: str
    kind: str
    risk: str = "low"
    description: str = ""
    default: bool = True
    # A few actions (administrator commands) can never be set to "don't ask".
    confirm_locked: bool = False

    def __post_init__(self):
        if self.kind not in KINDS or self.risk not in RISKS:
            raise ValueError(f"Invalid capability {self.id}: {self.kind}/{self.risk}")


@dataclass(frozen=True)
class Guards:
    """Indirect routes an integration owns, closed while it is switched off."""
    paths: Callable[[], list[str]] = lambda: []
    desktop_ids: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    title_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    # A capability ID, or a function of the arguments for tools whose effect
    # depends on them (mail_organize with action "archive" archives).
    capability: str | Callable[[dict], str]
    handler: Callable[[Any, dict], dict]
    # The integration a call belongs to: an ID, or a function of the
    # arguments for tools shared by many instances (every GUI app, every
    # mail account).
    integration: str | Callable[[dict], str]
    activity: str = "Working in {app}…"
    required: list[str] | None = None
    # One short line naming what a call touches, for the activity list and
    # the confirmation card.
    describe: Callable[[dict], str] | None = None
    # Argument names that carry paths, for the side-door checks.
    path_keys: tuple[str, ...] = ()
    # Every capability a callable `capability` can return, so the approval
    # mode can judge the tool before any arguments exist.
    possible: tuple[str, ...] = ()
    # For shared tools: which kind of instance the arguments pick out
    # ("app" for installed apps, "mail" for mail accounts).
    family: str = "app"
    # What the confirmation card shows, when the arguments alone do not say
    # it (an event ID means nothing; its title and time do). May do I/O.
    preview: Callable[[dict], str] | None = None

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "additionalProperties": False,
                           "properties": self.parameters,
                           "required": list(self.parameters) if self.required is None
                           else self.required}}}

    def capability_for(self, arguments: dict) -> str:
        return self.capability(arguments) if callable(self.capability) else self.capability

    def capability_ids(self) -> tuple[str, ...]:
        return self.possible if callable(self.capability) else (self.capability,)

    @property
    def per_app(self) -> bool:
        """Shared by every installed app, which the arguments pick out."""
        return callable(self.integration) or self.integration == ""

    def integration_for(self, arguments: dict) -> str:
        return self.integration(arguments) if callable(self.integration) else self.integration

    def confirmation_text(self, arguments: dict) -> str:
        if self.preview:
            try:
                return self.preview(arguments)[:2000]
            except Exception:  # noqa: BLE001 - fall back to what the arguments say
                pass
        return self.target(arguments)

    def target(self, arguments: dict) -> str:
        if self.describe:
            try:
                return self.describe(arguments)[:300]
            except Exception:  # noqa: BLE001 - a description must never break a call
                return ""
        return ""


@dataclass
class Integration:
    id: str
    name: str
    category: str
    icon: str
    description: str
    capabilities: tuple[Capability, ...]
    default_enabled: bool = False
    guards: Guards = field(default_factory=Guards)
    # A quick, non-blocking health report: {"state": "ready"|"needs_setup"|
    # "unavailable", "detail": "..."}.
    status: Callable[[], dict] = lambda: {"state": "ready", "detail": ""}
    # Set for integrations created per installed app or per account.
    instance_of: str = ""
    # The GUI app a person would recognise this integration by, if any.
    desktop_id: str = ""
    # For installed apps: the desktop file's Categories, which is how an app
    # is linked to the service it can show (Email, Calendar, WebBrowser...).
    app_categories: tuple[str, ...] = ()
    # Settings of the integration itself, shown on its App Access row:
    # [{"key", "label", "kind": "folder"|"text"|"action", "value", "detail"}].
    options: Callable[[], list[dict]] = lambda: []
    # Apply one option; raises ValueError in words the user can act on.
    set_option: Callable[[str, Any], None] | None = None

    def capability(self, capability_id: str) -> Capability | None:
        return next((c for c in self.capabilities if c.id == capability_id), None)

    @property
    def writes(self) -> bool:
        return any(c.kind in ("create", "modify", "delete", "execute") for c in self.capabilities)
