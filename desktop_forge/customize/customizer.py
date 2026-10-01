"""One interface over every customization backend, plus preview and revert."""
from __future__ import annotations

import copy

from .backends import ChromeBackend, DesktopStore, DockBackend, GSettingsBackend, Unavailable, sync_window_css
from .registry import BY_ID, SETTINGS, Setting, validate


class Customizer:
    def __init__(self, desktop: DesktopStore | None = None, gsettings: GSettingsBackend | None = None,
                 chrome: ChromeBackend | None = None, window_css=sync_window_css,
                 dock: DockBackend | None = None):
        self.desktop = desktop or DesktopStore()
        self.gsettings = gsettings or GSettingsBackend()
        self.chrome = chrome or ChromeBackend()
        self.dock = dock or DockBackend()
        self._window_css = window_css

    def _system(self, setting: Setting):
        """The backend for a setting another program owns (GNOME, Dash to Dock)."""
        return self.dock if setting.backend == "dock" else self.gsettings

    @staticmethod
    def setting(setting_id: str) -> Setting:
        try:
            return BY_ID[setting_id]
        except KeyError:
            raise ValueError(f"Unknown setting {setting_id}") from None

    def problem(self, setting: Setting) -> str:
        """Why a setting cannot be changed here, or "" when it can."""
        if setting.backend not in ("gsettings", "dock"):
            return ""
        backend = self._system(setting)
        try:
            backend.check(setting)
        except Unavailable as exc:
            return str(exc)
        if not backend.writable(setting):
            return "Locked by your administrator"
        return ""

    def available(self, setting: Setting) -> bool:
        return setting.backend not in ("gsettings", "dock") or self._system(setting).available(setting)

    def get(self, setting_id: str):
        setting = self.setting(setting_id)
        if setting.backend in DesktopStore.backends:
            return self.desktop.read()[setting_id]
        if setting.backend == "chrome":
            return self.chrome.get(setting)
        return self._system(setting).get(setting)

    def default(self, setting_id: str):
        setting = self.setting(setting_id)
        if setting.backend == "gsettings":
            try:
                return self.gsettings.default(setting)
            except Unavailable:
                pass
        return copy.deepcopy(setting.default)

    def values(self, ids=None) -> dict:
        """Current values of the given (or every) available setting."""
        settings = [self.setting(i) for i in ids] if ids is not None else list(SETTINGS)
        stored = self.desktop.read()
        chrome_cache = {}
        values = {}
        for setting in settings:
            if setting.backend in DesktopStore.backends:
                values[setting.id] = stored[setting.id]
            elif setting.backend == "chrome":
                if not chrome_cache:
                    chrome_cache["config"] = self.chrome._load()
                surface, key = ChromeBackend._parts(setting)
                options = chrome_cache["config"].chrome_options(surface)
                values[setting.id] = options.get(key, "" if setting.kind == "color" else setting.default)
            elif self._system(setting).available(setting):
                values[setting.id] = self._system(setting).get(setting)
        return values

    def set(self, setting_id: str, value):
        applied, problems = self.set_many({setting_id: value})
        if problems:
            raise ValueError(problems[setting_id])
        return applied[setting_id]

    def set_many(self, values: dict) -> tuple[dict, dict]:
        """Validate and apply; returns (applied values, {id: problem}).

        One bad value does not stop the others, so a profile applies as much
        as this system supports and reports the rest.
        """
        applied, problems = {}, {}
        desktop, chrome, gsettings = {}, {}, []
        values = self._keep_edges_apart(dict(values), problems)
        values = self._couple_snapping(values)
        for setting_id, value in values.items():
            try:
                setting = self.setting(setting_id)
                value = validate(setting, value)
            except ValueError as exc:
                problems[setting_id] = str(exc)
                continue
            if setting.backend in DesktopStore.backends:
                desktop[setting_id] = value
            elif setting.backend == "chrome":
                chrome[setting] = value
            else:
                gsettings.append((setting, value))
        if desktop:
            try:
                written = self.desktop.write(desktop)
            except OSError as exc:
                problems.update({i: f"Could not save: {exc}" for i in desktop})
            else:
                applied.update(desktop)
                if any(BY_ID[i].backend == "gtkcss" for i in desktop):
                    try:
                        self._window_css(written)
                    except OSError as exc:
                        problems.update({i: f"Could not write the GTK style: {exc}"
                                         for i in desktop if BY_ID[i].backend == "gtkcss"})
        if chrome:
            try:
                self.chrome.write(chrome)
            except OSError as exc:
                problems.update({s.id: f"Could not save: {exc}" for s in chrome})
            else:
                applied.update({s.id: v for s, v in chrome.items()})
        for setting, value in gsettings:
            try:
                self._system(setting).set(setting, value)
            except (Unavailable, PermissionError, ValueError, TypeError) as exc:
                problems[setting.id] = str(exc)
            else:
                applied[setting.id] = value
        return applied, problems

    def _keep_edges_apart(self, values: dict, problems: dict) -> dict:
        """The dock and the top bar cannot share a screen edge (the dock
        would sit under the bar); a profile asking for that keeps the dock
        where it is."""
        if "dock.position" not in values and "chrome.top_bar.position" not in values:
            return values
        try:
            bar = values.get("chrome.top_bar.position") or self.get("chrome.top_bar.position")
            dock = values.get("dock.position") or self.get("dock.position")
        except (Unavailable, ValueError):
            return values
        if isinstance(bar, str) and isinstance(dock, str) and bar.upper() == dock.upper():
            blocked = "dock.position" if "dock.position" in values else "chrome.top_bar.position"
            values.pop(blocked)
            problems[blocked] = "The dock and the top bar cannot use the same screen edge"
        return values

    @staticmethod
    def _ours(quarters, gaps, tiling=False) -> bool:
        return (quarters is True or tiling is True or
                (isinstance(gaps, (int, float)) and not isinstance(gaps, bool) and gaps > 0))

    def _couple_snapping(self, values: dict) -> dict:
        """Desktop Forge's snapping (corners, gaps) and tiling replace GNOME's
        edge tiling: both reacting to the same drag would fight, and a
        window GNOME tiled to half the screen cannot be tiled. Turning any
        on switches GNOME's off in the same change, remembering whether it
        was on; turning them all off puts back what was there."""
        if not {"snap.quarters", "snap.gaps", "tiling.enabled"} & values.keys():
            return values
        edge = BY_ID["snap.edge_tiling"]
        if not self.available(edge):
            return values
        try:
            quarters, gaps, tiling = self.get("snap.quarters"), self.get("snap.gaps"), self.get("tiling.enabled")
            was = self._ours(quarters, gaps, tiling)
            now = self._ours(values.get("snap.quarters", quarters), values.get("snap.gaps", gaps),
                             values.get("tiling.enabled", tiling))
            if now:
                if not was:
                    values.setdefault("snap.gnome_edge_before", self.get("snap.edge_tiling") is True)
                values["snap.edge_tiling"] = False
            elif was and "snap.edge_tiling" not in values:
                values["snap.edge_tiling"] = values.get("snap.gnome_edge_before",
                                                        self.get("snap.gnome_edge_before")) is True
        except (Unavailable, ValueError):
            pass
        return values

    def expand(self, values: dict) -> dict:
        """The values a change actually writes, including settings changed
        along with it, so a preview can remember all of them."""
        return self._couple_snapping(dict(values))

    def reset(self, ids) -> tuple[dict, dict]:
        return self.set_many({setting_id: self.default(setting_id) for setting_id in ids})


class PreviewSession:
    """Changes applied live, remembered so they can all be undone at once.

    The first time a setting changes in a session its previous value is
    recorded; later changes to it keep that original. Keep forgets the
    originals; Revert puts them back.
    """

    def __init__(self, customizer: Customizer):
        self.customizer = customizer
        self.before: dict = {}

    def apply(self, values: dict) -> tuple[dict, dict]:
        values = self.customizer.expand(values)
        for setting_id in values:
            if setting_id in self.before:
                continue
            try:
                self.before[setting_id] = copy.deepcopy(self.customizer.get(setting_id))
            except (Unavailable, ValueError):
                pass
        return self.customizer.set_many(values)

    def changed(self) -> list[str]:
        """IDs whose value now differs from before the session."""
        if not self.before:
            return []
        current = self.customizer.values(list(self.before))
        return [i for i, value in self.before.items() if current.get(i, value) != value]

    @property
    def active(self) -> bool:
        return bool(self.before)

    def keep(self) -> None:
        self.before = {}

    def revert(self) -> dict:
        """Restore the originals; returns {id: problem} for any that failed."""
        before, self.before = self.before, {}
        _applied, problems = self.customizer.set_many(before)
        return problems
