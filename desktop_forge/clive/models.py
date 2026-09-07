"""Ollama transport. Only inference is retried; tools never run in this layer."""
from __future__ import annotations

import json
import os
import threading
import time

from .settings import get_key


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
        body = {"model": model, "messages": messages, "stream": True, "think": False}
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
            if os.environ.get("CLIVE_DEBUG"):
                print(f"clive: {where} model {model} structured reply: extracted={content[:600]!r} "
                      f"content={result['content'][:600]!r} thinking={thinking[:300]!r}", flush=True)
            if not content:
                raise ModelOutputInvalid(f"The {where} model {model} did not return the JSON CLIVE "
                                         f"requires. Choose a different {where} model in CLIVE settings.")
            result["content"] = content
        if calls:
            result["tool_calls"] = calls
        return result

    def chat(self, messages: list, tools: list | None = None, schema: dict | None = None) -> dict:
        try:
            return self._chat(messages, tools, schema)
        except ModelUnavailable as exc:
            if self.local or self.cancel.is_set():
                raise
            self.local = True
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
