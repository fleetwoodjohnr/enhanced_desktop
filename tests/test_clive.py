from __future__ import annotations

import json
import contextlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desktop_forge.clive import agent as agent_module
from desktop_forge.clive.agent import (APP_AUTOMATION_TOOLS, Agent,
                                       complete_app_permissions)
from desktop_forge.clive.models import (ModelUnavailable, Models, generation_schema,
                                        safe_message)
from desktop_forge.clive.policy import (ScopeChanged, auto_approved, check_scope,
                                        local_path, public_url)
from desktop_forge.clive.settings import DEFAULTS
from desktop_forge.clive.storage import History
from desktop_forge.clive.integrations import AccessStore, build_registry
from desktop_forge.clive.integrations import mail as mail_integration
from desktop_forge.clive.tools import BY_NAME, READ_ONLY, Tools, blocked_surface

try:
    import langgraph.graph
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False


def plan(tools=None, folders=None):
    return {"summary": "Create a test file", "answer": "", "steps": ["Create the requested file"],
            "permissions": {"tools": tools or [], "folders": folders or [], "apps": [], "web": False}}


def tool(name, **arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


class FakeDesktop:
    def __init__(self, _cancel):
        self.closed = False
    def close(self):
        self.closed = True


@unittest.skipUnless(HAS_AGENT, "Install optional CLIVE dependencies to run agent integration tests")
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = patch("pathlib.Path.home", return_value=self.root)
        self.home.start()
        self.addCleanup(self.home.stop)
        self.enterContext(patch.object(mail_integration, "ACCOUNTS_PATH", self.root / "mail.json"))
        self.addCleanup(self.tmp.cleanup)
        self.agents = []
        # Agent behavior must never inherit the developer machine's live CLIVE
        # approval choice (notably Auto-approve everything).
        self.settings()

    def tearDown(self):
        for agent in self.agents:
            agent.close()
            if agent.checkpoint_connection:
                agent.checkpoint_connection.close()
            agent.history.close()

    def settings(self, **changes):
        """Pin what the agent reads, so a test never depends on the real file."""
        values = {**DEFAULTS, "context_files": [], **changes}
        self.enterContext(patch.object(agent_module, "load_settings", lambda: dict(values)))
        return values

    def agent(self, responses):
        class FakeModels:
            def __init__(self, settings, cancel, report):
                self.cancel = cancel
            def chat(self, messages, tools=None, schema=None):
                return responses.pop(0)
            def stop(self):
                self.cancel.set()
        agent = Agent(self.root / "data", model_factory=FakeModels,
                      desktop_factory=FakeDesktop, apps_factory=lambda: [])
        self.agents.append(agent)
        return agent

    def wait(self, agent):
        agent.worker.join(10)
        self.assertFalse(agent.worker.is_alive(), "Agent did not finish its turn")
        return agent.snapshot()

    def test_task_waits_for_one_approval_then_verifies_file(self):
        path = self.root / "note.txt"
        agent = self.agent([
            {"content": json.dumps(plan(["file_write", "file_read"], [str(self.root)]))},
            tool("file_write", path=str(path), text="hello"), tool("file_read", path=str(path)),
            {"role": "assistant", "content": "Created and verified note.txt."},
        ])
        submitted = agent.submit("Create note.txt containing hello")
        state = self.wait(agent)
        self.assertEqual(state["status"], "awaiting_approval", state)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            agent.approve(submitted["id"], 99)
        agent.approve(submitted["id"], state["version"])
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(path.read_text(), "hello")
        self.assertEqual(len(state["actions"]), 2)
        self.assertEqual(len(agent.history.messages(state["conversation"])), 2)

    def test_scope_expansion_waits_without_executing(self):
        path = self.root / "note.txt"
        agent = self.agent([
            {"content": json.dumps(plan(["file_search"], [str(self.root)]))},
            tool("file_write", path=str(path), text="hello"),
            {"role": "assistant", "content": "Created note.txt."},
        ])
        agent.submit("Find a file")
        state = self.wait(agent)
        agent.approve(state["id"], 1)
        state = self.wait(agent)
        self.assertEqual(state["status"], "awaiting_approval", state)
        self.assertEqual(state["version"], 2)
        self.assertFalse(path.exists())
        agent.approve(state["id"], 2)
        self.assertEqual(self.wait(agent)["status"], "complete")
        self.assertEqual(path.read_text(), "hello")

    def test_invalid_model_tool_is_corrected_without_repeating_completed_actions(self):
        path = self.root / "note.txt"
        agent = self.agent([
            {"content": json.dumps(plan(["file_write"], [str(path)]))},
            tool("file_write", path=str(path), text="hello"),
            tool("tool", path=str(path)),
            {"role": "assistant", "content": "Created note.txt containing hello."},
        ])
        agent.submit("Create note.txt")
        state = self.wait(agent)
        agent.approve(state["id"], 1)
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(path.read_text(), "hello")
        self.assertEqual(len(state["actions"]), 1)

    def test_cancel_before_approval_runs_no_tools(self):
        agent = self.agent([{"content": json.dumps(plan(["todo_add"]))}])
        agent.submit("Add a task")
        state = self.wait(agent)
        agent.cancel()
        with self.assertRaises(ValueError):
            agent.approve(state["id"], 1)
        self.assertEqual(agent.snapshot()["actions"], [])

    def test_restarted_task_is_paused_not_replayed_and_history_deleted(self):
        agent = self.agent([{"content": json.dumps(plan(["todo_add"]))}])
        agent.submit("Add a task")
        state = self.wait(agent)
        restarted = self.agent([])
        self.assertEqual(restarted.snapshot()["status"], "paused")
        agent.cancel()
        restarted.delete(state["conversation"])
        self.assertEqual(restarted.history.tasks(), [])
        self.assertEqual(restarted.history.list_conversations(), [])
        with contextlib.closing(sqlite3.connect(self.root / "data/checkpoints.sqlite")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)

    def test_uncertain_action_is_not_retried(self):
        agent = self.agent([{"content": json.dumps(plan(["file_write"], [str(self.root)]))},
                            tool("file_write", path=str(self.root / "exists"), text="new")])
        (self.root / "exists").write_text("original")
        agent.submit("Create exists")
        state = self.wait(agent)
        agent.approve(state["id"], 1)
        state = self.wait(agent)
        self.assertEqual(state["status"], "paused", state)
        self.assertEqual((self.root / "exists").read_text(), "original")

    def test_plan_that_is_not_json_reports_clive_wording_not_the_decoder(self):
        agent = self.agent([{"content": "Hi there!"}, {"content": "Still not JSON."}])
        agent.submit("Hello")
        state = self.wait(agent)
        self.assertEqual(state["status"], "paused", state)
        self.assertNotIn("Expecting value", state["notice"])
        self.assertNotIn("line 1 column 1", state["notice"])
        self.assertIn("task plan", state["notice"])

    def test_plan_that_breaks_the_schema_does_not_leak_it(self):
        denied = json.dumps({**plan(), "permissions": {"tools": ["shell"], "folders": [], "apps": [], "web": True}})
        agent = self.agent([{"content": denied}, {"content": denied}])
        agent.submit("Hello")
        state = self.wait(agent)
        self.assertEqual(state["status"], "paused", state)
        self.assertNotIn("enum", state["notice"])
        self.assertNotIn("is not one of", state["notice"])

    def test_plan_in_an_invented_shape_is_repaired_once(self):
        # What gemma4:31b actually returns: its own shape, because Ollama does
        # not enforce `format` for every cloud model.
        invented = {"answer": "", "steps": [{"step": "Search Documents", "permissions": ["~/Documents"]}],
                    "tools": [{"tool": "file_search"}]}
        asked = []
        class FakeModels:
            def __init__(self, settings, cancel, report):
                self.cancel = cancel
            def chat(self, messages, tools=None, schema=None):
                asked.append(messages)
                if len(asked) == 1:
                    return {"content": json.dumps(invented)}
                return {"content": json.dumps(plan(["file_search"], [str(self.root)]))}
            def stop(self):
                self.cancel.set()
        FakeModels.root = self.root
        agent = Agent(self.root / "data", model_factory=FakeModels,
                      desktop_factory=FakeDesktop, apps_factory=lambda: [])
        self.agents.append(agent)
        agent.submit("Find the text files in Documents")
        state = self.wait(agent)
        self.assertEqual(state["status"], "awaiting_approval", state)
        self.assertEqual(state["plan"]["permissions"]["tools"], ["file_search"])
        # The retry shows the model its own answer, and never runs anything.
        self.assertEqual(len(asked), 2)
        self.assertIn(json.dumps(invented), [m.get("content") for m in asked[1]])
        self.assertEqual(state["actions"], [])

    def test_conversational_reply_without_the_optional_plan_fields_completes(self):
        # Ollama does not enforce `required` on every model: a greeting arrives
        # as {"answer": ...} alone, and must not be treated as a broken plan.
        agent = self.agent([{"content": json.dumps({"answer": "Hello! How can I help you today?"})}])
        agent.submit("Hello")
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(state["messages"][-1]["content"], "Hello! How can I help you today?")
        self.assertEqual(state["actions"], [])

    def test_every_graphical_app_is_in_the_compact_planner_catalog(self):
        catalog = [
            {"desktop_id": f"org.example.App{index}.desktop", "name": f"App {index}",
             "description": "large detail that should not enter the planner"}
            for index in range(15)
        ]
        catalog.append({"desktop_id": "libreoffice-calc.desktop",
                        "name": "LibreOffice Calc", "description": "Spreadsheet"})
        seen = []

        class RecordingModels:
            def __init__(self, settings, cancel, report):
                self.cancel = cancel
            def chat(self, messages, tools=None, schema=None):
                seen.append(messages[0]["content"])
                return {"content": json.dumps({"answer": "Ready."})}
            def stop(self):
                self.cancel.set()

        agent = Agent(self.root / "catalog", model_factory=RecordingModels,
                      desktop_factory=FakeDesktop, apps_factory=lambda: catalog)
        self.agents.append(agent)
        agent.submit("Hello")
        self.wait(agent)
        self.assertIn("libreoffice-calc.desktop", seen[0])
        self.assertIn("org.example.App0.desktop", seen[0])
        self.assertNotIn("large detail", seen[0])

    def test_gui_action_plan_gets_complete_app_scoped_fallback_permissions(self):
        gui = plan(["desktop_type_text"])
        gui["permissions"]["apps"] = ["libreoffice-calc.desktop"]
        completed = complete_app_permissions(gui)
        self.assertEqual(completed["permissions"]["tools"],
                         ["desktop_type_text", *[name for name in APP_AUTOMATION_TOOLS
                                                if name != "desktop_type_text"]])
        self.assertEqual(completed["permissions"]["apps"],
                         ["libreoffice-calc.desktop"])

    def test_app_launch_waits_for_window_readiness(self):
        desktop = Mock()
        desktop.wait_for_app.return_value = True
        launcher = Mock()
        launcher.launch.return_value = True
        approved = plan(["app_launch"])
        approved["permissions"]["apps"] = ["libreoffice-calc.desktop"]
        calc = [{"desktop_id": "libreoffice-calc.desktop", "name": "LibreOffice Calc",
                 "categories": "Office;Spreadsheet;"}]
        registry = build_registry(AccessStore(self.root / "access.json",
                                              known_apps=lambda: ["libreoffice-calc.desktop"]),
                                  lambda: calc)
        with patch("desktop_forge.clive.tools.controllable_app", return_value=launcher):
            result = Tools(None, desktop, threading.Event(), registry).call(
                "app_launch", {"desktop_id": "libreoffice-calc.desktop"}, approved)
        self.assertEqual(result, {"launched": "libreoffice-calc.desktop", "ready": True})
        desktop.wait_for_app.assert_called_once_with("libreoffice-calc.desktop", timeout=10)

    # -- approval modes ----------------------------------------------------

    def test_read_only_mode_runs_a_reading_task_without_stopping(self):
        path = self.root / "note.txt"
        path.write_text("hello")
        self.settings(approval_mode="read_only")
        agent = self.agent([
            {"content": json.dumps(plan(["file_read"], [str(self.root)]))},
            tool("file_read", path=str(path)),
            {"role": "assistant", "content": "It says hello."},
        ])
        agent.submit("Read note.txt")
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(state["messages"][-1]["content"], "It says hello.")
        # Skipping the stop is not a reason to stop saying what was allowed.
        self.assertIn("file_read", state["preview"])

    def test_read_only_mode_still_asks_before_a_write(self):
        path = self.root / "note.txt"
        self.settings(approval_mode="read_only")
        agent = self.agent([
            {"content": json.dumps(plan(["file_write"], [str(self.root)]))},
            tool("file_write", path=str(path), text="hello"),
        ])
        agent.submit("Create note.txt")
        state = self.wait(agent)
        self.assertEqual(state["status"], "awaiting_approval", state)
        self.assertFalse(path.exists())

    def test_unattended_mode_runs_a_write_without_approval(self):
        path = self.root / "note.txt"
        self.settings(approval_mode="never")
        agent = self.agent([
            {"content": json.dumps(plan(["file_write"], [str(self.root)]))},
            tool("file_write", path=str(path), text="hello"),
            {"role": "assistant", "content": "Created note.txt."},
        ])
        agent.submit("Create note.txt containing hello")
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(path.read_text(), "hello")

    def test_a_widened_scope_still_stops_when_the_new_tool_writes(self):
        # Auto-approval removes the human stop, not the enforcement: a tool the
        # plan never named still has to come back and ask.
        path = self.root / "note.txt"
        path.write_text("hello")
        self.settings(approval_mode="read_only")
        agent = self.agent([
            {"content": json.dumps(plan(["file_read"], [str(self.root)]))},
            tool("file_read", path=str(path)),
            tool("file_trash", path=str(path)),
        ])
        agent.submit("Read note.txt")
        state = self.wait(agent)
        self.assertEqual(state["status"], "awaiting_approval", state)
        self.assertTrue(path.exists(), "a widened scope acted before it was approved")
        self.assertEqual(state["version"], 2)

    # -- attachments and instructions --------------------------------------

    def test_an_attached_file_reaches_the_model_and_the_transcript(self):
        attached = self.root / "notes.md"
        attached.write_text("the answer is 41")
        seen = []
        self.settings()
        agent = self.agent([{"content": json.dumps({"answer": "It says 41."})}])
        original = agent.model_factory

        class Recording(original):
            def chat(self, messages, tools=None, schema=None):
                seen.append(messages)
                return original.chat(self, messages, tools, schema)

        agent.model_factory = Recording
        agent.submit("What does this say?", attachments=[str(attached)])
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertIn("the answer is 41", json.dumps(seen[0]))
        self.assertIn("Attached: notes.md", state["messages"][0]["content"])

    def test_an_unreadable_attachment_is_refused_before_the_task_starts(self):
        self.settings()
        agent = self.agent([])
        with self.assertRaises(ValueError):
            agent.submit("Look at this", attachments=["/etc/passwd"])
        self.assertEqual(agent.snapshot()["status"], "idle")
        self.assertEqual(agent.history.list_conversations(), [])

    def test_always_attached_files_ride_along_and_a_missing_one_is_reported(self):
        always = self.root / "standing.md"
        always.write_text("prefer metric units")
        seen = []
        self.settings(context_files=[str(always), str(self.root / "gone.md")])
        agent = self.agent([{"content": json.dumps({"answer": "Noted."})}])
        original = agent.model_factory

        class Recording(original):
            def chat(self, messages, tools=None, schema=None):
                seen.append(messages)
                return original.chat(self, messages, tools, schema)

        agent.model_factory = Recording
        agent.submit("Hello")
        state = self.wait(agent)
        self.assertIn("prefer metric units", json.dumps(seen[0]))
        self.assertIn("gone.md", state.get("notice", ""))

    def test_extra_instructions_are_added_to_both_prompts_as_preference(self):
        seen = []
        self.settings(system_prompt="Always answer in French.")
        agent = self.agent([{"content": json.dumps({"answer": "Bonjour."})}])
        original = agent.model_factory

        class Recording(original):
            def chat(self, messages, tools=None, schema=None):
                seen.append(messages[0]["content"])
                return original.chat(self, messages, tools, schema)

        agent.model_factory = Recording
        agent.submit("Hello")
        self.wait(agent)
        self.assertIn("Always answer in French.", seen[0])
        # Framed as preference, never as a grant of new authority.
        self.assertIn("never grant", seen[0])


class ApprovalModeTests(unittest.TestCase):
    """The mode decides only what it should, and the two tool lists agree."""

    def test_every_read_only_name_is_a_real_tool(self):
        self.assertLessEqual(READ_ONLY, set(BY_NAME))

    def test_each_mode_decides_what_it_says_it_does(self):
        reading = plan(["file_read", "web_search"])
        writing = plan(["file_read", "file_write"])
        self.assertFalse(auto_approved(reading, "always", READ_ONLY))
        self.assertTrue(auto_approved(reading, "read_only", READ_ONLY))
        self.assertFalse(auto_approved(writing, "read_only", READ_ONLY))
        self.assertTrue(auto_approved(writing, "never", READ_ONLY))
        # A mode CLIVE does not recognise asks; it never assumes.
        self.assertFalse(auto_approved(reading, "whatever", READ_ONLY))

    def test_nothing_that_acts_is_treated_as_read_only(self):
        for name in ("file_write", "file_move", "file_trash", "app_launch", "app_focus", "open_url",
                     "todo_add", "todo_update", "reminder_add", "reminder_complete",
                     "desktop_action", "desktop_type", "desktop_screenshot",
                     "desktop_click", "desktop_scroll", "desktop_key",
                     "desktop_type_text", "desktop_shortcut"):
            self.assertNotIn(name, READ_ONLY, name)


class PolicyAndStorageTests(unittest.TestCase):
    def test_generation_grammar_keeps_types_without_expanding_large_bounds(self):
        schema = {"type": "object", "properties": {"text": {"type": "string", "maxLength": 12000}}, "required": ["text"]}
        self.assertEqual(generation_schema(schema)["properties"]["text"], {"type": "string"})
        self.assertEqual(schema["properties"]["text"]["maxLength"], 12000)

    def test_only_clive_wording_reaches_the_user(self):
        from jsonschema import ValidationError
        default = "CLIVE encountered an error."
        for hidden in (json.JSONDecodeError("Expecting value", "", 0), ValidationError("schema dump")):
            self.assertEqual(safe_message(hidden, default), default)
        self.assertEqual(safe_message(ValueError("Enter a shorter message"), default),
                         "Enter a shorter message")
        self.assertEqual(safe_message(ModelUnavailable("Cloud usage limit reached"), default),
                         "Cloud usage limit reached")

    def test_missing_permissions_normalize_to_the_narrowest_plan(self):
        from desktop_forge.clive.policy import normalize_plan, validate_plan
        filled = validate_plan({"answer": "hi", "unknown": "dropped"}, ["file_read"])
        self.assertEqual(filled, {"answer": "hi", "summary": "", "steps": [],
                                  "permissions": {"tools": [], "folders": [], "apps": [], "web": False}})
        # Fresh containers per call: one plan can never alias another's lists.
        self.assertIsNot(normalize_plan({})["steps"], normalize_plan({})["steps"])
        for broken in ("a string", None, {"steps": "not a list"}, {"permissions": {"web": "yes"}}):
            with self.assertRaises(ValueError):
                validate_plan(broken, ["file_read"])

    def test_no_shell_tool(self):
        from desktop_forge.clive.tools import BY_NAME
        self.assertNotIn("shell", BY_NAME)
        self.assertNotIn("exec", BY_NAME)

    def test_every_desktop_id_stays_inside_the_approved_app_scope(self):
        approved = plan(["app_focus"])
        approved["permissions"]["apps"] = ["org.mozilla.thunderbird.desktop"]
        check_scope("app_focus", {"desktop_id": "org.mozilla.thunderbird.desktop"}, approved)
        with self.assertRaises(ScopeChanged):
            check_scope("app_focus", {"desktop_id": "libreoffice-writer.desktop"}, approved)

    def test_authentication_surfaces_are_not_controllable_apps(self):
        from desktop_forge.clive.tools import controllable_app
        with self.assertRaisesRegex(ValueError, "Authentication"):
            controllable_app("org.gnome.Shell.desktop")

    def test_catalog_filter_and_runtime_policy_share_privileged_exclusions(self):
        for desktop_id in ("org.gnome.Shell.desktop", "org.example.Polkit.desktop",
                           "screen-lock.desktop"):
            self.assertTrue(blocked_surface(desktop_id), desktop_id)
        self.assertFalse(blocked_surface("libreoffice-calc.desktop"))

    def test_paths_resolve_symlinks_before_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "visible").symlink_to("/etc", target_is_directory=True)
            with patch("pathlib.Path.home", return_value=root):
                for path in (root / "visible/passwd", root / ".ssh/id_rsa"):
                    with self.assertRaises(ValueError):
                        local_path(str(path))
                with self.assertRaises(ScopeChanged):
                    check_scope("file_read", {"path": str(root / "other")}, plan(["file_read"], [str(root / "allowed")]))

    def test_web_rejects_private_and_non_http_destinations(self):
        for url in ("file:///etc/passwd", "http://localhost", "http://127.0.0.1", "http://[::1]", "http://user:pass@example.com"):
            with self.assertRaises(ValueError):
                public_url(url)
        self.assertEqual(public_url("https://example.com"), "https://example.com")

    def test_tool_journal_reuses_only_completed_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = History(Path(tmp))
            self.addCleanup(history.close)
            self.assertIsNone(history.begin_call("a", "task", "file_write", {}))
            with self.assertRaises(RuntimeError):
                history.begin_call("a", "task", "file_write", {})
            history.finish_call("a", {"created": "note"})
            self.assertEqual(history.begin_call("a", "task", "file_write", {}), {"created": "note"})

    def test_a_model_switch_takes_effect_on_the_next_request(self):
        calls, notices = [], []
        live = {"cloud_enabled": True, "free_account_confirmed": True}
        class Transport(Models):
            def _chat(self, messages, tools, schema):
                calls.append(self.local)
                return {"content": "ok"}
        model = Transport(dict(live), threading.Event(), lambda **k: notices.append(k.get("notice")))
        model.follow(lambda: dict(live))
        model.chat([])
        live["cloud_enabled"] = False
        model.chat([])
        live["cloud_enabled"] = True
        model.chat([])
        self.assertEqual(calls, [False, True, False])
        self.assertIn("Switched to the local model.", notices)

    def test_fallback_can_be_turned_off(self):
        class Transport(Models):
            def _chat(self, messages, tools, schema):
                raise ModelUnavailable("Free allowance exhausted")
        model = Transport({"cloud_enabled": True, "free_account_confirmed": True,
                           "fallback_to_local": False}, threading.Event())
        with self.assertRaises(ModelUnavailable):
            model.chat([])
        self.assertFalse(model.local, "a refused fallback must not quietly switch to local")

    def test_a_cloud_failure_keeps_the_task_local_even_if_cloud_is_still_selected(self):
        calls = []
        class Transport(Models):
            def _chat(self, messages, tools, schema):
                calls.append(self.local)
                if not self.local:
                    raise ModelUnavailable("Free allowance exhausted")
                return {"content": "ok"}
        live = {"cloud_enabled": True, "free_account_confirmed": True}
        model = Transport(dict(live), threading.Event())
        model.follow(lambda: dict(live))
        model.chat([])
        model.chat([])
        self.assertEqual(calls, [False, True, True])

    def test_cloud_failure_retries_only_inference_with_existing_tool_results(self):
        calls = []
        class Transport(Models):
            def _chat(self, messages, tools, schema):
                calls.append((self.local, messages))
                if not self.local:
                    raise ModelUnavailable("Free allowance exhausted")
                return {"content": "done"}
        model = Transport({"cloud_enabled": True, "free_account_confirmed": True}, threading.Event())
        messages = [{"role": "tool", "content": '{"created":"note"}'}]
        self.assertEqual(model.chat(messages)["content"], "done")
        self.assertEqual(calls, [(False, messages), (True, messages)])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_AGENT, "Install optional CLIVE dependencies to run agent integration tests")
class StagedAttachmentTests(AgentTests):
    def recording(self, agent, seen):
        original = agent.model_factory

        class Recording(original):
            def chat(self, messages, tools=None, schema=None):
                seen.append(messages)
                return original.chat(self, messages, tools, schema)
        agent.model_factory = Recording

    def test_staged_files_are_checked_when_picked_and_can_be_sent_alone(self):
        good = self.root / "a.md"
        good.write_text("alpha")
        self.settings()
        agent = self.agent([{"content": json.dumps({"answer": "It says alpha."})}])
        seen = []
        self.recording(agent, seen)
        result = agent.draft_add([str(good), "/etc/hostname", str(self.root / "missing.md")])
        self.assertEqual([item["name"] for item in result["draft"]], ["a.md"])
        self.assertEqual(len(result["errors"]), 2)
        self.assertTrue(any("hostname" in e for e in result["errors"]))
        agent.submit("", use_draft=True)
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(agent.draft_public(), [], "a sent file stayed staged")
        sent = seen[0]
        self.assertIn("alpha", json.dumps(sent))
        # The question comes after the file, where the model reads it last.
        self.assertTrue(sent[-1]["content"].startswith("Please review the attached file"))
        self.assertEqual(state["messages"][0]["attachments"][0]["name"], "a.md")

    def test_a_follow_up_in_the_same_chat_still_has_the_file_and_checkpoints_do_not(self):
        attached = self.root / "facts.md"
        attached.write_text("the code is 7731")
        self.settings()
        agent = self.agent([{"content": json.dumps({"answer": "Noted."})},
                            {"content": json.dumps({"answer": "It was 7731."})}])
        seen = []
        self.recording(agent, seen)
        first = agent.submit("Remember this", attachments=[str(attached)])
        self.wait(agent)
        agent.submit("What was the code?", conversation=first["conversation"])
        self.wait(agent)
        self.assertIn("the code is 7731", json.dumps(seen[-1]))
        self.assertIn("attached earlier", json.dumps(seen[-1]))
        checkpoints = (self.root / "data" / "checkpoints.sqlite").read_bytes()
        self.assertNotIn(b"the code is 7731", checkpoints, "file text was written into checkpoints")
