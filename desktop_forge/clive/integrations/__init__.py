"""CLIVE's integrations, and the one registry that enforces App Access.

To add an integration: write a module with `integration()` and `TOOLS`, then
list it in `build_registry`. The registry, the planner catalog, the approval
modes and the App Access page all read the declarations; nothing else needs
to know the new app exists.
"""
from __future__ import annotations

from . import apps, calendar, files, git, mail, media, notes, tasks, terminal, web
from .access import ACCESS_PATH, AccessStore
from .base import AccessDisabled, Capability, Guards, Integration, Tool
from .registry import APP_PREFIX, Registry, app_integration_id

ALL_TOOLS = (*files.TOOLS, *tasks.TOOLS, *calendar.TOOLS, *notes.TOOLS, *web.TOOLS, *terminal.TOOLS, *git.TOOLS,
             *mail.TOOLS, *apps.TOOLS, *media.TOOLS)
_CAPABILITIES = {files.ID: files.CAPABILITIES, tasks.ID: tasks.CAPABILITIES, web.ID: web.CAPABILITIES,
                 notes.ID: notes.CAPABILITIES, calendar.ID: calendar.CAPABILITIES, terminal.ID: terminal.CAPABILITIES, git.ID: git.CAPABILITIES}
APP_TEMPLATE = apps.APP_CAPABILITIES + media.CAPABILITIES + mail.CAPABILITIES
_ORDER = {"low": 0, "normal": 1, "high": 2}


def declared_risk(tool: Tool) -> str:
    """A tool's highest possible risk, from the declarations alone."""
    table = APP_TEMPLATE if tool.per_app else _CAPABILITIES.get(tool.integration, ())
    risks = [c.risk for c in table if c.id in tool.capability_ids()]
    return max(risks, key=_ORDER.__getitem__, default="normal")


def build_registry(store: AccessStore, apps_factory, key_state=lambda: None) -> Registry:
    return Registry(
        store,
        [files.integration(), tasks.integration(), calendar.integration(), notes.integration(),
         web.integration(key_state),
         terminal.integration(), git.integration()],
        list(ALL_TOOLS),
        app_integrations=lambda: apps.app_integrations(apps_factory),
        app_capabilities=apps.APP_CAPABILITIES + media.CAPABILITIES,
        accounts=mail.account_integrations,
        account_capabilities=mail.CAPABILITIES,
    )


__all__ = ["ACCESS_PATH", "ALL_TOOLS", "declared_risk", "APP_PREFIX", "AccessDisabled", "AccessStore", "Capability",
           "Guards", "Integration", "Registry", "Tool", "app_integration_id", "build_registry"]
