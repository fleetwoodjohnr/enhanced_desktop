"""Private settings and credentials, separate from the widget configuration."""
from __future__ import annotations

import os
import threading
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
    # Models offered by the quick switcher, per endpoint. The active model is
    # always one of them; load_settings adds it if a hand edit left it out.
    "cloud_models": [],
    "local_models": [],
    # Use the local model for the rest of a task when the cloud one fails.
    "fallback_to_local": True,
    # Appearance.
    "card_density": "comfortable",
    "show_action_details": True,
    # Notifications, sent by the desktop extension while the card is hidden.
    "notify_finished": True,
    "notify_waiting": True,
    # Privacy: what may be sent to a cloud model. Local models see everything.
    "cloud_images": True,
    "cloud_attachments": True,
    # Memory and context.
    "context_turns": 12,
    "history_days": 0,
    # Advanced.
    "debug_logging": False,
    # Integrations. An empty notes folder means ~/Notes.
    "notes_folder": "",
    # Automation: how often to look for emails that may need a reply (0 is
    # off), and how far back.
    "followup_hours": 0,
    "followup_days": 7,
}
FOLLOWUP_HOURS = (0, 1, 3, 6, 24)
BOOLEAN_KEYS = ("cloud_enabled", "free_account_confirmed", "fallback_to_local",
                "show_action_details", "notify_finished", "notify_waiting",
                "cloud_images", "cloud_attachments", "debug_logging")
DENSITIES = ("comfortable", "compact")
MAX_SAVED_MODELS = 20
ENDPOINTS = ("cloud", "local")
# Inclusive bounds for the numeric settings. The dialog's spin rows are a
# convenience; these are the boundary that actually protects the agent.
RANGES = {"local_context": (2048, 131072), "max_turns": (4, 64), "context_turns": (2, 40),
          "history_days": (0, 3650), "followup_days": (1, 30)}
# Long enough for real standing instructions, short enough that it cannot crowd
# out the conversation in an 8k local context.
MAX_SYSTEM_PROMPT = 4000
KEYRING_UNAVAILABLE = ("GNOME Keyring is locked or unavailable. Unlock your login "
                       "keyring, then try again.")


_cache: tuple[tuple[str, int, int], dict] | None = None
# Every change is load -> modify -> save, and the service makes changes from
# several request threads. Without one lock around the whole sequence, two
# quick switches (or a download finishing during one) drop each other's edit.
_write_lock = threading.RLock()


def _raw() -> dict:
    """The stored file, re-read only when it changed on disk.

    The service publishes the selected model with every state update, which
    during streaming is several times a second.
    """
    global _cache
    try:
        status = SETTINGS_PATH.stat()
    except OSError:
        return {}
    # The path is part of the key: tests, and a changed XDG_CONFIG_HOME, point
    # SETTINGS_PATH elsewhere, and two files can share a timestamp.
    key = (str(SETTINGS_PATH), status.st_mtime_ns, status.st_size)
    if _cache is None or _cache[0] != key:
        _cache = (key, config.read_json(str(SETTINGS_PATH)) or {})
    return _cache[1]


def _model_list(value, current: str) -> list[str]:
    names = [v for v in value if isinstance(v, str) and valid_model_name(v)] \
        if isinstance(value, list) else []
    names = list(dict.fromkeys(names))[:MAX_SAVED_MODELS]
    if current not in names:
        names.insert(0, current)
    return names


def load_settings() -> dict:
    raw = _raw()
    settings = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    # Hold the billing invariant on read, so every reader -- the transport, the
    # model test, the UI -- agrees. A hand-edited file cannot turn the cloud on
    # behind the confirmation.
    if not settings["free_account_confirmed"]:
        settings["cloud_enabled"] = False
    # context_files defaults to a list. Without the copy every caller would share
    # -- and could mutate -- the one list living inside DEFAULTS.
    settings["context_files"] = [p for p in settings["context_files"] if isinstance(p, str)]
    settings["cloud_models"] = _model_list(settings["cloud_models"], settings["cloud_model"])
    settings["local_models"] = _model_list(settings["local_models"], settings["local_model"])
    # A hand-edited value of the wrong type falls back to the default rather
    # than reaching code that trusts its type.
    for key in BOOLEAN_KEYS:
        if not isinstance(settings[key], bool):
            settings[key] = DEFAULTS[key]
    for key, (low, high) in RANGES.items():
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            settings[key] = DEFAULTS[key]
    if settings["card_density"] not in DENSITIES:
        settings["card_density"] = DEFAULTS["card_density"]
    if not isinstance(settings["notes_folder"], str):
        settings["notes_folder"] = ""
    if settings["followup_hours"] not in FOLLOWUP_HOURS:
        settings["followup_hours"] = 0
    return settings


def valid_model_name(value: str, local: bool = False) -> bool:
    if not value or len(value) > 150 or any(c.isspace() for c in value):
        return False
    return not (local and ("cloud" in value or "/" in value))


def validate_settings(changes: dict) -> tuple[dict, dict]:
    """Merge `changes` into the stored settings without writing anything.

    Every field is checked, so one bad value reports itself instead of hiding
    behind the first failure, and the caller can decide what to persist.
    Returns the merged settings and a per-field error map.
    """
    settings = load_settings()
    errors: dict[str, str] = {}

    for key, label in (("cloud_model", "Cloud model"), ("local_model", "Local model")):
        if key not in changes:
            continue
        value = str(changes[key]).strip()
        if not valid_model_name(value):
            errors[key] = f"{label}: enter a valid Ollama model name"
        elif key == "local_model" and not valid_model_name(value, local=True):
            errors[key] = "Local model: this must be a model that runs on your machine"
        else:
            settings[key] = value

    for key, local in (("cloud_models", False), ("local_models", True)):
        if key not in changes:
            continue
        value = changes[key]
        if (not isinstance(value, list) or len(value) > MAX_SAVED_MODELS or
                not all(isinstance(v, str) and valid_model_name(v.strip(), local) for v in value)):
            errors[key] = f"Saved models: enter at most {MAX_SAVED_MODELS} valid model names"
        else:
            settings[key] = list(dict.fromkeys(v.strip() for v in value))

    if "fallback_to_local" in changes:
        if not isinstance(changes["fallback_to_local"], bool):
            errors["fallback_to_local"] = "This setting must be on or off"
        else:
            settings["fallback_to_local"] = changes["fallback_to_local"]

    for key in BOOLEAN_KEYS:
        if key not in changes or key == "fallback_to_local":
            continue
        if not isinstance(changes[key], bool):
            errors[key] = "This setting must be on or off"
        else:
            settings[key] = changes[key]

    if "card_density" in changes:
        if changes["card_density"] not in DENSITIES:
            errors["card_density"] = "Card density: choose comfortable or compact"
        else:
            settings["card_density"] = changes["card_density"]

    if "followup_hours" in changes:
        if changes["followup_hours"] not in FOLLOWUP_HOURS or isinstance(changes["followup_hours"], bool):
            errors["followup_hours"] = "Follow-up check: choose one of the listed intervals"
        else:
            settings["followup_hours"] = changes["followup_hours"]

    for key, label in (("local_context", "Local context window"), ("max_turns", "Maximum task steps"),
                       ("context_turns", "Conversation memory"), ("history_days", "Keep history"),
                       ("followup_days", "Follow-up look-back")):
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

    if "notes_folder" in changes:
        value = changes["notes_folder"]
        if not isinstance(value, str):
            errors["notes_folder"] = "Notes folder: choose a folder"
        elif not value.strip():
            settings["notes_folder"] = ""
        else:
            try:
                settings["notes_folder"] = str(local_path(value.strip()))
            except ValueError as exc:
                errors["notes_folder"] = f"Notes folder: {exc}"

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
    # The active model is always offered by the switcher.
    settings["cloud_models"] = _model_list(settings["cloud_models"], settings["cloud_model"])
    settings["local_models"] = _model_list(settings["local_models"], settings["local_model"])

    return settings, errors


def save_settings(changes: dict) -> tuple[dict, dict]:
    """Persist `changes`, or nothing at all if any field failed validation."""
    with _write_lock:
        return _save_settings(changes)


def _save_settings(changes: dict) -> tuple[dict, dict]:
    settings, errors = validate_settings(changes)
    if errors:
        return load_settings(), errors
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.write_json(str(SETTINGS_PATH), settings)
    SETTINGS_PATH.chmod(0o600)
    # Timestamps are coarse; a second save within the same tick and of the
    # same size would otherwise read back the first one from the cache.
    global _cache
    status = SETTINGS_PATH.stat()
    _cache = ((str(SETTINGS_PATH), status.st_mtime_ns, status.st_size), dict(settings))
    return settings, {}


def select_model(endpoint: str, model: str | None = None, *, has_key: bool) -> dict:
    """Switch between the cloud and local model, optionally naming which one.

    Raises ValueError, in words the user can act on, when the cloud cannot be
    used yet. Allowed mid-task: the transport reads the selection before every
    request, so the switch applies from the next one.
    """
    if endpoint not in ENDPOINTS:
        raise ValueError("Choose the cloud or the local model")
    with _write_lock:
        return _select_model(endpoint, model, has_key)


def _select_model(endpoint: str, model: str | None, has_key: bool) -> dict:
    current = load_settings()
    changes: dict = {"cloud_enabled": endpoint == "cloud"}
    if endpoint == "cloud":
        if not current["free_account_confirmed"]:
            raise ValueError("Turn on Ollama Cloud in CLIVE settings first; it asks you to "
                             "confirm the billing terms once.")
        if not has_key:
            raise ValueError("Add your Ollama API key in CLIVE settings first.")
    if model is not None:
        key = f"{endpoint}_model"
        changes[key] = model.strip()
        saved = current[f"{endpoint}_models"]
        if changes[key] not in saved:
            changes[f"{endpoint}_models"] = [*saved, changes[key]][-MAX_SAVED_MODELS:]
    settings, errors = save_settings(changes)
    if errors:
        raise ValueError(next(iter(errors.values())))
    return settings


def edit_saved_models(endpoint: str, model: str, add: bool) -> dict:
    if endpoint not in ENDPOINTS:
        raise ValueError("Choose the cloud or the local model")
    with _write_lock:
        return _edit_saved_models(endpoint, model, add)


def _edit_saved_models(endpoint: str, model: str, add: bool) -> dict:
    current = load_settings()
    key = f"{endpoint}_models"
    model = model.strip()
    names = list(current[key])
    if add:
        if model not in names:
            names.append(model)
    else:
        if model == current[f"{endpoint}_model"]:
            raise ValueError("Switch to another model before removing this one")
        names = [name for name in names if name != model]
    settings, errors = save_settings({key: names})
    if errors:
        raise ValueError(next(iter(errors.values())))
    return settings


def selection(settings: dict, has_key: bool | None) -> dict:
    """What the model switchers show: both endpoints and which one is active."""
    return {
        "endpoint": "cloud" if settings["cloud_enabled"] else "local",
        "cloud_model": settings["cloud_model"],
        "local_model": settings["local_model"],
        "cloud_models": settings["cloud_models"],
        "local_models": settings["local_models"],
        # has_key is None while the service is still asking the keyring; the
        # cloud is then offered rather than greyed out, and a missing key is
        # reported by the request that needed it.
        "cloud_ready": bool(settings["free_account_confirmed"]) and has_key is not False,
        "fallback_to_local": settings["fallback_to_local"],
    }


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
