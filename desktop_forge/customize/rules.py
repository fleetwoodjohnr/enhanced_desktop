"""Window rules: which windows a rule matches and what it does to them.

The extension applies rules (extension/rulesLogic.js mirrors the matching);
this module is the one place a rule's shape is checked, so a hand-edited
desktop.json or an imported profile cannot hand the extension a malformed one.
"""
from __future__ import annotations

import re

MAX_RULES = 100
TYPES = ("any", "normal", "dialog")
MODES = ("default", "float", "tile")

MATCH_DEFAULTS = {"app": "", "wm_class": "", "title": "", "title_regex": False, "type": "any"}
ACTION_DEFAULTS = {"mode": "default", "workspace": 0, "monitor": -1, "center": False, "maximize": False,
                   "fullscreen": False, "above": False, "sticky": False, "opacity": 1.0,
                   "no_effects": False, "width": 0, "height": 0}
NUMBERS = {"workspace": (0, 36), "monitor": (-1, 8), "opacity": (0.2, 1.0), "width": (0, 8192),
           "height": (0, 8192)}


def _text(value, limit=200) -> str:
    if not isinstance(value, str):
        raise ValueError("must be text")
    return value.strip()[:limit]


def validate_rule(rule) -> dict:
    """A complete rule with every field present, or ValueError."""
    if not isinstance(rule, dict):
        raise ValueError("Each window rule must be an object")
    match_in = rule.get("match") if isinstance(rule.get("match"), dict) else {}
    actions_in = rule.get("actions") if isinstance(rule.get("actions"), dict) else {}
    match = dict(MATCH_DEFAULTS)
    for key in ("app", "wm_class", "title"):
        if key in match_in:
            match[key] = _text(match_in[key])
    match["title_regex"] = match_in.get("title_regex") is True
    if match_in.get("type") in TYPES:
        match["type"] = match_in["type"]
    if not (match["app"] or match["wm_class"] or match["title"]):
        raise ValueError("A window rule needs an app, a window class or a title to match")
    if match["title_regex"]:
        try:
            re.compile(match["title"])
        except re.error as exc:
            raise ValueError(f"The title pattern is not valid: {exc}") from None
        # The Shell extension matches with JavaScript's RegExp, which has no
        # Python-only syntax such as (?P<name>...) or inline flags like (?i).
        if re.search(r"\(\?(P|[aiLmsux-]+[:)])", match["title"]):
            raise ValueError("Use plain pattern syntax; named groups and inline flags are not supported")
    actions = dict(ACTION_DEFAULTS)
    if actions_in.get("mode") in MODES:
        actions["mode"] = actions_in["mode"]
    for key in ("center", "maximize", "fullscreen", "above", "sticky", "no_effects"):
        actions[key] = actions_in.get(key) is True
    for key, (low, high) in NUMBERS.items():
        value = actions_in.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = max(low, min(high, value))
            actions[key] = round(float(value), 2) if key == "opacity" else int(value)
    name = _text(rule.get("name", ""), 80) if isinstance(rule.get("name", ""), str) else ""
    return {"name": name, "enabled": rule.get("enabled", True) is not False, "match": match,
            "actions": actions}


def validate_rules(rules) -> list[dict]:
    if not isinstance(rules, list):
        raise ValueError("Window rules must be a list")
    if len(rules) > MAX_RULES:
        raise ValueError(f"Keep window rules to at most {MAX_RULES}")
    return [validate_rule(rule) for rule in rules]


def describe(rule: dict) -> str:
    """A one-line summary such as "Firefox · Workspace 2, floating"."""
    match, actions = rule["match"], rule["actions"]
    target = match["app"].removesuffix(".desktop") or match["wm_class"] or f'title "{match["title"]}"'
    parts = []
    if actions["mode"] != "default":
        parts.append("floating" if actions["mode"] == "float" else "tiled")
    if actions["workspace"]:
        parts.append(f"workspace {actions['workspace']}")
    if actions["monitor"] >= 0:
        parts.append(f"display {actions['monitor'] + 1}")
    if actions["width"] and actions["height"]:
        parts.append(f"{actions['width']}×{actions['height']}")
    for key, label in (("center", "centered"), ("maximize", "maximized"), ("fullscreen", "fullscreen"),
                       ("above", "always on top"), ("sticky", "on every workspace"),
                       ("no_effects", "no effects")):
        if actions[key]:
            parts.append(label)
    if actions["opacity"] < 1:
        parts.append(f"{round(actions['opacity'] * 100)}% opaque")
    return f"{target} · {', '.join(parts) if parts else 'no changes'}"


def _same_app(wanted: str, desktop_id: str) -> bool:
    wanted, desktop_id = wanted.casefold(), (desktop_id or "").casefold()
    return wanted == desktop_id or wanted.removesuffix(".desktop") == desktop_id.removesuffix(".desktop")


def matches(rule: dict, window: dict) -> bool:
    """Whether a window (as the Shell extension describes it) matches a rule.

    Mirrored by matchesRule in extension/rulesLogic.js; the tests keep the
    two in step.
    """
    match = rule["match"]
    if match["app"] and not _same_app(match["app"], str(window.get("desktop_id", ""))):
        return False
    if match["wm_class"]:
        wanted = match["wm_class"].casefold()
        if wanted not in (str(window.get("wm_class", "")).casefold(),
                          str(window.get("wm_class_instance", "")).casefold()):
            return False
    if match["title"]:
        title = str(window.get("title", ""))
        if match["title_regex"]:
            try:
                if not re.search(match["title"], title):
                    return False
            except re.error:
                return False
        elif match["title"].casefold() not in title.casefold():
            return False
    kind = window.get("type", "normal")
    return match["type"] == "any" or match["type"] == kind
