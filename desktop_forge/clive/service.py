"""Session D-Bus service for CLIVE; it never owns GNOME Shell actors."""
from __future__ import annotations

import json
import os
import threading
import time

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from .. import config
from .agent import Agent
from .models import safe_message
from .settings import (BUS_NAME, DATA_PATH, INTERFACE, OBJECT_PATH, get_key,
                       key_state, load_settings, save_settings, set_key)

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
        self.agent = Agent(DATA_PATH, self.publish)
        self.pending = None
        self.pending_lock = threading.Lock()
        self.setup_lock = threading.Lock()
        self.source = 0
        info = Gio.DBusNodeInfo.new_for_xml(XML)
        self.registration = self.bus.register_object(OBJECT_PATH, info.interfaces[0], self._call, None, None)
        self.owner = Gio.bus_own_name_on_connection(self.bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, self._lost)
        self.publish(self.agent.snapshot())

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
            self.bus.emit_signal(None, OBJECT_PATH, INTERFACE, "Changed", GLib.Variant("(s)", (json.dumps(state),)))
        return GLib.SOURCE_REMOVE

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
        if r.get("op") in ("submit", "configure", "validate", "check_key"):
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
            return self.agent.snapshot()
        if op == "submit":
            # Only paths cross the bus; the service reads the files itself, so a
            # large attachment never has to fit inside a D-Bus request.
            return self.agent.submit(r.get("message", ""), r.get("conversation", ""),
                                     r.get("attachments", []))
        if op == "approve":
            self.agent.approve(r.get("id"), r.get("version"))
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
            return {**settings, **key_state(), "errors": errors}
        elif op == "validate":
            if self.agent.busy():
                raise ValueError("Finish or cancel the current task before testing models")
            return validate_models()
        elif op == "check_key":
            return check_key()
        else:
            raise ValueError("Unknown CLIVE operation")
        return self.agent.snapshot()

    def stop(self):
        self.agent.close()
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def run(self):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, self.stop)
        self.loop.run()


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
