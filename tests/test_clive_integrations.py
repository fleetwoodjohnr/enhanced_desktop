"""Notes, Terminal, Git and Media integrations, against temporary folders and fakes."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_forge.clive import settings as clive_settings
from desktop_forge.clive.integrations import AccessDisabled, AccessStore, build_registry
from desktop_forge.clive.integrations import mail, media, notes, terminal
from desktop_forge.clive.integrations import git as git_integration


class Context:
    def __init__(self, registry):
        self.registry = registry


class IntegrationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        self.enterContext(patch("pathlib.Path.home", return_value=self.home))
        self.enterContext(patch.object(clive_settings, "SETTINGS_PATH", self.home / "clive.json"))
        self.enterContext(patch.object(mail, "ACCOUNTS_PATH", self.home / "mail.json"))
        self.registry = build_registry(AccessStore(self.home / "access.json"), lambda: [])
        self.ctx = Context(self.registry)

    def call(self, _tool, /, **arguments):
        tool, _integration, _capability = self.registry.check(_tool, arguments)
        return tool.handler(self.ctx, arguments)


class NotesTests(IntegrationCase):
    def test_notes_start_off_and_their_folder_is_closed_to_files(self):
        self.assertFalse(self.registry.enabled("notes"))
        (self.home / "Notes").mkdir()
        with self.assertRaisesRegex(AccessDisabled, "belongs to Notes"):
            self.registry.check("file_read", {"path": str(self.home / "Notes" / "diary.md")})

    def test_create_search_read_update_and_stay_inside_the_folder(self):
        self.registry.set("notes", enabled=True)
        created = self.call("note_create", title="Groceries: this week", text="milk\neggs")
        self.assertEqual(created, {"created": "Groceries this week.md"})
        self.assertEqual(self.call("note_search", query="EGGS")["results"],
                         [{"name": "Groceries this week.md", "matches": ["eggs"]}])
        self.call("note_update", name="Groceries this week.md", text="bread")
        text = self.call("note_read", name="Groceries this week")["text"]
        self.assertEqual(text, "# Groceries: this week\n\nmilk\neggs\nbread\n")
        self.assertEqual([n["name"] for n in self.call("note_list")["notes"]], ["Groceries this week.md"])
        for name in ("../escape.md", "/etc/passwd", ".hidden.md", "a/../../b.md"):
            with self.assertRaises(ValueError, msg=name):
                self.call("note_read", name=name)
        with self.assertRaises(FileExistsError):
            self.call("note_create", title="Groceries this week", text="again")

    def test_the_folder_is_a_setting_and_must_stay_in_the_home_folder(self):
        vault = self.home / "Vault"
        self.registry.set_option("notes", "notes_folder", str(vault))
        self.assertEqual(notes.folder(), vault)
        with self.assertRaises(ValueError):
            self.registry.set_option("notes", "notes_folder", "/etc")
        with self.assertRaises(ValueError):
            self.registry.set_option("notes", "notes_folder", str(self.home / ".secret"))

    def test_deleting_a_note_asks_first(self):
        self.registry.set("notes", enabled=True)
        self.assertTrue(self.registry.needs_confirmation("note_delete", {"name": "a.md"}))


class TerminalTests(IntegrationCase):
    def test_there_is_no_shell_until_terminal_is_switched_on(self):
        self.assertNotIn("terminal_run", self.registry.enabled_tool_names())
        with self.assertRaisesRegex(AccessDisabled, "Terminal access is turned off"):
            self.registry.check("terminal_run", {"command": "ls"})
        self.registry.set("terminal", enabled=True)
        self.assertIn("terminal_run", self.registry.enabled_tool_names())

    def test_commands_are_classified_by_what_they_would_do(self):
        classify = terminal.classify
        self.assertEqual(classify("ls -la ~/Documents"), "run")
        self.assertEqual(classify("sudo dnf upgrade"), "admin")
        self.assertEqual(classify("cd x && pkexec rm -rf /opt/y"), "admin")
        self.assertEqual(classify("flatpak install flathub org.gimp.GIMP"), "install")
        self.assertEqual(classify("python3 -m pip install --user requests"), "install")
        self.assertEqual(classify("FOO=1 pip3 uninstall numpy"), "install")
        self.assertEqual(classify("dnf search gimp"), "run")

    def test_install_and_admin_need_their_own_switches_and_always_ask(self):
        self.registry.set("terminal", enabled=True)
        with self.assertRaisesRegex(AccessDisabled, "Install and remove software"):
            self.registry.check("terminal_run", {"command": "flatpak install x"})
        self.registry.set("terminal", capabilities={"install": True, "admin": True})
        self.assertTrue(self.registry.needs_confirmation("terminal_run", {"command": "pip install x"}))
        self.assertTrue(self.registry.needs_confirmation("terminal_run", {"command": "sudo reboot"}))
        self.assertFalse(self.registry.needs_confirmation("terminal_run", {"command": "ls"}))
        with self.assertRaisesRegex(ValueError, "always asks"):
            self.registry.set("terminal", confirm={"admin": False})

    def test_the_sandbox_isolates_what_the_switches_do_not_allow(self):
        home = str(self.home)
        (self.home / ".ssh").mkdir()
        args = terminal.sandbox_arguments("id", home, home=home, runtime="/run/user/1000", write=False,
                                          network=False, hide_home=False,
                                          hidden=[str(self.home / ".ssh"), str(self.home / "missing")])
        joined = " ".join(args)
        for flag in ("--unshare-pid", "--proc /proc", "--dev /dev", "--tmpfs /tmp",
                     "--tmpfs /run/user/1000", "--unshare-net", "--ro-bind / /", "--clearenv",
                     f"--tmpfs {home}/.ssh"):
            self.assertIn(flag, joined)
        self.assertNotIn(f"--bind {home} {home}", joined, "home was writable with Change files off")
        self.assertNotIn("missing", joined)
        writable = " ".join(terminal.sandbox_arguments(
            "id", home, home=home, runtime="/run/r", write=True, network=True, hide_home=False,
            hidden=[str(self.home / ".ssh")]))
        self.assertNotIn("--unshare-net", writable)
        # The private overlays come after the writable home, so they win.
        self.assertLess(writable.index(f"--bind {home} {home}"), writable.index(f"--tmpfs {home}/.ssh"))
        hidden = " ".join(terminal.sandbox_arguments(
            "id", home, home=home, runtime="/run/r", write=True, network=False, hide_home=True))
        self.assertIn(f"--tmpfs {home}", hidden)
        self.assertNotIn(f"--bind {home}", hidden)

    def test_change_files_never_reaches_what_runs_at_the_next_login(self):
        home = str(self.home)
        (self.home / ".config" / "autostart").mkdir(parents=True)
        (self.home / ".bashrc").write_text("# shell\n")
        entries = terminal.home_dot_entries(home)
        self.assertIn(str(self.home / ".bashrc"), entries)
        args = " ".join(terminal.sandbox_arguments(
            "id", home, home=home, runtime="/run/r", write=True, network=False, hide_home=False,
            readonly=entries))
        bind = args.index(f"--bind {home} {home}")
        self.assertGreater(args.index(f"--ro-bind {home}/.bashrc {home}/.bashrc"), bind)
        self.assertGreater(args.index(f"--ro-bind {home}/.config {home}/.config"), bind)

    def test_switched_off_folders_and_files_access_shape_the_sandbox(self):
        self.registry.set("terminal", enabled=True)
        paths = terminal._hidden_paths(self.ctx)
        self.assertIn(str(self.home / "Notes"), paths, "a switched-off app's folder stayed visible")
        self.assertIn(str(self.home / ".config/desktop-forge"), paths)

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is not installed")
    def test_a_real_command_cannot_write_while_change_files_is_off(self):
        probe = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "true"],
                               capture_output=True, check=False)
        if probe.returncode != 0:
            self.skipTest("unprivileged user namespaces are unavailable here")
        self.registry.set("terminal", enabled=True)
        target = self.home / "written.txt"
        result = self.call("terminal_run", command=f"echo hi > {target}; echo done", cwd=str(self.home))
        self.assertFalse(target.exists(), "a sandboxed command wrote with Change files off")
        self.assertIn("done", result["output"])
        self.registry.set("terminal", capabilities={"modify": True})
        self.call("terminal_run", command=f"echo hi > {target}", cwd=str(self.home))
        self.assertEqual(target.read_text(), "hi\n")
        # Even with Change files on, startup files stay out of reach.
        (self.home / ".bashrc").write_text("# shell\n")
        self.call("terminal_run", command=f"echo evil >> {self.home}/.bashrc", cwd=str(self.home))
        self.assertEqual((self.home / ".bashrc").read_text(), "# shell\n",
                         "a sandboxed command changed a startup file")
        self.registry.set("terminal", capabilities={"read_output": False})
        hidden = self.call("terminal_run", command="echo secret-output", cwd=str(self.home))
        self.assertNotIn("output", hidden)
        self.assertEqual(hidden["exit_code"], 0)

    def test_admin_commands_open_in_a_terminal_for_the_user(self):
        self.registry.set("terminal", enabled=True, capabilities={"admin": True})
        with patch.object(terminal.shutil, "which", side_effect=lambda name: "/usr/bin/" + name
                          if name == "ptyxis" else None), \
                patch.object(terminal.subprocess, "Popen") as popen:
            result = self.call("terminal_run", command="sudo dnf upgrade")
        self.assertEqual(result["opened_in"], "ptyxis")
        self.assertIn("sudo dnf upgrade", popen.call_args.args[0][-1])


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class GitTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        self.repo = self.home / "project"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for key, value in (("user.email", "t@example.com"), ("user.name", "Test")):
            subprocess.run(["git", "-C", str(self.repo), "config", key, value], check=True)
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {self.home}/hook-ran\n")
        (hooks / "pre-commit").chmod(0o755)
        self.registry.set("git", enabled=True)

    def test_status_commit_and_log_without_running_hooks(self):
        (self.repo / "a.txt").write_text("one\n")
        self.assertIn("a.txt", self.call("git_status", repo=str(self.repo))["output"])
        self.call("git_commit", repo=str(self.repo), message="First commit")
        self.assertFalse((self.home / "hook-ran").exists(), "a repository hook ran")
        self.assertIn("First commit", self.call("git_log", repo=str(self.repo))["output"])

    def test_repository_configured_programs_do_not_run_on_reads(self):
        marker = self.home / "repo-code-ran"
        script = self.home / "evil.sh"
        script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
        script.chmod(0o755)
        (self.repo / "a.txt").write_text("one\n")
        self.call("git_commit", repo=str(self.repo), message="First")
        for key, value in (("core.fsmonitor", str(script)), ("diff.external", str(script)),
                           ("diff.evil.textconv", str(script)), ("filter.evil.clean", str(script))):
            subprocess.run(["git", "-C", str(self.repo), "config", key, value], check=True)
        (self.repo / ".gitattributes").write_text("*.txt diff=evil filter=evil\n")
        (self.repo / "a.txt").write_text("two\n")
        self.call("git_status", repo=str(self.repo))
        self.call("git_diff", repo=str(self.repo))
        self.call("git_show", repo=str(self.repo), revision="HEAD")
        self.assertFalse(marker.exists(), "a repository-configured program ran on a read")

    def test_options_cannot_be_smuggled_in_as_names(self):
        with self.assertRaises(ValueError):
            self.call("git_show", repo=str(self.repo), revision="--output=/tmp/x")
        with self.assertRaises(ValueError):
            self.call("git_branch", repo=str(self.repo), name="--force")
        with self.assertRaises(ValueError):
            self.call("git_status", repo="/etc")

    def test_pushing_asks_and_committing_does_not(self):
        self.assertTrue(self.registry.needs_confirmation("git_push", {"repo": str(self.repo)}))
        self.assertFalse(self.registry.needs_confirmation("git_commit", {"repo": str(self.repo), "message": "m"}))


class MediaTests(unittest.TestCase):
    PLAYERS = [
        {"bus_name": "org.mpris.MediaPlayer2.spotify", "desktop_entry": "com.spotify.Client", "identity": "Spotify"},
        {"bus_name": "org.mpris.MediaPlayer2.brave.instance2", "desktop_entry": "", "identity": "Brave"},
    ]

    def test_players_are_matched_to_their_apps(self):
        self.assertEqual(media.player_for("com.spotify.Client.desktop", self.PLAYERS)["identity"], "Spotify")
        self.assertEqual(media.player_for("com.brave.Browser.desktop", self.PLAYERS)["identity"], "Brave")
        self.assertIsNone(media.player_for("org.gnome.Nautilus.desktop", self.PLAYERS))

    def test_playback_is_a_capability_of_media_apps_only_and_starts_off(self):
        spotify = {"desktop_id": "com.spotify.Client.desktop", "name": "Spotify",
                   "categories": "Audio;Music;Player;AudioVideo;"}
        files = {"desktop_id": "org.gnome.Nautilus.desktop", "name": "Files", "categories": "Utility;"}
        with tempfile.TemporaryDirectory() as tmp:
            registry = build_registry(
                AccessStore(Path(tmp) / "access.json", known_apps=lambda: [spotify["desktop_id"],
                                                                           files["desktop_id"]]),
                lambda: [spotify, files])
            app = registry.get("app:com.spotify.Client.desktop")
            self.assertEqual(app.category, "media")
            self.assertIsNotNone(app.capability("playback"))
            self.assertIsNone(registry.get("app:org.gnome.Nautilus.desktop").capability("playback"))
            self.assertNotIn("media_control", registry.enabled_tool_names())
            with self.assertRaisesRegex(AccessDisabled, "Control playback"):
                registry.check("media_control", {"app": "com.spotify.Client.desktop", "action": "pause"})
            registry.set("app:com.spotify.Client.desktop", capabilities={"playback": True})
            registry.check("media_control", {"app": "com.spotify.Client.desktop", "action": "pause"})


if __name__ == "__main__":
    unittest.main()


class FakeCalendar:
    def __init__(self):
        self.events_by_id = {"eds:personal:abc:": {"id": "eds:personal:abc:", "summary": "Meeting with John",
                                                    "start": "2026-10-02T14:00:00+00:00", "writable": True}}
        self.created, self.modified, self.removed = [], [], []

    def calendars(self):
        return [{"id": "personal", "name": "Personal", "writable": True}]

    def events(self, start, end, query):
        return [dict(e) for e in self.events_by_id.values()
                if not query or query.casefold() in e["summary"].casefold()]

    def create(self, calendar_id, fields):
        self.created.append((calendar_id, fields))
        return "eds:personal:new:"

    def modify(self, event_id, fields):
        self.modified.append((event_id, fields))
        return event_id

    def remove(self, event_id):
        self.removed.append(event_id)

    def describe(self, event_id):
        return "Meeting with John — 20261002T140000Z"


class CalendarTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        from desktop_forge.clive.integrations import calendar
        self.calendar = calendar
        self.fake = FakeCalendar()
        self.enterContext(patch.object(calendar, "_backend", self.fake))
        self.enterContext(patch.object(calendar, "_thunderbird", lambda start, end: [
            {"id": "tb:0:Dentist", "summary": "Dentist", "start": "2026-10-03T09:00:00+00:00", "writable": False}]))

    def test_viewing_is_on_and_changing_is_off_until_allowed(self):
        found = self.call("calendar_events", query="john")["events"]
        self.assertEqual([e["summary"] for e in found], ["Meeting with John"])
        self.assertEqual(len(self.call("calendar_events")["events"]), 2, "Thunderbird events were missed")
        with self.assertRaisesRegex(AccessDisabled, "Move events to another time"):
            self.registry.check("calendar_update", {"event_id": "eds:personal:abc:",
                                                    "start": "2026-10-02T15:00"})

    def test_moving_a_meeting_needs_reschedule_and_editing_needs_modify(self):
        self.registry.set("calendar", capabilities={"reschedule": True})
        self.call("calendar_update", event_id="eds:personal:abc:", start="2026-10-02T15:00",
                  end="2026-10-02T16:00")
        self.assertEqual(self.fake.modified[0][1], {"start": "2026-10-02T15:00", "end": "2026-10-02T16:00"})
        with self.assertRaisesRegex(AccessDisabled, "Change events"):
            self.registry.check("calendar_update", {"event_id": "eds:personal:abc:", "summary": "Renamed"})

    def test_new_events_default_to_an_hour_and_cancelling_asks_with_the_title(self):
        self.registry.set("calendar", capabilities={"create": True, "cancel": True})
        self.call("calendar_create", summary="Lunch", start="2026-10-05T12:00:00+00:00")
        self.assertEqual(self.fake.created[0][1]["end"], "2026-10-05T13:00:00+00:00")
        self.assertTrue(self.registry.needs_confirmation("calendar_cancel", {"event_id": "eds:personal:abc:"}))
        preview = self.registry.tools["calendar_cancel"].confirmation_text({"event_id": "eds:personal:abc:"})
        self.assertIn("Meeting with John", preview)

    def test_turning_calendar_off_closes_the_calendar_app(self):
        registry = build_registry(AccessStore(self.home / "a2.json", known_apps=lambda: ["org.gnome.Calendar.desktop"]),
                                  lambda: [{"desktop_id": "org.gnome.Calendar.desktop", "name": "Calendar",
                                            "categories": "GNOME;GTK;Office;Calendar;Core;"}])
        registry.set("calendar", enabled=False)
        with self.assertRaisesRegex(AccessDisabled, "can show Calendar"):
            registry.check("desktop_inspect", {"app": "org.gnome.Calendar.desktop"})


RAW = (b"From: John Smith <john@example.com>\r\nTo: me@gmail.com\r\nSubject: Friday's meeting\r\n"
       b"Date: Mon, 28 Sep 2026 09:00:00 +0000\r\nMessage-ID: <m1@example.com>\r\n"
       b"Content-Type: text/plain; charset=utf-8\r\n\r\nCan we move Friday's meeting to 3 PM?\r\n")


class FakeIMAP:
    """Just enough of a Gmail IMAP server for the tools' round trips."""
    capabilities = ("IMAP4REV1", "X-GM-EXT-1", "MOVE")
    instances = []

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host, self.commands, self.appended = host, [], []
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.commands.append(("LOGIN", user, password))
        return "OK", [b"logged in"]

    def authenticate(self, mechanism, callback):
        self.commands.append(("AUTHENTICATE", mechanism, callback(b"")))
        return "OK", [b"ok"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"',
                      b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
                      b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Drafts"',
                      b'(\\Sent \\HasNoChildren) "/" "[Gmail]/Sent Mail"',
                      b'(\\Trash \\HasNoChildren) "/" "[Gmail]/Trash"']

    def select(self, mailbox, readonly=False):
        self.commands.append(("SELECT", mailbox, readonly))
        return "OK", [b"1"]

    def response(self, code):
        return code, [b"77"]

    def uid(self, command, *args):
        self.commands.append(("UID", command, *args))
        if command == "SEARCH":
            return "OK", [b"41 42"]
        if command == "FETCH":
            if "BODY.PEEK[]" in args[1]:
                return "OK", [(b"1 (UID 42 FLAGS () BODY[] {%d}" % len(RAW), RAW), b")"]
            header = RAW.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
            return "OK", [(b'1 (UID 42 FLAGS (\\Flagged) X-GM-LABELS ("\\\\Important" "\\\\Inbox") X-GM-THRID 9 '
                           b'BODY[HEADER.FIELDS (FROM)] {%d}' % len(header), header),
                          (b" BODY[TEXT]<0> {12}", b"Can we move "), b")"]
        return "OK", [b"done"]

    def append(self, mailbox, flags, date, message):
        self.appended.append((mailbox, flags, message))
        return "OK", [b"appended"]

    def logout(self):
        return "BYE", [b""]


class MailTests(IntegrationCase):
    def setUp(self):
        super().setUp()
        FakeIMAP.instances = []
        self.enterContext(patch.object(mail.imaplib, "IMAP4_SSL", FakeIMAP))
        self.enterContext(patch.object(mail, "_store_password", lambda identifier, password: None))
        self.enterContext(patch.object(mail, "_password", lambda identifier: "app-password"))
        account = mail.add_account({"address": "me@gmail.com", "imap_host": "imap.gmail.com",
                                    "smtp_host": "smtp.gmail.com", "password": "app-password"})
        self.account = mail.PREFIX + account["id"]
        self.registry.set(self.account, enabled=True)

    def test_each_account_is_its_own_switch_and_is_named_for_the_planner(self):
        integration = self.registry.get(self.account)
        self.assertEqual((integration.name, integration.category), ("Gmail", "communication"))
        self.assertIn(f'account="{self.account}"', self.registry.planner_catalog())
        self.assertIn("mail_search", self.registry.enabled_tool_names())
        self.registry.set(self.account, enabled=False)
        self.assertNotIn("mail_search", self.registry.enabled_tool_names())
        with self.assertRaisesRegex(AccessDisabled, "Gmail access is turned off"):
            self.registry.check("mail_search", {"account": self.account})
        off = self.registry.planner_catalog().split("Turned off by the user: ")[1].split(".")[0]
        self.assertIn("Gmail", off.split(", "))

    def test_a_switched_off_account_also_closes_mail_apps_and_its_website(self):
        registry = build_registry(AccessStore(self.home / "a2.json",
                                              known_apps=lambda: ["net.thunderbird.Thunderbird.desktop"]),
                                  lambda: [{"desktop_id": "net.thunderbird.Thunderbird.desktop",
                                            "name": "Thunderbird", "categories": "Network;Email;"}])
        registry.set(self.account, enabled=True)
        registry.check("app_focus", {"desktop_id": "net.thunderbird.Thunderbird.desktop"})
        registry.set(self.account, enabled=False)
        with self.assertRaisesRegex(AccessDisabled, "can show Gmail"):
            registry.check("app_focus", {"desktop_id": "net.thunderbird.Thunderbird.desktop"})
        with self.assertRaisesRegex(AccessDisabled, "part of Gmail"):
            registry.check("open_url", {"url": "https://mail.google.com/mail/u/0/"})

    def test_search_uses_gmail_syntax_and_reading_never_marks_read(self):
        found = self.call("mail_search", account=self.account, query="from:john friday")
        box = FakeIMAP.instances[-1]
        self.assertIn(("UID", "SEARCH", "X-GM-RAW", '"from:john friday"'), box.commands)
        self.assertIn(("SELECT", '"[Gmail]/All Mail"', True), box.commands)
        message = found["messages"][0]
        self.assertEqual((message["subject"], message["flagged"], message["important"]),
                         ("Friday's meeting", True, True))
        read = self.call("mail_read", account=self.account, id=message["id"])
        self.assertIn("3 PM", read["text"])
        fetch = [c for c in FakeIMAP.instances[-1].commands if c[:2] == ("UID", "FETCH")]
        self.assertIn("BODY.PEEK[]", fetch[0][3], "reading marked the message read")

    def test_archive_is_its_own_permission_and_uses_gmail_labels(self):
        found = self.call("mail_search", account=self.account)["messages"][0]["id"]
        self.registry.set(self.account, capabilities={"archive": False})
        with self.assertRaisesRegex(AccessDisabled, "Archive emails"):
            self.registry.check("mail_organize", {"account": self.account, "ids": [found], "action": "archive"})
        self.call("mail_organize", account=self.account, ids=[found], action="flag")
        self.registry.set(self.account, capabilities={"archive": True})
        self.call("mail_organize", account=self.account, ids=[found], action="archive")
        self.assertIn(("UID", "STORE", "42", "-X-GM-LABELS", "(\\Inbox)"), FakeIMAP.instances[-1].commands)

    def test_drafts_are_saved_as_replies_and_nothing_is_sent(self):
        found = self.call("mail_search", account=self.account)["messages"][0]["id"]
        with patch.object(mail.smtplib, "SMTP_SSL") as smtp:
            self.call("mail_draft", account=self.account, to="john@example.com",
                      body="3 PM works.", reply_to_id=found)
        smtp.assert_not_called()
        folder, flags, message = FakeIMAP.instances[-1].appended[0]
        self.assertEqual((folder, flags), ('"[Gmail]/Drafts"', "(\\Draft \\Seen)"))
        self.assertIn(b"In-Reply-To: <m1@example.com>", message)
        self.assertIn(b"Subject: Re: Friday's meeting", message)

    def test_sending_asks_first_with_the_whole_message_and_uses_smtp(self):
        arguments = {"account": self.account, "to": "john@example.com", "subject": "Moved",
                     "body": "See you at 3 PM."}
        self.assertTrue(self.registry.needs_confirmation("mail_send", arguments))
        preview = self.registry.tools["mail_send"].confirmation_text(arguments)
        for part in ("From: me@gmail.com", "To: john@example.com", "Subject: Moved", "See you at 3 PM."):
            self.assertIn(part, preview)
        with patch.object(mail.smtplib, "SMTP_SSL") as smtp:
            result = self.call("mail_send", **arguments)
        self.assertTrue(result["sent"])
        client = smtp.return_value  # SMTP.__enter__ returns the connection itself
        client.login.assert_called_once_with("me@gmail.com", "app-password")
        client.send_message.assert_called_once()
        self.assertEqual(FakeIMAP.instances[-1].appended, [], "Gmail files sent mail itself")

    def test_follow_ups_find_who_is_waiting_on_whom(self):
        inbox = [
            {"from": "John <john@example.com>", "message_id": "<a@x>", "answered": False, "list": ""},
            {"from": "Ann <ann@example.com>", "message_id": "<b@x>", "answered": True, "list": ""},
            {"from": "News <news@example.com>", "message_id": "<c@x>", "answered": False,
             "list": "<https://unsubscribe>"},
            {"from": "Bot <noreply@example.com>", "message_id": "<d@x>", "answered": False, "list": ""},
            {"from": "Eve <eve@example.com>", "message_id": "<e@x>", "answered": False, "list": "",
             "in_reply_to": "<s2@me>"},
            {"from": "Zed <zed@example.com>", "message_id": "<f@x>", "answered": False, "list": ""},
        ]
        sent = [{"to": "zed@example.com", "message_id": "<s1@me>", "in_reply_to": "<f@x>"},
                {"to": "eve@example.com", "message_id": "<s2@me>"},
                {"to": "sam@example.com", "message_id": "<s3@me>"}]
        result = mail.followups(inbox, sent, "me@gmail.com")
        self.assertEqual([m["message_id"] for m in result["waiting_on_you"]], ["<a@x>", "<e@x>"])
        self.assertEqual([m["message_id"] for m in result["waiting_on_them"]], ["<s1@me>", "<s3@me>"])

    def test_parsers_handle_real_server_shapes(self):
        roles = mail.parse_list([b'(\\HasNoChildren \\Sent) "/" "Sent Items"', b'(\\Trash) "." Trash'])
        self.assertEqual(roles, {"sent": "Sent Items", "trash": "Trash"})
        self.assertEqual(mail.search_criteria(False, 'say "hi"', True, 7)[:3], ["TEXT", '"say \\"hi\\""', "UNSEEN"])
        self.assertEqual(mail.decode_id(mail.encode_id("[Gmail]/All Mail", "77", "42")),
                         ("[Gmail]/All Mail", "77", "42"))
        with self.assertRaises(ValueError):
            mail.decode_id("not-an-id")
