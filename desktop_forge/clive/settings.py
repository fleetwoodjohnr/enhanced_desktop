"""Private settings and credentials, separate from the widget configuration."""
from __future__ import annotations

import os
from pathlib import Path

from .. import config
from .attachments import MAX_FILES
from .policy import APPROVAL_MODES, local_path

BUS_NAME = "org.jrf.DesktopForge.Clive"
OBJECT_PATH = "/org/jrf/DesktopForge/Clive"
INTERFACE = "org.jrf.DesktopForge.Clive1"
SETTINGS_PATH = Path(config.CONFIG_DIR) / "clive.json"
DATA_PATH = Path(config.DATA_DIR) / "clive"
DEFAULTS = {
    "cloud_model": "gemma4:31b",
    "local_model": "qwen3.5:4b",
    "cloud_enabled": False,
    "free_account_confirmed": False,
    "local_context": 8192,
    "max_turns": 16,
    "approval_mode": "always",
    "system_prompt": "",
    "context_files": [],
}
# Inclusive bounds for the numeric settings. The dialog's spin rows are a
# convenience; these are the boundary that actually protects the agent.
RANGES = {"local_context": (2048, 131072), "max_turns": (4, 64)}
# Long enough for real standing instructions, short enough that it cannot crowd
# out the conversation in an 8k local context.
MAX_SYSTEM_PROMPT = 4000
KEYRING_UNAVAILABLE = ("GNOME Keyring is locked or unavailable. Unlock your login "
                       "keyring, then try again.")


def load_settings() -> dict:
    raw = config.read_json(str(SETTINGS_PATH)) or {}
    settings = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    # Hold the billing invariant on read, so every reader -- the transport, the
    # model test, the UI -- agrees. A hand-edited file cannot turn the cloud on
    # behind the confirmation.
    if not settings["free_account_confirmed"]:
        settings["cloud_enabled"] = False
    # context_files defaults to a list. Without the copy every caller would share
    # -- and could mutate -- the one list living inside DEFAULTS.
    settings["context_files"] = [p for p in settings["context_files"] if isinstance(p, str)]
    return settings


def validate_settings(changes: dict) -> tuple[dict, dict]:
    """Merge `changes` into the stored settings without writing anything.

    Every field is checked, so one bad value reports itself instead of hiding
    behind the first failure, and the caller can decide what to persist.
    Returns the merged settings and a per-field error map.
    """
    settings = load_settings()
    errors: dict[str, str] = {}

    for key, label in (("cloud_model", "Cloud model"), ("local_model", "Local fallback")):
        if key not in changes:
            continue
        value = str(changes[key]).strip()
        if not value or len(value) > 150 or any(c.isspace() for c in value):
            errors[key] = f"{label}: enter a valid Ollama model name"
        elif key == "local_model" and ("cloud" in value or "/" in value):
            errors[key] = "Local fallback: this must be a model that runs on your machine"
        else:
            settings[key] = value

    for key in ("cloud_enabled", "free_account_confirmed"):
        if key not in changes:
            continue
        if not isinstance(changes[key], bool):
            errors[key] = "This setting must be on or off"
        else:
            settings[key] = changes[key]

    for key, label in (("local_context", "Local context window"), ("max_turns", "Maximum task steps")):
        if key not in changes:
            continue
        value = changes[key]
        low, high = RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int):
            errors[key] = f"{label}: enter a whole number"
        elif not low <= value <= high:
            errors[key] = f"{label}: choose a value between {low} and {high}"
        else:
            settings[key] = value

    if "approval_mode" in changes:
        if changes["approval_mode"] not in APPROVAL_MODES:
            errors["approval_mode"] = "Task approval: choose one of the listed modes"
        else:
            settings["approval_mode"] = changes["approval_mode"]

    if "system_prompt" in changes:
        value = changes["system_prompt"]
        if not isinstance(value, str):
            errors["system_prompt"] = "Extra instructions: enter text"
        elif len(value) > MAX_SYSTEM_PROMPT:
            errors["system_prompt"] = f"Extra instructions: keep this under {MAX_SYSTEM_PROMPT} characters"
        else:
            settings["system_prompt"] = value.strip()

    if "context_files" in changes:
        value = changes["context_files"]
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            errors["context_files"] = "Attached files: enter a list of file paths"
        elif len(value) > MAX_FILES:
            errors["context_files"] = f"Attached files: attach at most {MAX_FILES} files"
        else:
            try:
                # The same boundary the file tools enforce, checked here so a
                # path that could never be read is refused at the settings.
                settings["context_files"] = [str(local_path(p)) for p in value]
            except ValueError as exc:
                errors["context_files"] = f"Attached files: {exc}"

    # Cloud reasoning is gated on the billing confirmation, but a missing
    # confirmation must never fail the save and take an API key with it.
    if settings["cloud_enabled"] and not settings["free_account_confirmed"]:
        settings["cloud_enabled"] = False

    return settings, errors


def save_settings(changes: dict) -> tuple[dict, dict]:
    """Persist `changes`, or nothing at all if any field failed validation."""
    settings, errors = validate_settings(changes)
    if errors:
        return load_settings(), errors
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.write_json(str(SETTINGS_PATH), settings)
    SETTINGS_PATH.chmod(0o600)
    return settings, {}


def _secret():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    schema = Secret.Schema.new("org.jrf.DesktopForge.Clive", Secret.SchemaFlags.NONE,
                               {"provider": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def get_key() -> str:
    # Do not copy environment credentials into configuration or state snapshots.
    if os.environ.get("OLLAMA_API_KEY"):
        return os.environ["OLLAMA_API_KEY"]
    try:
        Secret, schema = _secret()
        return Secret.password_lookup_sync(schema, {"provider": "ollama"}, None) or ""
    except Exception:
        # libsecret reports a locked or absent keyring as a GLib.Error, which
        # the service would otherwise flatten into "could not complete this
        # request". Say what the user can actually do, without the detail.
        raise RuntimeError(KEYRING_UNAVAILABLE) from None


def set_key(value: str) -> None:
    try:
        Secret, schema = _secret()
        if value:
            stored = Secret.password_store_sync(schema, {"provider": "ollama"},
                Secret.COLLECTION_DEFAULT, "CLIVE — Ollama", value.strip(), None)
        else:
            stored = Secret.password_clear_sync(schema, {"provider": "ollama"}, None)
    except Exception:
        raise RuntimeError(KEYRING_UNAVAILABLE) from None
    if value and not stored:
        raise RuntimeError("GNOME Keyring could not save the API key")


def key_state() -> dict:
    """Whether a key is stored, for status display. Never raises."""
    try:
        return {"has_key": bool(get_key()), "keyring_error": ""}
    except RuntimeError as exc:
        return {"has_key": False, "keyring_error": str(exc)}
