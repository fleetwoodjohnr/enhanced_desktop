"""The one place a tool call is allowed or refused.

Every call goes through Registry.check() immediately before it runs, against
the switches as they are at that moment. The planner and the model only ever
see tools that are switched on, but that is a courtesy; this is the boundary.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .access import AccessStore
from .base import CATEGORIES, AccessDisabled, Integration, Tool

APP_PREFIX = "app:"
# How long the installed-application list is trusted before asking Gio again.
APP_CACHE_SECONDS = 30


def app_integration_id(desktop_id: str) -> str:
    return APP_PREFIX + (desktop_id or "")


class Registry:
    def __init__(self, store: AccessStore, integrations: list[Integration], tools: list[Tool],
                 app_integrations: Callable[[], list[Integration]] = lambda: [],
                 app_capabilities: tuple = (),
                 accounts: Callable[[], list[Integration]] = lambda: [],
                 account_capabilities: tuple = ()):
        self.store = store
        # The capabilities every installed app (or connected account) can
        # offer, for judging shared tools before any particular one is named.
        self.app_capabilities = {c.id: c for c in app_capabilities}
        self.account_capabilities = {c.id: c for c in account_capabilities}
        self._fixed = {integration.id: integration for integration in integrations}
        self.tools = {tool.name: tool for tool in tools}
        self._app_integrations = app_integrations
        self._account_integrations = accounts
        self._apps: dict[str, Integration] = {}
        self._apps_at = 0.0
        self.lock = threading.RLock()

    @property
    def _static(self) -> dict[str, Integration]:
        """Service integrations: the built-in ones and every connected account.

        Accounts are read each time; the list is a small file the user changes
        from settings, and a new account must be usable at once.
        """
        combined = dict(self._fixed)
        try:
            for integration in self._account_integrations():
                combined[integration.id] = integration
        except Exception:  # noqa: BLE001 - an unreadable account list grants nothing
            pass
        return combined

    # -- what exists -------------------------------------------------------

    def apps(self) -> dict[str, Integration]:
        with self.lock:
            if not self._apps_at or time.monotonic() - self._apps_at > APP_CACHE_SECONDS:
                try:
                    self._apps = {i.id: i for i in self._app_integrations()}
                except Exception:  # noqa: BLE001 - an unreadable app list grants nothing
                    self._apps = {}
                self._apps_at = time.monotonic()
            return dict(self._apps)

    def integrations(self) -> list[Integration]:
        return [*self._static.values(), *self.apps().values()]

    def get(self, integration_id: str) -> Integration | None:
        return self._static.get(integration_id) or self.apps().get(integration_id)

    # -- the switches ------------------------------------------------------

    def entry(self, integration: Integration, data: dict | None = None) -> dict:
        """The effective switches of one integration, defaults filled in."""
        data = data if data is not None else self.store.snapshot()
        stored = data["apps"].get(integration.id, {})
        if "enabled" in stored:
            enabled = stored["enabled"]
        elif integration.instance_of == "app":
            # Installed when App Access first appeared: keeps today's access.
            enabled = integration.desktop_id in data["known_apps"]
        else:
            enabled = integration.default_enabled
        capabilities = {c.id: stored.get("capabilities", {}).get(c.id, c.default)
                        for c in integration.capabilities}
        confirm = {c.id: True if c.confirm_locked else stored.get("confirm", {}).get(c.id, True)
                   for c in integration.capabilities if c.risk == "high"}
        new = (integration.instance_of == "app" and "enabled" not in stored and
               integration.desktop_id not in data["known_apps"])
        return {"enabled": enabled, "capabilities": capabilities, "confirm": confirm, "new": new}

    def paused(self) -> bool:
        return self.store.snapshot()["paused"]

    def enabled(self, integration_id: str, data: dict | None = None) -> bool:
        integration = self.get(integration_id)
        if integration is None:
            return False
        data = data if data is not None else self.store.snapshot()
        return not data["paused"] and self.entry(integration, data)["enabled"]

    def allows(self, integration_id: str, capability: str, data: dict | None = None) -> bool:
        integration = self.get(integration_id)
        if integration is None:
            return False
        data = data if data is not None else self.store.snapshot()
        entry = self.entry(integration, data)
        return not data["paused"] and entry["enabled"] and entry["capabilities"].get(capability, False)

    # -- checking a call ---------------------------------------------------

    def resolve(self, name: str, arguments: dict) -> tuple[Tool, Integration | None, str]:
        tool = self.tools[name]
        integration_id = tool.integration_for(arguments)
        return tool, (self.get(integration_id) if integration_id else None), tool.capability_for(arguments)

    def check(self, name: str, arguments: dict, window_titles: Callable[[str], list[str]] | None = None):
        """Raise AccessDisabled unless this exact call is allowed right now."""
        tool, integration, capability = self.resolve(name, arguments)
        data = self.store.snapshot()
        if data["paused"]:
            raise AccessDisabled(integration.id if integration else "", "", paused=True)
        if integration is None:
            # Tools that span apps (listing them) need at least one app on;
            # their handlers filter what they return to the enabled ones.
            if tool.integration == "" and self.any_app_enabled(data):
                return tool, None, capability
            desktop_id = arguments.get("desktop_id") or arguments.get("app") or ""
            raise AccessDisabled(app_integration_id(desktop_id), desktop_id or "This app",
                                 reason=f"{desktop_id or 'That application'} is not available "
                                        "to CLIVE; turn it on in CLIVE settings (App Access)")
        entry = self.entry(integration, data)
        if not entry["enabled"]:
            raise AccessDisabled(integration.id, integration.name)
        if not entry["capabilities"].get(capability, False):
            label = integration.capability(capability).label if integration.capability(capability) else capability
            raise AccessDisabled(integration.id, integration.name, capability, label)
        self._check_guards(tool, integration, arguments, data, window_titles)
        return tool, integration, capability

    def _off_owners(self, data: dict, skip: str) -> list[Integration]:
        return [i for i in self._static.values()
                if i.id != skip and not (not data["paused"] and self.entry(i, data)["enabled"])]

    def _check_guards(self, tool: Tool, integration: Integration, arguments: dict, data: dict,
                      window_titles) -> None:
        """Close the side doors into integrations that are switched off."""
        owners = self._off_owners(data, integration.id)
        if not owners:
            return
        for key in tool.path_keys:
            value = arguments.get(key)
            if not isinstance(value, str):
                continue
            target = Path(value).expanduser().resolve()
            for owner in owners:
                for root in owner.guards.paths():
                    root_path = Path(root).expanduser().resolve()
                    if target == root_path or target.is_relative_to(root_path):
                        raise AccessDisabled(owner.id, owner.name, reason=(
                            f"{value} belongs to {owner.name}, which is turned off in CLIVE "
                            "settings (App Access)"))
        url = arguments.get("url")
        if isinstance(url, str):
            from urllib.parse import urlsplit
            host = (urlsplit(url).hostname or "").lower()
            for owner in owners:
                if any(host == h or host.endswith("." + h) for h in owner.guards.hosts):
                    raise AccessDisabled(owner.id, owner.name, reason=(
                        f"{host} is part of {owner.name}, which is turned off in CLIVE "
                        "settings (App Access)"))
        if integration.instance_of == "app":
            for owner in owners:
                if self._app_reaches(integration, owner):
                    raise AccessDisabled(owner.id, owner.name, reason=(
                        f"{integration.name} can show {owner.name}, which is turned off in CLIVE "
                        "settings (App Access)"))
            if window_titles is not None:
                markers = [(owner, marker) for owner in owners for marker in owner.guards.title_markers]
                if markers:
                    titles = [t.casefold() for t in window_titles(integration.desktop_id)]
                    for owner, marker in markers:
                        if any(marker.casefold() in title for title in titles):
                            raise AccessDisabled(owner.id, owner.name, reason=(
                                f"{integration.name} is showing {owner.name}, which is turned off "
                                "in CLIVE settings (App Access)"))

    @staticmethod
    def _app_reaches(app: Integration, owner: Integration) -> bool:
        desktop_id = app.desktop_id.casefold()
        if any(candidate.casefold() in desktop_id for candidate in owner.guards.desktop_ids):
            return True
        return bool(set(app.app_categories).intersection(owner.guards.categories))

    def closed_paths(self) -> list[str]:
        """Folders owned by integrations that are switched off, for sandboxes."""
        data = self.store.snapshot()
        paths = []
        for owner in self._off_owners(data, ""):
            try:
                paths += [str(Path(p).expanduser()) for p in owner.guards.paths()]
            except Exception:  # noqa: BLE001 - a broken guard must not open the folder
                continue
        return paths

    def any_app_enabled(self, data: dict | None = None) -> bool:
        data = data if data is not None else self.store.snapshot()
        return not data["paused"] and any(self.entry(app, data)["enabled"] for app in self.apps().values())

    def enabled_apps(self) -> list[Integration]:
        data = self.store.snapshot()
        if data["paused"]:
            return []
        return [app for app in self.apps().values() if self.entry(app, data)["enabled"]]

    def needs_confirmation(self, name: str, arguments: dict) -> bool:
        tool, integration, capability = self.resolve(name, arguments)
        if integration is None:
            return False
        cap = integration.capability(capability)
        if cap is None or cap.risk != "high":
            return False
        return self.entry(integration)["confirm"].get(capability, True)

    def risk(self, name: str) -> str:
        """The highest risk any use of this tool can carry, for approval modes."""
        tool = self.tools[name]
        if tool.per_app:
            template = {**self.app_capabilities, **self.account_capabilities}
            capabilities = [template.get(c) for c in tool.capability_ids()]
        else:
            integration = self._static.get(tool.integration)
            capabilities = [integration.capability(c) if integration else None
                            for c in tool.capability_ids()]
        order = {"low": 0, "normal": 1, "high": 2}
        # A capability nobody declared is treated as one that acts.
        return max((c.risk if c else "normal" for c in capabilities),
                   key=order.__getitem__, default="normal")

    # -- what the model is shown ------------------------------------------

    def enabled_tool_names(self) -> list[str]:
        """Tools at least one switched-on integration lets CLIVE use."""
        data = self.store.snapshot()
        if data["paused"]:
            return []
        instances = [i for i in [*self.apps().values(), *self._static.values()] if i.instance_of]
        enabled = [(i, self.entry(i, data)) for i in instances]
        enabled = [(i, entry) for i, entry in enabled if entry["enabled"]]
        apps = [entry for i, entry in enabled if i.instance_of == "app"]
        names = []
        for name, tool in self.tools.items():
            if tool.integration == "":
                usable = bool(apps)
            elif tool.per_app:
                usable = any(entry["capabilities"].get(c, False)
                             for i, entry in enabled if i.instance_of == tool.family
                             for c in tool.capability_ids())
            else:
                integration = self._static.get(tool.integration)
                entry = self.entry(integration, data) if integration else None
                usable = bool(entry and entry["enabled"] and any(
                    entry["capabilities"].get(c, False) for c in tool.capability_ids()))
            if usable:
                names.append(name)
        return names

    def specs(self, names) -> list[dict]:
        wanted = set(names)
        return [tool.spec() for name, tool in self.tools.items() if name in wanted]

    def turned_off(self) -> list[str]:
        """Service integrations that exist but are switched off, by name."""
        data = self.store.snapshot()
        return [i.name for i in self._static.values()
                if data["paused"] or not self.entry(i, data)["enabled"]]

    def planner_catalog(self) -> str:
        """One line per switched-on integration: what it can do and with which tools."""
        data = self.store.snapshot()
        if data["paused"]:
            return "All app access is paused by the user. No tools are available.\n"
        enabled = set(self.enabled_tool_names())
        lines = []
        for integration in self._static.values():
            entry = self.entry(integration, data)
            if not entry["enabled"] or integration.instance_of:
                continue
            tools = [n for n, t in self.tools.items() if n in enabled and t.integration == integration.id]
            if not tools:
                continue
            allowed = [c.label.lower() for c in integration.capabilities if entry["capabilities"].get(c.id)]
            lines.append(f"- {integration.name} ({', '.join(allowed)}): {', '.join(tools)}")
        for account in self._static.values():
            if account.instance_of != "mail" or not self.entry(account, data)["enabled"]:
                continue
            entry = self.entry(account, data)
            tools = [n for n, t in self.tools.items() if n in enabled and t.family == "mail" and
                     any(entry["capabilities"].get(c) for c in t.capability_ids())]
            allowed = [c.label.lower() for c in account.capabilities if entry["capabilities"].get(c.id)]
            if tools:
                lines.append(f"- {account.name}, account=\"{account.id}\" ({', '.join(allowed)}): "
                             + ", ".join(tools))
        gui = [n for n, t in self.tools.items() if n in enabled and t.per_app and t.family == "app"]
        if gui:
            lines.append("- Desktop applications (per app below): " + ", ".join(gui))
        off = self.turned_off()
        text = "Apps CLIVE may use:\n" + ("\n".join(lines) if lines else "- none") + "\n"
        if off:
            text += ("Turned off by the user: " + ", ".join(off) + ". If a request needs one of "
                     "these, say that its access is turned off in CLIVE settings (App Access) and "
                     "stop. Never work around a switched-off app through another app or tool.\n")
        return text

    # -- for the App Access page ------------------------------------------

    def describe(self, usage: dict | None = None) -> dict:
        data = self.store.snapshot()
        usage = usage or {}
        rows = []
        for integration in self.integrations():
            entry = self.entry(integration, data)
            try:
                status = integration.status()
            except Exception:  # noqa: BLE001 - a broken probe must not hide the switch
                status = {"state": "unavailable", "detail": "Could not check this app"}
            rows.append({
                "id": integration.id, "name": integration.name, "category": integration.category,
                "icon": integration.icon, "description": integration.description,
                "desktop_id": integration.desktop_id, "enabled": entry["enabled"],
                "new": entry["new"], "access": "read_write" if integration.writes else "read_only",
                "status": status, "last_used": usage.get(integration.id),
                "options": self._options(integration),
                "capabilities": [{"id": c.id, "label": c.label, "kind": c.kind, "risk": c.risk,
                                  "description": c.description,
                                  "enabled": entry["capabilities"][c.id],
                                  "confirm": entry["confirm"].get(c.id),
                                  "confirm_locked": c.confirm_locked}
                                 for c in integration.capabilities],
            })
        return {"paused": data["paused"], "error": self.store.error, "apps": rows,
                "categories": [{"id": key, "name": name} for key, name in CATEGORIES]}

    @staticmethod
    def _options(integration: Integration) -> list:
        try:
            return integration.options()
        except Exception:  # noqa: BLE001 - a broken option list must not hide the app
            return []

    def set_option(self, integration_id: str, key: str, value) -> None:
        integration = self.get(integration_id)
        if integration is None or integration.set_option is None:
            raise ValueError("This app has no settings of its own")
        if key not in {option["key"] for option in self._options(integration)}:
            raise ValueError("Unknown setting for this app")
        integration.set_option(key, value)

    def summary(self) -> dict:
        data = self.store.snapshot()
        integrations = self.integrations()
        on = [i for i in integrations if not data["paused"] and self.entry(i, data)["enabled"]]
        return {"paused": data["paused"], "enabled": len(on), "total": len(integrations),
                "off": self.turned_off()}

    # -- changing the switches ---------------------------------------------

    def set(self, integration_id: str, enabled=None, capabilities=None, confirm=None) -> dict:
        integration = self.get(integration_id)
        if integration is None:
            raise ValueError("That app is not known to CLIVE")
        known = {c.id: c for c in integration.capabilities}
        for values in (capabilities or {}, confirm or {}):
            if not all(isinstance(v, bool) and k in known for k, v in values.items()):
                raise ValueError("Unknown permission for this app")
        if any(known[k].confirm_locked and not v for k, v in (confirm or {}).items()):
            raise ValueError("This action always asks before it runs")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("Access must be on or off")

        def change(data):
            entry = data["apps"].setdefault(integration_id, {})
            if enabled is not None:
                entry["enabled"] = enabled
            if capabilities:
                entry.setdefault("capabilities", {}).update(capabilities)
            if confirm:
                entry.setdefault("confirm", {}).update(confirm)
        self.store.update(change)
        return self.entry(integration)

    def set_many(self, enabled: bool, ids=None) -> int:
        if not isinstance(enabled, bool):
            raise ValueError("Access must be on or off")
        targets = [i.id for i in self.integrations()] if ids is None else \
            [i for i in ids if isinstance(i, str) and self.get(i) is not None]

        def change(data):
            for integration_id in targets:
                data["apps"].setdefault(integration_id, {})["enabled"] = enabled
        self.store.update(change)
        return len(targets)

    def pause(self, paused: bool) -> None:
        if not isinstance(paused, bool):
            raise ValueError("Pause must be on or off")
        self.store.update(lambda data: data.__setitem__("paused", paused))
