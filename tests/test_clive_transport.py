"""Exercise the real HTTP transport and model test without network or credentials."""
import json
import threading
import unittest
from unittest.mock import patch

from desktop_forge.clive.models import Cancelled, ModelOutputInvalid, Models, extract_json
from desktop_forge.clive.settings import DEFAULTS

try:
    import httpx
except ImportError:
    httpx = None


@unittest.skipUnless(httpx, "Install optional CLIVE dependencies")
class TransportTests(unittest.TestCase):
    def transport(self, handler, cancel=None):
        model = Models({**DEFAULTS, "cloud_enabled": True, "free_account_confirmed": True},
                       cancel or threading.Event())
        client = httpx.Client
        transport = httpx.MockTransport(handler)
        self.enterContext(patch("httpx.Client", side_effect=lambda **kw: client(transport=transport, **kw)))
        self.enterContext(patch("desktop_forge.clive.models.get_key", return_value="test-secret"))
        return model

    def test_partial_cloud_tool_call_is_discarded_before_local_retry(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.host == "ollama.com":
                # No final done record: this is not a completed tool request.
                return httpx.Response(200, text=json.dumps({"message": {
                    "content": "partial", "tool_calls": [{"function": {"name": "todo_add", "arguments": {"text": "unsafe partial"}}}]}}))
            return httpx.Response(200, text='{"message":{"content":"local answer"},"done":true}\n')
        model = self.transport(handler)
        messages = [{"role": "tool", "tool_name": "file_write", "content": '{"created":"note.txt"}'}]
        result = model.chat(messages)
        self.assertEqual(result, {"role": "assistant", "content": "local answer"})
        self.assertEqual(len(requests), 2)
        self.assertEqual(json.loads(requests[1].content)["messages"], messages)
        self.assertNotIn("authorization", requests[1].headers)

    def test_malformed_stream_falls_back_and_preserves_completed_context(self):
        hosts = []
        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(200, text="invalid JSON" if len(hosts) == 1 else
                                  '{"message":{"content":"ready"},"done":true}')
        self.assertEqual(self.transport(handler).chat([])["content"], "ready")
        self.assertEqual(hosts, ["ollama.com", "127.0.0.1"])

    def test_cancel_during_stream_does_not_fall_back(self):
        cancelled = threading.Event()
        hosts = []
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"message":{"content":"partial"}}\n'
                cancelled.set()
                yield b'{"message":{"content":"too late"},"done":true}\n'
        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(200, stream=Stream())
        with self.assertRaises(Cancelled):
            self.transport(handler, cancelled).chat([])
        self.assertEqual(hosts, ["ollama.com"])

    def test_keyring_failure_uses_local_without_exposing_exception(self):
        reports = []
        def handler(request):
            self.assertEqual(request.url.host, "127.0.0.1")
            return httpx.Response(200, text='{"message":{"content":"ready"},"done":true}')
        model = self.transport(handler)
        model.report = lambda **kw: reports.append(kw)
        with patch("desktop_forge.clive.models.get_key", side_effect=RuntimeError("secret-detail")):
            self.assertEqual(model.chat([])["content"], "ready")
        self.assertNotIn("secret-detail", json.dumps(reports))

    def test_fenced_structured_output_is_recovered_without_a_local_retry(self):
        hosts = []
        def handler(request):
            hosts.append(request.url.host)
            fenced = '```json\n{"ready": true}\n```'
            return httpx.Response(200, text=json.dumps({"message": {"content": fenced}, "done": True}))
        result = self.transport(handler).chat([], schema={"type": "object"})
        self.assertEqual(json.loads(result["content"]), {"ready": True})
        self.assertEqual(hosts, ["ollama.com"])

    def test_reasoning_only_answer_is_recovered_and_kept_out_of_the_preview(self):
        reports = []
        def handler(request):
            # A cloud model that ignores think:false leaves content empty.
            return httpx.Response(200, text="".join(
                json.dumps({"message": {"content": "", "thinking": part}}) + "\n" for part in
                ("The user said hello, so ", 'answer with {"ready": true}')) +
                json.dumps({"message": {"content": ""}, "done": True}))
        model = self.transport(handler)
        model.report = lambda **kw: reports.append(kw)
        result = model.chat([], schema={"type": "object"})
        self.assertEqual(json.loads(result["content"]), {"ready": True})
        self.assertNotIn("thinking", result)
        self.assertNotIn("The user said hello", json.dumps(reports))

    def test_unusable_structured_output_names_the_model_without_a_local_retry(self):
        hosts = []
        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(200, text=json.dumps(
                {"message": {"content": "Hi! How can I help you today?"}, "done": True}))
        with self.assertRaises(ModelOutputInvalid) as caught:
            self.transport(handler).chat([], schema={"type": "object"})
        self.assertIn(DEFAULTS["cloud_model"], str(caught.exception))
        # A working service returning a poor answer is a model choice, not an
        # outage: retrying it on the slow local model reaches the same place.
        self.assertEqual(hosts, ["ollama.com"])

    def test_narrated_object_is_extracted_and_junk_is_not(self):
        self.assertEqual(extract_json('Sure! {"a": 1} Hope that helps.'), '{"a": 1}')
        self.assertEqual(extract_json('```\n{"a": [1, {"b": 2}]}\n```'), '{"a": [1, {"b": 2}]}')
        for junk in ("", "no json here", "{ not closed", "[1, 2, 3]", '"blue"'):
            self.assertEqual(extract_json(junk), "")


@unittest.skipUnless(httpx, "Install optional CLIVE dependencies")
class ModelTestTests(unittest.TestCase):
    """The setup probe must not refuse a cloud model over an unanswerable /api/show."""

    def validate(self, show):
        from desktop_forge.clive import service
        probes = []
        client = httpx.Client
        transport = httpx.MockTransport(show)
        self.enterContext(patch("httpx.Client", side_effect=lambda **kw: client(transport=transport, **kw)))
        self.enterContext(patch.object(service, "probe_model",
                                       lambda candidate, vision=True: probes.append((candidate.local, vision))))
        self.enterContext(patch.object(service, "load_settings",
            lambda: {**DEFAULTS, "cloud_enabled": True, "free_account_confirmed": True}))
        self.enterContext(patch.object(service, "key_state", lambda: {"has_key": True, "keyring_error": ""}))
        self.enterContext(patch.object(service, "get_key", lambda: "test-secret"))
        return service.validate_models()["message"], probes

    def test_cloud_without_a_usable_api_show_is_still_tested_directly(self):
        def show(request):
            if request.url.host == "ollama.com":
                return httpx.Response(404, text="<html>Not Found</html>")
            return httpx.Response(200, json={"capabilities": ["tools", "vision"]})
        message, probes = self.validate(show)
        self.assertIn("did not report what", message)
        self.assertNotIn("unavailable (HTTP 404)", message)
        self.assertEqual(probes, [(False, True), (True, True)])

    def test_a_model_without_vision_is_tested_text_only_instead_of_refused(self):
        def show(request):
            return httpx.Response(200, json={"capabilities": ["tools"]})
        message, probes = self.validate(show)
        self.assertIn("does not support images", message)
        self.assertNotIn("choose another model", message)
        self.assertEqual(probes, [(False, False), (True, False)])

    def test_a_model_without_tool_calling_is_still_refused(self):
        def show(request):
            return httpx.Response(200, json={"capabilities": ["vision"]})
        message, probes = self.validate(show)
        self.assertIn("does not report tool calling", message)
        self.assertEqual(probes, [])


@unittest.skipUnless(httpx, "Install optional CLIVE dependencies")
class AvailableModelsTests(unittest.TestCase):
    """What the Cloud model and Local model dropdowns are offered."""

    def setUp(self):
        import os
        import tempfile
        from desktop_forge.clive import service
        self.service = service
        self.requests = []
        self.store = self.enterContext(tempfile.TemporaryDirectory())
        # An empty model folder unless a test downloads something into it.
        self.enterContext(patch.dict(os.environ, {"OLLAMA_MODELS": self.store}))

    def listing(self, endpoint, names, key=""):
        def tags(request):
            self.requests.append(request)
            return httpx.Response(200, json={"models": [{"name": name} for name in names]})
        client = httpx.Client
        transport = httpx.MockTransport(tags)
        with patch("httpx.Client", side_effect=lambda **kw: client(transport=transport, **kw)), \
                patch.object(self.service, "get_key", lambda: key):
            return self.service.available_models(endpoint)

    def download(self, *names):
        from pathlib import Path
        for name in names:
            model, tag = name.split(":")
            manifest = Path(self.store, "manifests", "registry.ollama.ai", "library", model, tag)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("{}")

    def test_local_list_leaves_out_cloud_proxies(self):
        names = self.listing("local", ["qwen3.5:9b", "gpt-oss:120b-cloud", "qwen3.5:4b"])
        self.assertEqual(names, ["qwen3.5:4b", "qwen3.5:9b"])
        self.assertEqual(self.requests[0].url.host, "127.0.0.1")

    def test_cloud_catalog_is_listed_without_a_key(self):
        names = self.listing("cloud", ["gpt-oss:120b", "gemma4:31b"])
        self.assertEqual(names, ["gemma4:31b", "gpt-oss:120b"])
        self.assertEqual(self.requests[0].url.host, "ollama.com")
        self.assertNotIn("authorization", self.requests[0].headers)

    def test_cloud_catalog_sends_the_key_when_there_is_one(self):
        self.listing("cloud", ["gemma4:31b"], key="test-secret")
        self.assertEqual(self.requests[0].headers["authorization"], "Bearer test-secret")

    def test_a_locked_keyring_still_lists_the_cloud_catalog(self):
        def locked():
            raise RuntimeError("GNOME Keyring is locked or unavailable.")
        client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"models": [{"name": "gemma4:31b"}]}))
        with patch("httpx.Client", side_effect=lambda **kw: client(transport=transport, **kw)), \
                patch.object(self.service, "get_key", locked):
            self.assertEqual(self.service.available_models("cloud"), ["gemma4:31b"])

    def test_downloaded_models_hidden_by_another_server_are_explained(self):
        self.download("qwen3.5:4b", "smollm2:1.7b")
        with self.assertRaises(ValueError) as caught:
            self.listing("local", [])
        message = str(caught.exception)
        self.assertIn("none of the 2 models", message)
        self.assertIn("qwen3.5:4b", message)
        self.assertIn("systemctl disable --now ollama.service", message)

    def test_downloaded_models_the_server_lists_are_not_an_error(self):
        self.download("qwen3.5:4b")
        self.assertEqual(self.listing("local", ["qwen3.5:4b"]), ["qwen3.5:4b"])

    def test_nothing_downloaded_is_an_empty_list_not_an_error(self):
        self.assertEqual(self.listing("local", []), [])

    def test_a_store_of_only_cloud_proxies_is_not_mistaken_for_another_server(self):
        self.download("gpt-oss:120b-cloud")
        self.assertEqual(self.listing("local", ["gpt-oss:120b-cloud"]), [])
