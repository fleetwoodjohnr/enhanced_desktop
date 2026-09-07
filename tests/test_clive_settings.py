"""The settings contract: what persists, what is refused, and what survives it.

These cover the failure that made CLIVE's settings look broken -- one rejected
field aborting the whole save and taking a freshly pasted API key with it.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_forge.clive import service, settings as clive_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "clive.json"
        self.enterContext(patch.object(clive_settings, "SETTINGS_PATH", self.path))

    def stored(self):
        return json.loads(self.path.read_text())

    def test_cloud_without_confirmation_is_turned_off_rather_than_refused(self):
        # The original code raised here, so nothing was written at all and the
        # API key sent alongside was discarded with it.
        result, errors = clive_settings.save_settings(
            {"cloud_enabled": True, "cloud_model": "gemma4:31b"})
        self.assertEqual(errors, {})
        self.assertFalse(result["cloud_enabled"])
        self.assertTrue(self.path.exists(), "a refused confirmation blocked the whole save")
        self.assertEqual(self.stored()["cloud_model"], "gemma4:31b")

    def test_confirmed_cloud_is_stored_and_reloaded(self):
        result, errors = clive_settings.save_settings(
            {"cloud_enabled": True, "free_account_confirmed": True})
        self.assertEqual(errors, {})
        self.assertTrue(result["cloud_enabled"])
        self.assertTrue(clive_settings.load_settings()["cloud_enabled"])

    def test_hand_edited_file_cannot_enable_cloud_without_the_confirmation(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"cloud_enabled": True, "free_account_confirmed": False}))
        self.assertFalse(clive_settings.load_settings()["cloud_enabled"])

    def test_every_setting_round_trips(self):
        home = str(Path.home())
        values = {"cloud_model": "gemma4:31b", "local_model": "qwen3.5:4b",
                  "cloud_enabled": True, "free_account_confirmed": True,
                  "local_context": 32768, "max_turns": 40,
                  "approval_mode": "read_only", "system_prompt": "Answer briefly.",
                  "context_files": [home + "/notes.md"]}
        result, errors = clive_settings.save_settings(values)
        self.assertEqual(errors, {})
        self.assertEqual({k: result[k] for k in values}, values)
        self.assertEqual({k: clive_settings.load_settings()[k] for k in values}, values)

    def test_context_and_turns_are_no_longer_silently_dropped(self):
        # Both keys used to be ignored: they are read by the transport and the
        # agent but had no validation branch and no UI.
        result, _ = clive_settings.save_settings({"local_context": 16384, "max_turns": 32})
        self.assertEqual(result["local_context"], 16384)
        self.assertEqual(result["max_turns"], 32)

    def test_numeric_settings_are_bounded(self):
        low, high = clive_settings.RANGES["local_context"]
        for value in (low, high):
            _, errors = clive_settings.save_settings({"local_context": value})
            self.assertEqual(errors, {}, value)
        for value in (low - 1, high + 1, "eight", None, 1.5):
            _, errors = clive_settings.save_settings({"local_context": value})
            self.assertIn("local_context", errors, value)
        low, high = clive_settings.RANGES["max_turns"]
        for value in (low - 1, high + 1, True):
            # True must not slip through as 1: bool is a subclass of int.
            _, errors = clive_settings.save_settings({"max_turns": value})
            self.assertIn("max_turns", errors, value)

    def test_a_rejected_field_persists_nothing_and_names_itself(self):
        clive_settings.save_settings({"cloud_model": "gemma4:31b", "local_model": "qwen3.5:4b"})
        result, errors = clive_settings.save_settings(
            {"cloud_model": "two words", "local_model": "other:4b"})
        self.assertEqual(set(errors), {"cloud_model"})
        self.assertIn("Cloud model", errors["cloud_model"])
        self.assertEqual(result["cloud_model"], "gemma4:31b")
        self.assertEqual(self.stored()["local_model"], "qwen3.5:4b",
                         "a half-valid save was written anyway")

    def test_local_fallback_rejects_a_cloud_model(self):
        _, errors = clive_settings.save_settings({"local_model": "gemma4:31b-cloud"})
        self.assertIn("local_model", errors)

    def test_approval_defaults_to_asking_and_refuses_an_unknown_mode(self):
        self.assertEqual(clive_settings.load_settings()["approval_mode"], "always")
        for value in ("sometimes", "", None, True):
            _, errors = clive_settings.save_settings({"approval_mode": value})
            self.assertIn("approval_mode", errors, value)
        for value in clive_settings.APPROVAL_MODES:
            result, errors = clive_settings.save_settings({"approval_mode": value})
            self.assertEqual(errors, {}, value)
            self.assertEqual(result["approval_mode"], value)

    def test_extra_instructions_are_trimmed_and_bounded(self):
        result, errors = clive_settings.save_settings({"system_prompt": "  be terse  "})
        self.assertEqual(errors, {})
        self.assertEqual(result["system_prompt"], "be terse")
        _, errors = clive_settings.save_settings(
            {"system_prompt": "x" * (clive_settings.MAX_SYSTEM_PROMPT + 1)})
        self.assertIn("system_prompt", errors)
        _, errors = clive_settings.save_settings({"system_prompt": 42})
        self.assertIn("system_prompt", errors)

    def test_attached_files_take_the_same_boundary_as_the_file_tools(self):
        home = Path.home()
        result, errors = clive_settings.save_settings({"context_files": [str(home / "a.md")]})
        self.assertEqual(errors, {})
        self.assertEqual(result["context_files"], [str(home / "a.md")])
        for value in (["relative.md"], ["/etc/passwd"], [str(home / ".ssh/id_rsa")],
                      "notes.md", [7]):
            _, errors = clive_settings.save_settings({"context_files": value})
            self.assertIn("context_files", errors, value)
        too_many = [str(home / f"n{i}.md") for i in range(clive_settings.MAX_FILES + 1)]
        _, errors = clive_settings.save_settings({"context_files": too_many})
        self.assertIn("context_files", errors)

    def test_readers_never_share_the_default_attachment_list(self):
        # A mutable value in DEFAULTS is one caller's append away from becoming
        # everybody's setting.
        first, second = clive_settings.load_settings(), clive_settings.load_settings()
        first["context_files"].append("/tmp/mutated")
        self.assertEqual(second["context_files"], [])
        self.assertEqual(clive_settings.DEFAULTS["context_files"], [])


class Busy:
    def __init__(self, busy):
        self._busy = busy

    def busy(self):
        return self._busy


class ConfigureTests(unittest.TestCase):
    """`configure` must never let one half of the request destroy the other."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "clive.json"
        self.enterContext(patch.object(clive_settings, "SETTINGS_PATH", self.path))
        self.stored_key = ""
        self.service = service.Service.__new__(service.Service)
        self.service.agent = Busy(False)

    def configure(self, request, key_error=None):
        def set_key(value):
            if key_error:
                raise RuntimeError(key_error)
            self.stored_key = value

        self.enterContext(patch.object(service, "set_key", set_key))
        self.enterContext(patch.object(service, "key_state",
                                       lambda: {"has_key": bool(self.stored_key), "keyring_error": ""}))
        return self.service._dispatch({"op": "configure", **request})

    def test_a_rejected_model_name_keeps_the_new_api_key(self):
        result = self.configure({"api_key": "secret", "settings": {"cloud_model": "two words"}})
        self.assertEqual(self.stored_key, "secret")
        self.assertTrue(result["has_key"])
        self.assertIn("cloud_model", result["errors"])

    def test_a_locked_keyring_keeps_the_new_model_settings(self):
        result = self.configure({"api_key": "secret", "settings": {"cloud_model": "gemma4:31b"}},
                                key_error=clive_settings.KEYRING_UNAVAILABLE)
        self.assertEqual(result["cloud_model"], "gemma4:31b")
        self.assertEqual(json.loads(self.path.read_text())["cloud_model"], "gemma4:31b")
        self.assertIn("api_key", result["errors"])
        self.assertIn("keyring", result["errors"]["api_key"].lower())
        self.assertNotIn("secret", json.dumps(result))

    def test_the_api_key_can_be_stored_during_a_task(self):
        self.service.agent = Busy(True)
        result = self.configure({"api_key": "secret"})
        self.assertEqual(self.stored_key, "secret")
        self.assertEqual(result["errors"], {})

    def test_model_changes_still_wait_for_the_task_to_finish(self):
        self.service.agent = Busy(True)
        result = self.configure({"settings": {"cloud_model": "gemma4:31b"}})
        self.assertIn("settings", result["errors"])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
