"""Session D-Bus service for CLIVE; it never owns GNOME Shell actors."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from .. import config
from .agent import Agent
from .integrations import ACCESS_PATH
from .models import safe_message
from .settings import (BUS_NAME, DATA_PATH, INTERFACE, OBJECT_PATH, edit_saved_models,
                       get_key, key_state, load_settings, save_settings, select_model,
                       selection, set_key, valid_model_name)

XML = f"""<node><interface name="{INTERFACE}">
  <method name="Call"><arg name="request" type="s" direction="in"/>
    <arg name="response" type="s" direction="out"/></method>
  <signal name="Changed"><arg name="state" type="s"/></signal>
</interface></node>"""


class Service:
    def __init__(self):
        os.umask(0o077)
        # CLIVE keeps its traces local even if the desktop environment enabled LangSmith elsewhere.
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        self.loop = GLib.MainLoop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.agent = Agent(DATA_PATH, self.publish, access_path=ACCESS_PATH,
                           key_state=lambda: self.has_key)
        self.pending = None
        self.pending_lock = threading.Lock()
        self.setup_lock = threading.Lock()
        self.pull_lock = threading.Lock()
        self.source = 0
        # Checked once and after every key change, not per published state:
        # asking the keyring is a D-Bus round trip, and a locked one may
        # prompt. None means "not known yet".
        self.has_key = None
        self.pull = None
        self.followups = None
        self.followup_running = False
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = self.bus.register_object(OBJECT_PATH, info.interfaces[0], self._call, None, None)
        self.owner = Gio.bus_own_name_on_connection(self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, self._lost)
        self.publish(self.agent.snapshot())
        # Off the main loop, after the name is owned: D-Bus activation at login
        # must not wait on a keyring that is still locked.
        threading.Thread(target=self._learn_key_state, daemon=True, name="clive-keyring").start()

    def _learn_key_state(self):
        self.has_key = key_state()["has_key"]
        self._prune()
        self.republish()
        GLib.timeout_add_seconds(PRUNE_SECONDS, self._prune_tick)
        GLib.timeout_add_seconds(FOLLOWUP_TICK_SECONDS, self._followup_tick)

    def _prune(self):
        """Apply Memory's history retention; a no-op while it keeps everything."""
        try:
            if self.agent.prune(load_settings()["history_days"]):
                self.republish()
        except Exception:  # noqa: BLE001 - retention must never take the service down
            pass

    def _followup_tick(self):
        settings = load_settings()
        hours = settings["followup_hours"]
        due = hours and time.time() - (self.followups or {}).get("checked", 0) >= hours * 3600
        if due and not self.followup_running:
            self.followup_running = True
            threading.Thread(target=self._check_followups, args=(settings["followup_days"],),
                             daemon=True, name="clive-followups").start()
        return GLib.SOURCE_CONTINUE

    def _check_followups(self, days):
        """Look for emails that may need a reply, through App Access like any tool call.

        Only headers are read, and only from accounts whose follow-up tracking
        is switched on; nothing is sent to a model. The result is a count the
        desktop turns into a notification.
        """
        from .integrations import AccessDisabled
        from .tools import TOOLS
        registry = self.agent.registry
        found = []
        try:
            for integration in registry.integrations():
                if integration.instance_of != "mail":
                    continue
                arguments = {"account": integration.id, "days": days}
                try:
                    registry.check("mail_followups", arguments)
                except AccessDisabled:
                    continue
                try:
                    result = TOOLS["mail_followups"].handler(None, arguments)
                except Exception:  # noqa: BLE001 - an unreachable server is tried next time
                    continue
                self.agent.history.record_usage(integration.id, "Checking for follow-ups")
                found.append({"account": integration.id, "name": integration.name,
                              "count": len(result.get("waiting_on_you", []))})
            self.followups = {"checked": time.time(), "accounts": found,
                              "count": sum(a["count"] for a in found)}
            self.republish()
        finally:
            self.followup_running = False

    def _prune_tick(self):
        threading.Thread(target=self._prune, daemon=True, name="clive-prune").start()
        return GLib.SOURCE_CONTINUE

    def _lost(self, *_args):
        self.loop.quit()

    def publish(self, state):
        with self.pending_lock:
            self.pending = state
            if not self.source:
                self.source = GLib.timeout_add(100, self._flush)

    def _flush(self):
        with self.pending_lock:
            state, self.pending, self.source = self.pending, None, 0
        if state is not None:
            self.bus.emit_signal(None, OBJECT_PATH, INTERFACE, "Changed",
                                 GLib.Variant("(s)", (json.dumps(self.decorate(state)),)))
        return GLib.SOURCE_REMOVE

    def decorate(self, state):
        """The public state: the task, trimmed, plus the model and App Access summary."""
        try:
            chosen = selection(load_settings(), self.has_key)
        except Exception:  # noqa: BLE001 - a broken settings file must not stop updates
            chosen = None
        try:
            access = self.agent.registry.summary()
        except Exception:  # noqa: BLE001
            access = None
        try:
            stored = load_settings()
            preferences = {key: stored[key] for key in PUBLIC_PREFERENCES}
        except Exception:  # noqa: BLE001
            preferences = None
        try:
            draft = self.agent.draft_public()
        except Exception:  # noqa: BLE001
            draft = []
        return {**public_state(state), "selection": chosen, "pull": self.pull, "access": access,
                "preferences": preferences, "draft": draft, "followups": self.followups}

    def republish(self):
        self.publish(self.agent.snapshot())

    def _call(self, _connection, _sender, _path, _interface, _method, params, invocation):
        raw = params.unpack()[0]
        if len(raw) > 100000:
            invocation.return_dbus_error(INTERFACE + ".InvalidRequest", "Request is too large")
            return

        def work():
            try:
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise ValueError("Expected a request object")
                result = self.dispatch(request)
                response = {"ok": True, "result": result}
            except Exception as exc:
                # Avoid putting keyring/HTTP errors or configuration contents on the bus.
                message = safe_message(exc, "CLIVE could not complete this request")
                response = {"ok": False, "error": message}
            GLib.idle_add(lambda: invocation.return_value(GLib.Variant("(s)", (json.dumps(response),))))

        threading.Thread(target=work, daemon=True, name="clive-request").start()

    def dispatch(self, r):
        # A setup probe must not compete with an active task or a second probe.
        # A message only has to wait for a probe; two messages sent close
        # together are the agent's business ("finish the current task first").
        if r.get("op") == "submit" and self.setup_lock.locked():
            raise ValueError("A model test is running. Wait for it to finish, then send your message.")
        if r.get("op") in ("configure", "validate", "check_key"):
            if not self.setup_lock.acquire(blocking=False):
                raise ValueError("Model setup is in progress. Wait for the test to finish.")
            try:
                return self._dispatch(r)
            finally:
                self.setup_lock.release()
        return self._dispatch(r)

    def _dispatch(self, r):
        op = r.get("op")
        if op == "state":
            return self.decorate(self.agent.snapshot())
        if op in MODEL_OPS:
            result = self._models(op, r)
            self.republish()
            return result
        if op in ACCESS_OPS:
            result = self._access(op, r)
            self.republish()
            return result
        if op == "submit":
            # Only paths cross the bus; the service reads the files itself, so a
            # large attachment never has to fit inside a D-Bus request.
            return self.agent.submit(r.get("message", ""), r.get("conversation", ""),
                                     r.get("attachments", []), bool(r.get("use_draft")))
        if op == "draft_add":
            return self.agent.draft_add(r.get("paths"))
        if op == "draft_remove":
            return self.agent.draft_remove(r.get("id", ""))
        if op == "draft_clear":
            return self.agent.draft_clear()
        if op == "approve":
            self.agent.approve(r.get("id"), r.get("version"))
        elif op == "confirm":
            self.agent.confirm(r.get("id"), r.get("approved"))
        elif op == "cancel":
            self.agent.cancel()
        elif op == "history":
            return self.agent.history.list_conversations()
        elif op == "view":
            self.agent.view(r.get("conversation", ""))
        elif op == "delete":
            self.agent.delete(r.get("conversation", ""))
        elif op == "settings":
            return {**load_settings(), **key_state()}
        elif op == "configure":
            errors = {}
            # The credential is stored first and on its own. A rejected model
            # name must never discard a key the user just pasted, and a locked
            # keyring must never discard the model settings. The key is also
            # allowed mid-task -- it is only read per HTTP request -- while a
            # model or limit change is not.
            if "api_key" in r:
                if not isinstance(r["api_key"], str) or len(r["api_key"]) > 2000:
                    raise ValueError("Invalid API key")
                try:
                    set_key(r["api_key"])
                except RuntimeError as exc:
                    errors["api_key"] = str(exc)
            if r.get("settings"):
                if self.agent.busy():
                    errors["settings"] = "Finish or cancel the current task before changing models"
                    settings = load_settings()
                else:
                    settings, field_errors = save_settings(r["settings"])
                    errors.update(field_errors)
            else:
                settings = load_settings()
            keys = key_state()
            self.has_key = keys["has_key"]
            self.republish()
            return {**settings, **keys, "errors": errors}
        elif op == "validate":
            if self.agent.busy():
                raise ValueError("Finish or cancel the current task before testing models")
            return validate_models()
        elif op == "check_key":
            result = check_key()
            self.has_key = key_state()["has_key"]
            self.republish()
            return result
        else:
            raise ValueError("Unknown CLIVE operation")
        return self.decorate(self.agent.snapshot())

    def _access(self, op, r):
        registry = self.agent.registry
        if op == "access":
            return registry.describe(self.agent.history.usage())
        if op == "access_set":
            if not isinstance(r.get("id"), str):
                raise ValueError("Choose an app")
            registry.set(r["id"], r.get("enabled"), r.get("capabilities"), r.get("confirm"))
            return registry.describe(self.agent.history.usage())
        if op == "access_bulk":
            ids = r.get("ids")
            if ids is not None and not isinstance(ids, list):
                raise ValueError("Choose the apps to change")
            registry.set_many(r.get("enabled"), ids)
            return registry.describe(self.agent.history.usage())
        if op == "access_pause":
            paused = r.get("paused")
            registry.pause(paused)
            # Pausing is the emergency stop: nothing already under way runs on.
            if paused is True and self.agent.busy():
                self.agent.cancel()
            return registry.summary()
        if op == "action_detail":
            detail = self.agent.history.call_result(r.get("call") or "")
            if detail is None:
                raise ValueError("That action is no longer in the history")
            return detail
        if op == "integration_option":
            if not isinstance(r.get("id"), str) or not isinstance(r.get("key"), str):
                raise ValueError("Choose an app setting")
            registry.set_option(r["id"], r["key"], r.get("value"))
            return registry.describe(self.agent.history.usage())
        if op == "goa_accounts":
            from .integrations import mail
            connected = {a.get("goa_id") for a in mail.load_accounts()}
            return {"accounts": [a for a in mail.goa_mail_accounts() if a["goa_id"] not in connected]}
        if op == "mail_account_add":
            from .integrations import mail
            request = r.get("account")
            if not isinstance(request, dict):
                raise ValueError("Choose an account to connect")
            account = mail.add_account(request)
            # Connecting an account is the user's explicit choice to use it.
            registry.set(mail.PREFIX + account["id"], enabled=True)
            return registry.describe(self.agent.history.usage())
        if op == "mail_account_remove":
            from .integrations import mail
            if not isinstance(r.get("id"), str) or not r["id"].startswith(mail.PREFIX):
                raise ValueError("Choose an email account")
            mail.remove_account(r["id"].removeprefix(mail.PREFIX))
            return registry.describe(self.agent.history.usage())
        if op == "access_check":
            return check_integration(registry, r.get("id"))
        if op == "usage_clear":
            self.agent.history.clear_usage()
            return registry.describe({})
        raise ValueError("Unknown CLIVE operation")

    def _models(self, op, r):
        endpoint = r.get("endpoint")
        model = r.get("model")
        if model is not None and not isinstance(model, str):
            raise ValueError("Enter a model name")
        if op == "models":
            return selection(load_settings(), self.has_key)
        if op == "model_select":
            return selection(select_model(endpoint, model, has_key=self.has_key is not False),
                             self.has_key)
        if op in ("model_add", "model_remove"):
            if not model or not valid_model_name(model.strip(), local=endpoint == "local"):
                raise ValueError("Enter a valid Ollama model name")
            return selection(edit_saved_models(endpoint, model, op == "model_add"), self.has_key)
        if op == "models_available":
            return {"endpoint": endpoint, "models": available_models(endpoint)}
        if op == "model_pull":
            if not model or not valid_model_name(model.strip(), local=True):
                raise ValueError("Enter the name of a model to download, such as qwen3.5:4b")
            self._start_pull(model.strip())
            return {"started": model.strip()}
        raise ValueError("Unknown CLIVE operation")

    def _start_pull(self, model):
        if not self.pull_lock.acquire(blocking=False):
            raise ValueError("A model download is already running")

        def work():
            try:
                for progress in pull_model(model):
                    self.pull = progress
                    self.republish()
                self.pull = {"model": model, "status": "done"}
                edit_saved_models("local", model, True)
            except Exception as exc:  # noqa: BLE001 - reported to the user, never raised
                self.pull = {"model": model, "status": "failed",
                             "error": safe_message(exc, "The download did not finish.")}
            finally:
                self.pull_lock.release()
                self.republish()

        threading.Thread(target=work, daemon=True, name="clive-pull").start()

    def stop(self):
        self.agent.close()
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def run(self):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, self.stop)
        self.loop.run()


# Settings the card and the extension act on themselves.
PUBLIC_PREFERENCES = ("card_density", "show_action_details", "notify_finished", "notify_waiting",
                      "followup_hours")
PRUNE_SECONDS = 6 * 3600
FOLLOWUP_TICK_SECONDS = 15 * 60
ACCESS_OPS = ("access", "access_set", "access_bulk", "access_pause", "action_detail", "usage_clear",
              "integration_option", "goa_accounts", "mail_account_add", "mail_account_remove",
              "access_check")
# What a published action result may carry before it is cut to a preview. The
# full result stays in CLIVE's own journal (action_detail); Shell does not need
# an email body streamed into it with every state update.
PUBLIC_RESULT_LIMIT = 2000


def _trim(value):
    if isinstance(value, str):
        return value if len(value) <= PUBLIC_RESULT_LIMIT else value[:PUBLIC_RESULT_LIMIT] + "…"
    if isinstance(value, dict):
        return {key: _trim(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_trim(item) for item in value[:50]]
    return value


def public_state(state: dict) -> dict:
    actions = state.get("actions")
    confirmation = state.get("confirmation")
    if not actions and not confirmation:
        return state
    trimmed = []
    for action in actions or []:
        text = json.dumps(action.get("result"), ensure_ascii=False)
        if len(text) > PUBLIC_RESULT_LIMIT:
            action = {**action, "result": None, "preview": text[:PUBLIC_RESULT_LIMIT], "truncated": True}
        trimmed.append(action)
    return {**state, "actions": trimmed, "confirmation": _trim(confirmation) if confirmation else confirmation}


MODEL_OPS = ("models", "model_select", "model_add", "model_remove",
             "models_available", "model_pull")
LOCAL_BASE = "http://127.0.0.1:11434"
CLOUD_BASE = "https://ollama.com"


def check_integration(registry, integration_id) -> dict:
    """A live check, for the App Access Check button: can CLIVE reach it now?"""
    integration = registry.get(integration_id) if isinstance(integration_id, str) else None
    if integration is None:
        raise ValueError("Choose an app")
    if integration.instance_of == "mail":
        from .integrations import mail
        try:
            with mail.Mailbox(mail.account_for(integration.id)) as box:
                folders = len(box.roles)
            return {"state": "ready", "detail": f"Connected; {folders} special folders found"}
        except Exception as exc:  # noqa: BLE001 - the user needs the reason, not a traceback
            return {"state": "unavailable",
                    "detail": safe_message(exc, "Could not sign in to the mail server. Check the "
                                                "account and your internet connection.")}
    try:
        return integration.status()
    except Exception:  # noqa: BLE001
        return {"state": "unavailable", "detail": "Could not check this app"}


def downloaded_models() -> list[str]:
    """Model names pulled into this user's Ollama store, read from disk.

    Only used to explain an empty answer from the server: a different Ollama
    (the system-wide ollama.service, say) can hold the port while serving a
    store of its own.
    """
    root = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models") / "manifests"
    names = []
    try:
        for manifest in root.glob("*/*/*/*"):
            host, namespace, name, tag = manifest.relative_to(root).parts
            if not manifest.is_file():
                continue
            if host == "registry.ollama.ai":
                names.append(f"{name}:{tag}" if namespace == "library" else f"{namespace}/{name}:{tag}")
            else:
                names.append(f"{host}/{namespace}/{name}:{tag}")
    except OSError:
        return []
    return sorted(names)


def available_models(endpoint) -> list[str]:
    """Names Ollama can serve right now: installed locally, or in the cloud catalog."""
    import httpx
    if endpoint not in ("cloud", "local"):
        raise ValueError("Choose the cloud or the local model")
    headers = {}
    if endpoint == "cloud":
        # The catalog is public; a key is sent when there is one, and a locked
        # keyring only means asking without it.
        try:
            key = get_key()
        except RuntimeError:
            key = ""
        if key:
            headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False) as client:
            response = client.get((LOCAL_BASE if endpoint == "local" else CLOUD_BASE) + "/api/tags",
                                  headers=headers)
    except Exception:
        # httpx errors carry the URL and headers; say what the user can do.
        raise ValueError("The local Ollama service is not running." if endpoint == "local"
                         else "Could not reach Ollama Cloud.") from None
    if response.status_code != 200:
        raise ValueError(f"Ollama returned HTTP {response.status_code} for the model list.")
    try:
        names = [item.get("name") or item.get("model") for item in response.json().get("models", [])]
    except (ValueError, AttributeError):
        raise ValueError("Ollama returned an unreadable model list.") from None
    local = endpoint == "local"
    names = {name for name in names if isinstance(name, str)}
    if local:
        # Compared before filtering: a store holding only "-cloud" models is
        # served correctly, even though none of them is offered below.
        downloaded = downloaded_models()
        if downloaded and not set(downloaded) & names:
            shown = ", ".join(downloaded[:4]) + (", …" if len(downloaded) > 4 else "")
            raise ValueError(
                f"Ollama reports none of the {len(downloaded)} models downloaded in your model "
                f"folder ({shown}). Another Ollama server, such as the system-wide "
                "ollama.service, is probably using port 11434. Run "
                "`sudo systemctl disable --now ollama.service`, then "
                "`systemctl --user restart desktop-forge-ollama`.")
    # A local "-cloud" model is a proxy to Ollama's servers; it is never offered
    # as the local model, which must keep everything on this machine.
    return sorted(name for name in names if valid_model_name(name, local))


def pull_model(model):
    """Download a local model, yielding progress the UI can show."""
    import httpx
    with httpx.Client(timeout=httpx.Timeout(3600, connect=10)) as client:
        with client.stream("POST", LOCAL_BASE + "/api/pull",
                           json={"model": model, "stream": True}) as response:
            if response.status_code != 200:
                raise ValueError(f"Ollama could not download {model} (HTTP {response.status_code}).")
            last = 0.0
            for line in response.iter_lines():
                if not line:
                    continue
                item = json.loads(line)
                if item.get("error"):
                    raise ValueError(f"Ollama could not download {model}.")
                now = time.monotonic()
                # One update a second is plenty for a progress bar.
                if now - last >= 1 or item.get("status") == "success":
                    last = now
                    yield {"model": model, "status": item.get("status", ""),
                           "completed": item.get("completed"), "total": item.get("total")}


def validate_models():
    import httpx
    settings = load_settings()
    state = key_state()
    result = []
    for local in (False, True):
        label = "Local" if local else "Cloud"
        model = settings["local_model" if local else "cloud_model"]
        if not local and not settings["cloud_enabled"]:
            result.append("Cloud: turned off. Enable Ollama Cloud in settings to test it.")
            continue
        if not local and state["keyring_error"]:
            result.append(f"Cloud: {state['keyring_error']}")
            continue
        key = "" if local else get_key()
        if not local and not key:
            result.append("Cloud: add your Ollama API key first.")
            continue
        base = "http://127.0.0.1:11434" if local else "https://ollama.com"
        try:
            # /api/show is not part of the documented cloud surface. An answer
            # that is missing or not JSON means "capabilities unknown", and the
            # probe below decides -- refusing a model that works is worse.
            capabilities = None
            with httpx.Client(timeout=180) as client:
                response = client.post(base + "/api/show", json={"model": model},
                                       headers={"Authorization": f"Bearer {key}"} if key else {})
                if response.status_code == 200:
                    try:
                        capabilities = response.json().get("capabilities", [])
                    except ValueError:
                        capabilities = None
                elif local:
                    result.append(f"{label}: {model} unavailable (HTTP {response.status_code}).")
                    continue
            if capabilities is not None and "tools" not in capabilities:
                result.append(f"{label}: {model} does not report tool calling, which CLIVE requires; choose another model.")
                continue
            # Vision is advisory: most cloud models report tools without it, and
            # only the screenshot tools need it.
            vision = capabilities is None or "vision" in capabilities
            from .models import Models
            candidate = Models({**settings, "cloud_enabled": not local, "free_account_confirmed": not local}, threading.Event())
            # Direct call: setup must report the cloud result, not a successful local fallback.
            started = time.monotonic()
            probe_model(candidate, vision)
            checks = ("image recognition, " if vision else "") + "tool calling, structured output, and streaming"
            result.append(f"{label}: {model} passed {checks}. Test took {time.monotonic() - started:.1f}s.")
            if capabilities is None:
                result.append(f"{label}: Ollama did not report what {model} supports, so the test exercised it directly.")
            elif not vision:
                result.append(f"{label}: {model} does not support images, so desktop screenshots will not be sent to it.")
        except Exception as exc:
            result.append(f"{label}: " + safe_message(exc, "could not connect or complete the model test."))
    if state["has_key"] and not settings["cloud_enabled"]:
        result.append("An API key is saved, so live web search and page fetching work "
                      "even with cloud reasoning turned off.")
    return {"message": "\n".join(result)}


def check_key() -> dict:
    """Confirm the saved key in seconds, instead of only through a full model test.

    A single one-token streaming request: the status code answers the question
    before any generation happens, and the stream is closed without reading it.
    """
    import httpx
    settings = load_settings()
    model = settings["cloud_model"]
    state = key_state()
    if state["keyring_error"]:
        return {"ok": False, "message": state["keyring_error"]}
    key = get_key()
    if not key:
        return {"ok": False, "message": "No API key is saved yet. Paste one above and save it."}
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}],
            "stream": True, "think": False, "options": {"num_predict": 1}}
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
            with client.stream("POST", "https://ollama.com/api/chat", json=body,
                               headers={"Authorization": f"Bearer {key}"}) as response:
                status = response.status_code
    except Exception:
        # Never surface httpx exceptions: they carry the URL and headers.
        return {"ok": False, "message": "Could not reach Ollama Cloud. Check your internet connection."}
    if status == 200:
        return {"ok": True, "message": f"API key accepted. {model} is available on your account."}
    labels = {
        401: "Ollama rejected this API key. Create a new one at ollama.com/settings/keys.",
        403: f"The key works, but your account cannot use {model}.",
        404: f"The key works, but {model} was not found. Choose another cloud model.",
        402: "The key works, but the free allowance is used up for this month.",
        429: "The key works, but the cloud usage limit was reached. Try again later.",
    }
    return {"ok": status in (402, 403, 404, 429),
            "message": labels.get(status, f"Ollama Cloud returned HTTP {status}.")}


def probe_model(candidate, vision=True):
    """Exercise real model capabilities with synthetic data, without desktop access."""
    from .tools import spec
    arguments = {"color": {"type": "string", "enum": ["red", "green", "blue"]}}
    if vision:
        import base64
        import struct
        import zlib
        # A blue square constructed as a PNG test fixture, never a screen capture.
        def chunk(kind, data):
            return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 96, 96, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress((b"\0" + b"\x00\x00\xff" * 96) * 96)) + chunk(b"IEND", b"")
        probe = spec("clive_probe", "Report the dominant color in the attached image.", arguments)
        messages = [{"role": "user", "content": "Call clive_probe with the dominant color of this image.",
                     "images": [base64.b64encode(png).decode("ascii")]}]
    else:
        probe = spec("clive_probe", "Report the requested color.", arguments)
        messages = [{"role": "user", "content": "Call clive_probe with the color blue."}]
    answer = candidate._chat(messages, [probe], None)
    calls = answer.get("tool_calls", [])
    if len(calls) != 1 or calls[0].get("function", {}).get("name") != "clive_probe" or calls[0]["function"].get("arguments") != {"color": "blue"}:
        raise ValueError("Model did not complete the image and tool probe")
    messages += [answer, {"role": "tool", "tool_name": "clive_probe", "content": '{"verified":true}'},
                 {"role": "user", "content": "Return ready: true as JSON."}]
    output = candidate._chat(messages, None, {"type": "object", "properties": {"ready": {"type": "boolean"}},
                                             "required": ["ready"], "additionalProperties": False})
    if json.loads(output["content"]) != {"ready": True}:
        raise ValueError("Model did not complete the structured output probe")


def main():
    try:
        Service().run()
    except ImportError:
        print("CLIVE dependencies are missing. Run ./install.sh --clive", flush=True)
        return 1
    return 0
