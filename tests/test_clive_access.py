"""App Access: switched-off apps are unavailable to CLIVE, enforced in the tool layer."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_forge.clive.integrations import (AccessDisabled, AccessStore, Guards, Integration,
                                              build_registry)
from desktop_forge.clive.integrations import mail as mail_integration
from desktop_forge.clive.integrations.base import Capability
from desktop_forge.clive.policy import plan_schema
from desktop_forge.clive.settings import DEFAULTS

try:
    import langgraph.graph  # noqa: F401
    from desktop_forge.clive import agent as agent_module
    from desktop_forge.clive.agent import REDACTED, Agent
    HAS_AGENT = True
except ImportError:
    HAS_AGENT = False

NAUTILUS = {"desktop_id": "org.gnome.Nautilus.desktop", "name": "Files",
            "categories": "GNOME;GTK;Utility;Core;FileManager;"}
CALC = {"desktop_id": "libreoffice-calc.desktop", "name": "LibreOffice Calc",
        "categories": "Office;Spreadsheet;"}
SIGNAL = {"desktop_id": "org.signal.Signal.desktop", "name": "Signal",
          "categories": "Network;InstantMessaging;Chat;"}


def plan(tools, folders=()):
    return {"summary": "A test task", "answer": "", "steps": ["Do it"],
            "permissions": {"tools": list(tools), "folders": list(folders), "apps": [], "web": False}}


def tool(name, **arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


class FakeDesktop:
    def __init__(self, _cancel):
        pass

    def close(self):
        pass


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(patch("pathlib.Path.home", return_value=self.root))
        self.enterContext(patch.object(mail_integration, "ACCOUNTS_PATH", self.root / "mail.json"))
        self.apps = [NAUTILUS, CALC]
        self.store = AccessStore(self.root / "access.json",
                                 known_apps=lambda: [a["desktop_id"] for a in self.apps])
        self.registry = build_registry(self.store, lambda: self.apps)

    def test_defaults_keep_todays_access(self):
        names = self.registry.enabled_tool_names()
        for name in ("file_read", "file_write", "file_trash", "todos_list", "web_search",
                     "app_launch", "desktop_click"):
            self.assertIn(name, names)
        # New capabilities of existing apps start off.
        self.assertNotIn("file_edit", names)
        self.assertNotIn("todo_delete", names)

    def test_a_disabled_app_is_hidden_from_the_plan_and_refused(self):
        self.registry.set("files", enabled=False)
        names = self.registry.enabled_tool_names()
        self.assertFalse([n for n in names if n.startswith("file_")])
        enum = plan_schema(names)["properties"]["permissions"]["properties"]["tools"]["items"]["enum"]
        self.assertNotIn("file_read", enum)
        self.assertIn("Turned off by the user: Files", self.registry.planner_catalog())
        with self.assertRaisesRegex(AccessDisabled, "Files access is turned off"):
            self.registry.check("file_read", {"path": str(self.root / "a.txt")})

    def test_the_app_switch_overrides_its_permissions_without_erasing_them(self):
        self.registry.set("files", capabilities={"modify": True})
        self.assertTrue(self.registry.allows("files", "modify"))
        self.registry.set("files", enabled=False)
        self.assertFalse(self.registry.allows("files", "modify"))
        self.assertFalse(self.registry.allows("files", "read"))
        self.registry.set("files", enabled=True)
        self.assertTrue(self.registry.allows("files", "modify"), "turning an app back on lost its permissions")

    def test_a_capability_can_be_switched_off_on_its_own(self):
        self.registry.set("files", capabilities={"delete": False})
        self.assertIn("file_read", self.registry.enabled_tool_names())
        self.assertNotIn("file_trash", self.registry.enabled_tool_names())
        with self.assertRaisesRegex(AccessDisabled, "Move files to Trash"):
            self.registry.check("file_trash", {"path": str(self.root / "a.txt")})

    def test_each_installed_app_has_its_own_switch(self):
        self.registry.set("app:libreoffice-calc.desktop", enabled=False)
        with self.assertRaises(AccessDisabled):
            self.registry.check("app_focus", {"desktop_id": "libreoffice-calc.desktop"})
        self.registry.check("app_focus", {"desktop_id": "org.gnome.Nautilus.desktop"})
        with self.assertRaisesRegex(AccessDisabled, "not available"):
            self.registry.check("app_focus", {"desktop_id": "org.unknown.App.desktop"})

    def test_an_app_installed_later_starts_off(self):
        self.registry.enabled_tool_names()  # the first read takes the snapshot
        self.apps = [NAUTILUS, CALC, SIGNAL]
        self.registry._apps_at = 0  # let the next read see the new install
        signal = self.registry.get("app:org.signal.Signal.desktop")
        self.assertEqual(signal.category, "communication")
        entry = self.registry.entry(signal)
        self.assertFalse(entry["enabled"])
        self.assertTrue(entry["new"])
        self.assertTrue(self.registry.entry(self.registry.get("app:libreoffice-calc.desktop"))["enabled"])

    def test_turning_files_off_also_closes_the_files_app(self):
        self.registry.set("files", enabled=False)
        with self.assertRaisesRegex(AccessDisabled, "can show Files"):
            self.registry.check("desktop_inspect", {"app": "org.gnome.Nautilus.desktop"})
        self.registry.check("desktop_inspect", {"app": "libreoffice-calc.desktop"})

    def test_side_doors_into_a_switched_off_integration_are_closed(self):
        notes = self.root / "Notes"
        owner = Integration("notes", "Notes", "productivity", "", "", (Capability("read", "Read", "read"),),
                            default_enabled=False,
                            guards=Guards(paths=lambda: [str(notes)], hosts=("notes.example.com",),
                                          title_markers=("My Notes",)))
        self.registry._fixed["notes"] = owner
        with self.assertRaisesRegex(AccessDisabled, "belongs to Notes"):
            self.registry.check("file_read", {"path": str(notes / "diary.md")})
        self.registry.check("file_read", {"path": str(self.root / "elsewhere.txt")})
        with self.assertRaisesRegex(AccessDisabled, "part of Notes"):
            self.registry.check("open_url", {"url": "https://app.notes.example.com/page"})
        with self.assertRaisesRegex(AccessDisabled, "showing Notes"):
            self.registry.check("desktop_screenshot", {"app": "libreoffice-calc.desktop"},
                                window_titles=lambda _id: ["My Notes — LibreOffice Calc"])
        self.registry.set("notes", enabled=True)
        self.registry.check("file_read", {"path": str(notes / "diary.md")})

    def test_pausing_turns_everything_off_at_once(self):
        self.registry.pause(True)
        self.assertEqual(self.registry.enabled_tool_names(), [])
        with self.assertRaisesRegex(AccessDisabled, "paused"):
            self.registry.check("todos_list", {})
        self.assertIn("paused", self.registry.planner_catalog())
        self.registry.pause(False)
        self.registry.check("todos_list", {})

    def test_a_change_on_disk_applies_to_the_next_check(self):
        self.registry.check("todos_list", {})
        data = json.loads((self.root / "access.json").read_text())
        data["apps"]["tasks"] = {"enabled": False}
        (self.root / "access.json").write_text(json.dumps(data) + "\n")
        with self.assertRaises(AccessDisabled):
            self.registry.check("todos_list", {})

    def test_a_damaged_file_fails_closed(self):
        (self.root / "access.json").write_text("{not json")
        fresh = build_registry(AccessStore(self.root / "access.json"), lambda: self.apps)
        self.assertTrue(fresh.paused())
        with self.assertRaises(AccessDisabled):
            fresh.check("todos_list", {})

    def test_high_risk_actions_ask_unless_told_not_to(self):
        path = {"path": str(self.root / "a.txt")}
        self.assertTrue(self.registry.needs_confirmation("file_trash", path))
        self.assertFalse(self.registry.needs_confirmation("file_write", {**path, "text": ""}))
        self.registry.set("files", confirm={"delete": False})
        self.assertFalse(self.registry.needs_confirmation("file_trash", path))

    def test_unknown_permissions_are_refused(self):
        with self.assertRaises(ValueError):
            self.registry.set("files", capabilities={"launch_missiles": True})
        with self.assertRaises(ValueError):
            self.registry.set("nothing-here", enabled=True)

    def test_enable_all_and_disable_all_can_target_a_filtered_set(self):
        self.registry.set_many(False, ["files", "web"])
        self.assertFalse(self.registry.enabled("files"))
        self.assertTrue(self.registry.enabled("tasks"))
        self.registry.set_many(False)
        self.assertEqual(self.registry.summary()["enabled"], 0)
        self.registry.set_many(True)
        self.assertEqual(self.registry.summary()["enabled"], self.registry.summary()["total"])

    def test_parallel_switches_are_never_lost(self):
        ids = ["files", "tasks", "web", "app:org.gnome.Nautilus.desktop", "app:libreoffice-calc.desktop"]
        barrier = threading.Barrier(len(ids))

        def flip(integration_id):
            barrier.wait()
            self.registry.set(integration_id, enabled=False)

        threads = [threading.Thread(target=flip, args=(i,)) for i in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([i for i in ids if self.registry.enabled(i)], [])


@unittest.skipUnless(HAS_AGENT, "Install optional CLIVE dependencies to run agent integration tests")
class AgentAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.enterContext(patch("pathlib.Path.home", return_value=self.root))
        self.enterContext(patch.object(mail_integration, "ACCOUNTS_PATH", self.root / "mail.json"))
        self.addCleanup(self.tmp.cleanup)
        self.agents = []
        self.seen = []
        self.settings(approval_mode="never")

    def tearDown(self):
        for agent in self.agents:
            agent.close()
            if agent.checkpoint_connection:
                agent.checkpoint_connection.close()
            agent.history.close()

    def settings(self, **changes):
        values = {**DEFAULTS, "context_files": [], **changes}
        self.enterContext(patch.object(agent_module, "load_settings", lambda: dict(values)))

    def agent(self, responses, hook=None):
        seen = self.seen

        class FakeModels:
            def __init__(self, settings, cancel, report):
                self.cancel = cancel

            def chat(self, messages, tools=None, schema=None):
                seen.append({"messages": messages, "tools": [t["function"]["name"] for t in tools or []]})
                if hook:
                    hook(len(seen))
                return responses.pop(0)

            def stop(self):
                self.cancel.set()

        agent = Agent(self.root / "data", model_factory=FakeModels, desktop_factory=FakeDesktop,
                      apps_factory=lambda: [NAUTILUS])
        self.agents.append(agent)
        return agent

    def wait(self, agent):
        agent.worker.join(10)
        self.assertFalse(agent.worker.is_alive(), "Agent did not finish its turn")
        return agent.snapshot()

    def test_an_app_switched_off_mid_task_is_refused_not_widened(self):
        note = self.root / "note.txt"
        note.write_text("PRIVATE-7731")
        responses = [
            {"content": json.dumps(plan(["file_read"], [str(self.root)]))},
            tool("file_read", path=str(note)),
            {"role": "assistant", "content": "Files access is off."},
        ]
        holder = {}

        def hook(count):
            if count == 2:  # the user flips Files off while CLIVE is thinking
                holder["agent"].registry.set("files", enabled=False)

        agent = self.agent(responses, hook)
        holder["agent"] = agent
        agent.submit("Read note.txt")
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertEqual(state["version"], 1, "a refused call widened the approved task")
        action = state["actions"][-1]
        self.assertEqual((action["tool"], action["status"]), ("file_read", "blocked"))
        refusal = json.loads(self.seen[-1]["messages"][-1]["content"])
        self.assertTrue(refusal["access_disabled"])
        self.assertNotIn("PRIVATE-7731", json.dumps(self.seen[-1]["messages"]))
        self.assertIn("Files access is turned off", state["notice"])

    def test_a_disabled_capability_outside_the_scope_is_not_silently_granted(self):
        # In unattended mode a scope change is re-approved without anyone
        # seeing it. A switched-off capability must never take that route.
        note = self.root / "note.txt"
        note.write_text("keep me")
        agent = self.agent([
            {"content": json.dumps(plan(["file_read"], [str(self.root)]))},
            tool("file_trash", path=str(note)),
            {"role": "assistant", "content": "I cannot delete files."},
        ])
        agent.registry.set("files", capabilities={"delete": False})
        agent.submit("Read note.txt")
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertTrue(note.exists())
        self.assertEqual(state["version"], 1)
        self.assertNotIn("file_trash", state["plan"]["permissions"]["tools"])

    def test_the_model_is_only_offered_switched_on_tools(self):
        agent = self.agent([
            {"content": json.dumps(plan(["todos_list", "file_read"], [str(self.root)]))},
            {"role": "assistant", "content": "Done."},
        ])
        agent.registry.set("files", enabled=False)
        agent.submit("List my tasks")
        self.wait(agent)
        planner = self.seen[0]["messages"][0]["content"]
        self.assertIn("Turned off by the user: Files", planner)
        state = agent.snapshot()
        # file_read is not a valid plan tool while Files is off: the plan is
        # rejected rather than quietly granted.
        self.assertEqual(state["status"], "paused", state)
        self.assertIn("usable task plan", state["notice"])
        self.assertFalse([name for name in self.seen[0]["tools"] if name.startswith("file_")])

    def test_results_from_an_app_switched_off_later_are_removed_from_context(self):
        note = self.root / "note.txt"
        note.write_text("private text")
        holder = {}

        def hook(count):
            if count == 3:
                holder["agent"].registry.set("files", enabled=False)

        agent = self.agent([
            {"content": json.dumps(plan(["file_read", "todos_list"], [str(self.root)]))},
            tool("file_read", path=str(note)),
            tool("todos_list"),
            {"role": "assistant", "content": "Done."},
        ], hook)
        holder["agent"] = agent
        agent.submit("Read note.txt then list tasks")
        self.wait(agent)
        final = json.dumps(self.seen[-1]["messages"])
        self.assertNotIn("private text", final)
        self.assertIn(REDACTED.format(name="Files"), final)
        self.assertNotIn('"integration"', final, "bookkeeping fields reached the model")

    def test_high_risk_actions_wait_even_when_everything_is_auto_approved(self):
        note = self.root / "note.txt"
        note.write_text("draft")
        agent = self.agent([
            {"content": json.dumps(plan(["file_trash"], [str(self.root)]))},
            tool("file_trash", path=str(note)),
            {"role": "assistant", "content": "Moved to Trash."},
        ])
        with patch("desktop_forge.clive.integrations.files._gio") as gio:
            gio.return_value.File.new_for_path.return_value.trash.side_effect = \
                lambda _c: note.unlink() or True
            agent.submit("Trash note.txt")
            state = self.wait(agent)
            self.assertEqual(state["status"], "awaiting_confirmation", state)
            self.assertTrue(agent.busy(), "a second task could start mid-confirmation")
            self.assertTrue(note.exists())
            card = state["confirmation"]["calls"][0]
            self.assertEqual((card["tool"], card["app_name"], card["target"]),
                             ("file_trash", "Files", str(note)))
            with self.assertRaises(ValueError):
                agent.confirm(state["id"], ["someone-else"])
            agent.confirm(state["id"], [card["id"]])
            state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertFalse(note.exists())

    def test_a_declined_action_never_runs(self):
        note = self.root / "note.txt"
        note.write_text("draft")
        agent = self.agent([
            {"content": json.dumps(plan(["file_trash"], [str(self.root)]))},
            tool("file_trash", path=str(note)),
            {"role": "assistant", "content": "Left it alone."},
        ])
        agent.submit("Trash note.txt")
        state = self.wait(agent)
        agent.confirm(state["id"], [])
        state = self.wait(agent)
        self.assertEqual(state["status"], "complete", state)
        self.assertTrue(note.exists())
        self.assertEqual(state["actions"][-1]["status"], "declined")

    def test_a_widened_scope_still_passes_the_confirmation(self):
        # approve -> tools used to skip every later stop.
        note = self.root / "note.txt"
        note.write_text("draft")
        agent = self.agent([
            {"content": json.dumps(plan(["file_read"], [str(self.root)]))},
            tool("file_trash", path=str(note)),
            {"role": "assistant", "content": "Done."},
        ])
        agent.submit("Tidy up")
        state = self.wait(agent)
        self.assertEqual(state["status"], "awaiting_confirmation", state)
        self.assertEqual(state["version"], 2)
        self.assertTrue(note.exists())

    def test_the_chat_page_counts_the_same_statuses_as_active(self):
        import ast
        from desktop_forge.clive.agent import ACTIVE
        source = Path(__file__).parents[1].joinpath("desktop_forge/pages/clive.py").read_text()
        page = next(ast.literal_eval(node.value) for node in ast.parse(source).body
                    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACTIVE")
        self.assertEqual(page, ACTIVE)

    def test_a_restart_pauses_a_task_waiting_for_confirmation(self):
        note = self.root / "note.txt"
        note.write_text("draft")
        agent = self.agent([
            {"content": json.dumps(plan(["file_trash"], [str(self.root)]))},
            tool("file_trash", path=str(note)),
        ])
        agent.submit("Trash note.txt")
        self.assertEqual(self.wait(agent)["status"], "awaiting_confirmation")
        restarted = Agent(self.root / "data", model_factory=None, desktop_factory=FakeDesktop,
                          apps_factory=lambda: [NAUTILUS])
        self.agents.append(restarted)
        self.assertEqual(restarted.snapshot()["status"], "paused")
        self.assertIsNone(restarted.snapshot()["confirmation"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_AGENT, "Install optional CLIVE dependencies to run agent integration tests")
class ServiceAccessTests(unittest.TestCase):
    def setUp(self):
        from desktop_forge.clive import service
        self.service_module = service
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(patch("pathlib.Path.home", return_value=self.root))
        self.enterContext(patch.object(mail_integration, "ACCOUNTS_PATH", self.root / "mail.json"))
        self.svc = service.Service.__new__(service.Service)
        self.svc.agent = Agent(self.root / "data", desktop_factory=FakeDesktop,
                               apps_factory=lambda: [NAUTILUS], key_state=lambda: self.svc.has_key)
        self.addCleanup(self.svc.agent.history.close)
        self.published = []
        self.svc.publish = self.published.append
        self.svc.has_key = False
        self.svc.pull = None
        self.svc.followups = None

    def call(self, **request):
        return self.svc._dispatch(request)

    def test_the_access_page_lists_every_app_with_its_permissions(self):
        listing = self.call(op="access")
        rows = {row["id"]: row for row in listing["apps"]}
        self.assertTrue({"files", "tasks", "web", "app:org.gnome.Nautilus.desktop"} <= set(rows))
        files = rows["files"]
        self.assertTrue(files["enabled"])
        self.assertEqual(files["access"], "read_write")
        delete = next(c for c in files["capabilities"] if c["id"] == "delete")
        self.assertEqual((delete["risk"], delete["confirm"]), ("high", True))
        self.assertEqual(rows["web"]["status"]["state"], "needs_setup")

    def test_switches_apply_and_are_published(self):
        listing = self.call(op="access_set", id="files", enabled=False)
        self.assertFalse(next(r for r in listing["apps"] if r["id"] == "files")["enabled"])
        self.assertTrue(self.published, "a switch must reach the card and chat at once")
        self.assertIn("Files", self.svc.decorate(self.published[-1])["access"]["off"])
        with self.assertRaises(ValueError):
            self.call(op="access_set", id="files", capabilities={"delete": "yes"})

    def test_pause_stops_the_running_task(self):
        self.svc.agent.state = {**self.svc.agent.state, "status": "running", "id": "t"}
        self.call(op="access_pause", paused=True)
        self.assertEqual(self.svc.agent.snapshot()["status"], "cancelled")
        self.assertTrue(self.svc.agent.registry.paused())

    def test_large_results_are_trimmed_before_they_reach_shell(self):
        state = {"status": "complete", "actions": [
            {"tool": "file_read", "result": {"text": "x" * 50000}},
            {"tool": "todos_list", "result": {"items": []}}]}
        public = self.service_module.public_state(state)
        self.assertIsNone(public["actions"][0]["result"])
        self.assertTrue(public["actions"][0]["truncated"])
        self.assertLessEqual(len(public["actions"][0]["preview"]), 2000)
        self.assertEqual(public["actions"][1]["result"], {"items": []})
        self.assertEqual(len(state["actions"][0]["result"]["text"]), 50000, "trimming changed the journal")


class PrivacyTests(unittest.TestCase):
    def outgoing(self, local, **settings):
        from desktop_forge.clive.models import Models
        model = Models({**DEFAULTS, "cloud_enabled": not local, "free_account_confirmed": True,
                        **settings}, threading.Event())
        return model._outgoing([
            {"role": "user", "content": "files", "attachment": True},
            {"role": "user", "content": "look", "images": ["abc"]},
            {"role": "tool", "tool_name": "file_read", "content": "{}", "integration": "files"},
        ])

    def test_cloud_models_see_images_and_files_unless_turned_off(self):
        sent = self.outgoing(local=False)
        self.assertEqual(sent[1]["images"], ["abc"])
        self.assertEqual(sent[0]["content"], "files")
        self.assertNotIn("integration", sent[2], "bookkeeping fields reached Ollama")
        self.assertNotIn("attachment", sent[0])

    def test_withheld_data_is_replaced_by_a_note_for_cloud_models_only(self):
        from desktop_forge.clive.models import WITHHELD_FILES, WITHHELD_IMAGES
        sent = self.outgoing(local=False, cloud_images=False, cloud_attachments=False)
        self.assertEqual(sent[0]["content"], WITHHELD_FILES)
        self.assertNotIn("images", sent[1])
        self.assertIn(WITHHELD_IMAGES, sent[1]["content"])
        local = self.outgoing(local=True, cloud_images=False, cloud_attachments=False)
        self.assertEqual(local[1]["images"], ["abc"], "a local model was denied data it may see")


@unittest.skipUnless(HAS_AGENT, "Install optional CLIVE dependencies to run agent integration tests")
class RetentionTests(unittest.TestCase):
    def test_old_conversations_are_pruned_and_recent_ones_kept(self):
        import time as clock
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(Path(tmp) / "data", desktop_factory=FakeDesktop, apps_factory=lambda: [])
            self.addCleanup(agent.history.close)
            old = agent.history.conversation("old chat")
            recent = agent.history.conversation("recent chat")
            with agent.history.lock, agent.history.db:
                agent.history.db.execute("UPDATE conversations SET updated=? WHERE id=?",
                                         (clock.time() - 40 * 86400, old))
            self.assertEqual(agent.prune(0), 0, "0 must keep everything")
            self.assertEqual(agent.prune(30), 1)
            remaining = {c["id"] for c in agent.history.list_conversations()}
            self.assertEqual(remaining, {recent})


@unittest.skipUnless(HAS_AGENT, "Install optional CLIVE dependencies to run agent integration tests")
class FollowupCheckTests(unittest.TestCase):
    def test_the_background_check_goes_through_app_access(self):
        from desktop_forge.clive import service
        from desktop_forge.clive.integrations import mail
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.enterContext(patch("pathlib.Path.home", return_value=root))
            self.enterContext(patch.object(mail, "ACCOUNTS_PATH", root / "mail.json"))
            self.enterContext(patch.object(mail, "_store_password", lambda *_: None))
            first = mail.add_account({"address": "a@example.com", "imap_host": "i", "smtp_host": "s",
                                      "password": "p", "name": "Work"})
            second = mail.add_account({"address": "b@example.com", "imap_host": "i", "smtp_host": "s",
                                       "password": "p", "name": "Home"})
            svc = service.Service.__new__(service.Service)
            svc.agent = Agent(root / "data", desktop_factory=FakeDesktop, apps_factory=lambda: [])
            self.addCleanup(svc.agent.history.close)
            svc.publish = lambda state: None
            svc.followups, svc.followup_running = None, True
            registry = svc.agent.registry
            registry.set(mail.PREFIX + first["id"], enabled=True)
            registry.set(mail.PREFIX + second["id"], enabled=True, capabilities={"followup": False})
            asked = []

            def followups(_ctx, arguments):
                asked.append(arguments["account"])
                return {"waiting_on_you": [{}, {}]}
            import dataclasses
            from desktop_forge.clive.tools import TOOLS
            self.enterContext(patch.dict(TOOLS, {"mail_followups": dataclasses.replace(
                TOOLS["mail_followups"], handler=followups)}))
            svc._check_followups(7)
        self.assertEqual(asked, [mail.PREFIX + first["id"]], "an account with tracking off was read")
        self.assertEqual(svc.followups["count"], 2)
        self.assertFalse(svc.followup_running)
