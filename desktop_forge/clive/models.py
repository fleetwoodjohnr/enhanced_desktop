"""Ollama transport. Only inference is retried; tools never run in this layer."""
from __future__ import annotations

import json
import os
import threading
import time

from .settings import get_key


ALLOWED_FIELDS = ("role", "content", "images", "tool_calls", "tool_name")
# /api/show answers per (base URL, model); they do not change while running.
_SHOW_CACHE: dict = {}
WITHHELD_IMAGES = ("[An image was not sent: sending images to cloud models is turned off in "
                   "CLIVE settings (Privacy). Tell the user if you needed it.]")
WITHHELD_FILES = ("[Attached files were not sent: sending attachments to cloud models is turned "
                  "off in CLIVE settings (Privacy). Tell the user if you needed them.]")


class Cancelled(Exception):
    pass


class ModelUnavailable(Exception):
    pass


class ModelOutputInvalid(Exception):
    """The model answered, but not with the JSON the request asked for.

    Deliberately not a ModelUnavailable: the service is working, so retrying the
    same request on the local model only spends minutes reaching the same place.
    The model name is the thing the user has to change.
    """


def safe_message(exc: Exception, default: str) -> str:
    """Return only text CLIVE wrote itself. Libraries raise ValueError too.

    json and jsonschema messages carry the payload or the whole schema, and httpx
    messages carry URLs and headers. A message is trustworthy only when it comes
    from a builtin CLIVE raised on purpose, or from a CLIVE exception class.
    """
    module = type(exc).__module__
    if module == "builtins" and isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)[:500]
    if module.startswith("desktop_forge."):
        return str(exc)[:500]
    return default


def extract_json(text: str) -> str:
    """Recover the requested JSON object from fenced or narrated model output.

    Models honour `format` inconsistently: the object arrives wrapped in a code
    fence, or with a sentence around it. Scanning from each `{` means only a
    real object is accepted -- there is no partial or repaired parse.
    """
    text = text.strip()
    if text.startswith("```"):
        fenced = text[3:].split("\n", 1)
        if len(fenced) == 2:
            text = fenced[1].rsplit("```", 1)[0]
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            _, end = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        return text[start:end]
    return ""


def credential():
    try:
        return get_key()
    except Exception:
        raise ModelUnavailable("GNOME Keyring could not provide the Ollama API key") from None


def generation_schema(value):
    """Keep size limits in local validation, out of Ollama's expanded grammar."""
    if isinstance(value, dict):
        return {key: generation_schema(item) for key, item in value.items()
                if key not in ("minLength", "maxLength", "minItems", "maxItems", "uniqueItems")}
    if isinstance(value, list):
        return [generation_schema(item) for item in value]
    return value


class Models:
    def __init__(self, settings: dict, cancel: threading.Event, report=lambda **k: None):
        self.settings, self.cancel, self.report = settings, cancel, report
        self.local = not settings["cloud_enabled"] or not settings["free_account_confirmed"]
        self.response = None
        self.source = None
        # Set once the cloud failed during this task; the rest of the task
        # stays local rather than retrying a service that just refused.
        self.fell_back = False

    def follow(self, source):
        """Re-read the selected model from `source()` before every request.

        This is what makes switching between the cloud and local model take
        effect straight away, mid-task included, without restarting anything.
        """
        self.source = source

    def _refresh(self):
        if self.source is None:
            return
        fresh = self.source()
        wanted_local = not fresh["cloud_enabled"] or not fresh["free_account_confirmed"]
        if wanted_local != self.local and not (self.fell_back and not wanted_local):
            where = "local" if wanted_local else "cloud"
            self.report(notice=f"Switched to the {where} model.", partial="")
        self.settings = fresh
        self.local = wanted_local or self.fell_back

    def stop(self):
        self.cancel.set()
        if self.response is not None:
            try:
                self.response.close()
            except Exception:
                pass

    def _chat(self, messages: list, tools: list | None, schema: dict | None) -> dict:
        import httpx
        if self.cancel.is_set():
            raise Cancelled()
        key = "" if self.local else credential()
        if not self.local and not key:
            raise ModelUnavailable("Ollama Cloud needs an API key")
        base = "http://127.0.0.1:11434" if self.local else "https://ollama.com"
        model = self.settings["local_model" if self.local else "cloud_model"]
        self.report(mode="local" if self.local else "cloud", model=model, partial="")
        body = {"model": model, "messages": self._outgoing(messages), "stream": True, "think": False}
        if self.local:
            body.update(keep_alive="2m", options={"num_ctx": self.settings["local_context"], "num_predict": 2048})
        if tools:
            body["tools"] = tools
        if schema:
            body["format"] = generation_schema(schema)
        result = {"role": "assistant", "content": ""}
        calls = []
        # Reasoning is kept out of the returned message: it must not reach the
        # streamed preview, the saved conversation, or the next request body.
        thinking = ""
        done = False
        last_report = 0
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            with httpx.Client(timeout=httpx.Timeout(900 if self.local else 180, connect=10), follow_redirects=False) as client:
                with client.stream("POST", f"{base}/api/chat", json=body, headers=headers) as response:
                    self.response = response
                    if response.status_code != 200:
                        labels = {401: "Cloud authentication failed", 403: "Model access denied", 404: "Model is unavailable", 402: "Free allowance exhausted", 429: "Cloud usage limit reached"}
                        raise ModelUnavailable(labels.get(response.status_code, f"Model service returned HTTP {response.status_code}"))
                    for line in response.iter_lines():
                        if self.cancel.is_set():
                            raise Cancelled()
                        if not line:
                            continue
                        item = json.loads(line)
                        if item.get("error"):
                            raise ModelUnavailable("Ollama could not complete this request")
                        chunk = item.get("message", {})
                        result["content"] += chunk.get("content", "")
                        thinking += chunk.get("thinking", "")
                        if len(result["content"]) + len(thinking) > 100_000:
                            raise ModelUnavailable("Model response exceeded the size limit")
                        calls.extend(chunk.get("tool_calls", []))
                        if not schema and time.monotonic() - last_report > 0.12:
                            self.report(partial=result["content"])
                            last_report = time.monotonic()
                        if item.get("done"):
                            done = True
                    if not done:
                        raise ModelUnavailable("Model connection ended before the response finished")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if self.cancel.is_set():
                raise Cancelled() from None
            # HTTP exceptions can contain URLs/headers. Do not expose them in the UI/log.
            raise ModelUnavailable("Local Ollama is unavailable" if self.local else "Ollama Cloud is unavailable") from None
        finally:
            self.response = None
        if schema:
            # Outside the block above: a rejected answer is not a transport error,
            # and must not be rewritten into "the service is unavailable".
            where = "local" if self.local else "cloud"
            content = extract_json(result["content"]) or extract_json(thinking)
            if os.environ.get("CLIVE_DEBUG") or self.settings.get("debug_logging"):
                print(f"clive: {where} model {model} structured reply: extracted={content[:600]!r} "
                      f"content={result['content'][:600]!r} thinking={thinking[:300]!r}", flush=True)
            if not content:
                raise ModelOutputInvalid(f"The {where} model {model} did not return the JSON CLIVE "
                                         f"requires. Choose a different {where} model in CLIVE settings.")
            result["content"] = content
        if calls:
            result["tool_calls"] = calls
        return result

    def _endpoint(self) -> tuple[str, str, str]:
        """(base URL, model, key) of the model the next request will use."""
        self._refresh()
        if self.local:
            return "http://127.0.0.1:11434", self.settings["local_model"], ""
        try:
            key = credential()
        except ModelUnavailable:
            key = ""
        return "https://ollama.com", self.settings["cloud_model"], key

    def _show(self) -> dict:
        """What Ollama says about the active model, asked once per model."""
        base, model, key = self._endpoint()
        cached = _SHOW_CACHE.get((base, model))
        if cached is not None:
            return cached
        info = {}
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False) as client:
                response = client.post(base + "/api/show", json={"model": model},
                                       headers={"Authorization": f"Bearer {key}"} if key else {})
            if response.status_code == 200:
                info = response.json()
        except Exception:  # noqa: BLE001 - unknown capabilities are not a failure
            info = {}
        _SHOW_CACHE[(base, model)] = info
        return info

    def supports_vision(self) -> bool:
        """False only when Ollama reports the model's capabilities without vision."""
        capabilities = self._show().get("capabilities")
        return capabilities is None or "vision" in capabilities

    def attachment_budget(self) -> int:
        """Characters of attached text that fit, leaving room for the task itself.

        Half the context window, at a conservative three characters a token.
        The local window is the user's setting; a cloud model's comes from
        Ollama, or 32k tokens when it does not say.
        """
        if self.local:
            tokens = int(self.settings.get("local_context", 8192))
        else:
            model_info = self._show().get("model_info") or {}
            reported = [v for k, v in model_info.items() if k.endswith(".context_length")]
            tokens = int(reported[0]) if reported else 32768
        return max(4000, tokens * 3 // 2)

    def _outgoing(self, messages: list) -> list:
        """The messages as they may leave this machine.

        Only fields Ollama understands are sent. For a cloud model, the Privacy
        settings decide whether images and attached files go too; when they do
        not, the model is told something was withheld rather than left to guess.
        """
        images = self.local or self.settings.get("cloud_images", True)
        files = self.local or self.settings.get("cloud_attachments", True)
        result = []
        for message in messages:
            clean = {key: value for key, value in message.items() if key in ALLOWED_FIELDS}
            if not files and message.get("attachment"):
                clean = {"role": message.get("role", "user"), "content": WITHHELD_FILES}
            elif not images and clean.get("images"):
                clean.pop("images")
                clean["content"] = (clean.get("content") or "") + "\n" + WITHHELD_IMAGES
            result.append(clean)
        return result

    def chat(self, messages: list, tools: list | None = None, schema: dict | None = None) -> dict:
        self._refresh()
        try:
            return self._chat(messages, tools, schema)
        except ModelUnavailable as exc:
            if self.local or self.cancel.is_set() or not self.settings.get("fallback_to_local", True):
                raise
            self.local = True
            self.fell_back = True
            self.report(notice=f"{exc}. Continuing with the local model.", partial="")
            # Discard an incomplete cloud response; only replay the model request.
            return self._chat(messages, tools, schema)

    def web(self, operation: str, arguments: dict) -> dict:
        import httpx
        if self.cancel.is_set():
            raise Cancelled()
        key = credential()
        if not key:
            raise ModelUnavailable("Live web research needs an Ollama API key and an internet connection")
        try:
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.post(f"https://ollama.com/api/{operation}", json=arguments,
                                       headers={"Authorization": f"Bearer {key}"})
                if response.status_code != 200:
                    raise ModelUnavailable("Live web research is unavailable or its allowance is exhausted")
                data = response.json()
                # Bound tool context, preserving result URLs for citations.
                if operation == "web_search":
                    return {"results": [{"title": r.get("title", ""), "url": r.get("url", ""),
                        "content": r.get("content", "")[:2500]} for r in data.get("results", [])[:5]]}
                return {"title": data.get("title", ""), "url": arguments["url"],
                        "content": data.get("content", "")[:14000]}
        except (httpx.HTTPError, ValueError):
            raise ModelUnavailable("Live web research could not connect") from None
