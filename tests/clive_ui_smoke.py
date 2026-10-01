"""Construct and exercise CLIVE's GTK UI inside the isolated Shell smoke session."""
import json
import os
import sys
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

sys.path.insert(0, os.environ["DF_TEST_ROOT"])
from desktop_forge.pages import clive

STORED = {"cloud_model": "gemma4:31b", "local_model": "qwen3.5:4b",
          "free_account_confirmed": False, "cloud_enabled": False,
          "local_context": 8192, "max_turns": 16, "approval_mode": "always",
          "system_prompt": "", "context_files": [], "has_key": False,
          "keyring_error": "", "errors": {}}


SELECTION = {"endpoint": "local", "cloud_model": "gemma4:31b", "local_model": "qwen3.5:4b",
             "cloud_models": ["gemma4:31b"], "local_models": ["qwen3.5:4b", "llama3.2:3b"],
             "cloud_ready": True, "fallback_to_local": True}
AVAILABLE = {"local": ["qwen3.5:4b", "qwen3.5:9b", "smollm2:1.7b"],
             "cloud": ["gemma4:31b", "gpt-oss:120b"]}


def iter_children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        child = child.get_next_sibling()


def iter_descendants(widget):
    for child in iter_children(widget):
        yield child
        yield from iter_descendants(child)


def offered(row):
    """The names a model dropdown lists."""
    model = row.get_model()
    return [model.get_string(i) for i in range(model.get_n_items())]


def capability(identifier, label, risk="low", enabled=True, confirm=None):
    return {"id": identifier, "label": label, "kind": "read", "risk": risk, "description": "",
            "enabled": enabled, "confirm": confirm, "confirm_locked": False}


ACCESS = {"paused": False, "error": "", "categories": [
    {"id": "files", "name": "Files & Documents"}, {"id": "communication", "name": "Communication"},
    {"id": "productivity", "name": "Productivity"}],
    "apps": [
        {"id": "files", "name": "Files", "category": "files", "icon": "system-file-manager-symbolic",
         "description": "Visible files", "desktop_id": "", "enabled": True, "new": False,
         "access": "read_write", "status": {"state": "ready", "detail": ""},
         "last_used": {"time": time.time() - 300, "action": "Reading a file…"},
         "capabilities": [capability("read", "Read files"),
                          capability("delete", "Move files to Trash", "high", True, True)]},
        {"id": "app:org.signal.Signal.desktop", "name": "Signal", "category": "communication",
         "icon": "", "description": "Messaging", "desktop_id": "org.signal.Signal.desktop",
         "enabled": False, "new": True, "access": "read_write", "status": {"state": "ready", "detail": ""},
         "last_used": None, "capabilities": [capability("open", "Open and switch to it", "normal")]},
        {"id": "tasks", "name": "To-Do & Reminders", "category": "productivity", "icon": "",
         "description": "Lists", "desktop_id": "", "enabled": True, "new": False,
         "access": "read_write", "status": {"state": "ready", "detail": ""}, "last_used": None,
         "capabilities": [capability("read", "See tasks")]},
    ]}


class FakeClient:
    """Records what the dialog asks the service to do, and answers it."""

    def __init__(self, changed):
        self.changed = changed
        self.calls = []
        self.configure_response = None
        self.access = json.loads(json.dumps(ACCESS))

    def call(self, op, callback=lambda result, error: None, **args):
        self.calls.append((op, args))
        if op == "configure":
            callback(self.configure_response or dict(STORED), None)
            return
        if op == "model_select":
            callback({**SELECTION, "endpoint": args["endpoint"],
                      f"{args['endpoint']}_model": args.get("model") or
                      SELECTION[f"{args['endpoint']}_model"]}, None)
            return
        if op == "models_available":
            error = getattr(self, "models_error", "") if args["endpoint"] == "local" else ""
            callback(None if error else {"endpoint": args["endpoint"],
                                         "models": AVAILABLE[args["endpoint"]]}, error or None)
            return
        if op == "draft_add":
            self.draft = [{"id": "d1", "name": "report.txt", "kind": "text", "label": "Text", "size": 120,
                           "pages": None, "chars": 120, "truncated": False, "preview": "Quarterly…",
                           "thumbnail": "", "path": args["paths"][0]}]
            callback({"draft": self.draft, "errors": []}, None)
            return
        if op == "submit":
            self.draft = []
            callback({"id": "t", "conversation": "c"}, None)
            return
        if op == "goa_accounts":
            callback({"accounts": getattr(self, "goa", [])}, None)
            return
        if op == "access_check":
            callback({"state": "ready", "detail": "Connected"}, None)
            return
        if op == "mail_account_add":
            callback(json.loads(json.dumps(self.access)), None)
            return
        if op in ("access_set", "access_bulk"):
            listing = self.access
            for app in listing["apps"]:
                if op == "access_bulk" and app["id"] in args.get("ids", [a["id"] for a in listing["apps"]]):
                    app["enabled"] = args["enabled"]
                if op == "access_set" and app["id"] == args["id"]:
                    if "enabled" in args:
                        app["enabled"] = args["enabled"]
                    for cap in app["capabilities"]:
                        cap["enabled"] = args.get("capabilities", {}).get(cap["id"], cap["enabled"])
                        if cap["id"] in args.get("confirm", {}):
                            cap["confirm"] = args["confirm"][cap["id"]]
            callback(json.loads(json.dumps(listing)), None)
            return
        responses = {"state": {"status": "idle", "messages": []}, "history": [],
                     "access": json.loads(json.dumps(self.access)),
                     "settings": dict(STORED),
                     "check_key": {"ok": True, "message": "API key accepted."},
                     "validate": {"message": "Cloud: turned off."}}
        callback(responses.get(op, {}), None)

    def close(self):
        pass


clive.Client = FakeClient
application = Adw.Application(application_id="org.jrf.DesktopForge.CliveSmoke")


def check_access(settings):
    """The App Access page: every app, its own switch, search and bulk changes."""
    access = settings.access
    assert set(access.rows) == {"files", "app:org.signal.Signal.desktop", "tasks"}, access.rows
    files = access.rows["files"]
    subtitle = GLib.markup_escape_text("Read & write · Ready · Last used 5 minutes ago")
    assert files.switch.get_active() and files.get_subtitle() == subtitle, files.get_subtitle()
    signal = access.rows["app:org.signal.Signal.desktop"]
    assert not signal.switch.get_active() and signal.badge.get_visible(), "a new app must start off"
    assert "3 apps" in access.count.get_label() and "2 of 3" in access.count.get_label(), \
        access.count.get_label()
    client = settings.client
    client.calls.clear()
    signal.switch.set_active(True)
    assert client.calls[-1] == ("access_set", {"id": "app:org.signal.Signal.desktop", "enabled": True}), \
        client.calls
    # Expanding builds the permission rows; turning the app off greys them.
    files.set_expanded(True)
    assert files.capability_rows["read"].get_sensitive()
    assert files.confirm_rows["delete"].get_active(), "a high-risk action must ask by default"
    client.calls.clear()
    files.capability_rows["read"].set_active(False)
    assert client.calls[-1] == ("access_set", {"id": "files", "capabilities": {"read": False}}), \
        client.calls
    # Search narrows the list and Enable All becomes Enable Shown.
    access.search.set_text("sig")
    access._apply_filter()
    assert signal.get_visible() and not files.get_visible()
    assert access.enable_all.get_label() == "Enable Shown"
    access._bulk(False)
    access.bulk_dialog.emit("response", "apply")
    assert client.calls[-1] == ("access_bulk", {"enabled": False, "ids": ["app:org.signal.Signal.desktop"]}), \
        client.calls
    access.search.set_text("")
    access._apply_filter()
    access.bulk_dialog.force_close()
    # The pause switch is the emergency stop.
    client.calls.clear()
    access.pause.set_active(True)
    assert client.calls[0] == ("access_pause", {"paused": True}), client.calls
    # Permissions lists every high-risk action of the switched-on apps.
    assert [row.get_title() for row in settings.ask_first.rows] == ["Move files to Trash"], \
        [row.get_title() for row in settings.ask_first.rows]
    # The Connect dialog lists Online Accounts' mail accounts and connects one.
    from desktop_forge.pages.clive_access import ConnectMailDialog
    client.goa = [{"goa_id": "g1", "provider": "Google", "address": "me@gmail.com", "oauth": True}]
    connect = ConnectMailDialog(access)
    assert [row.get_title() for row in connect.online_rows] == ["me@gmail.com"], \
        [row.get_title() for row in connect.online_rows]
    client.calls.clear()
    connect._connect({"goa_id": "g1"})
    assert client.calls[0] == ("mail_account_add", {"account": {"goa_id": "g1"}}), client.calls
    # Options and the live check are on each app's row.
    files.check_button = None
    client.calls.clear()
    access.check("files", files.status_row)
    assert client.calls[-1] == ("access_check", {"id": "files"}), client.calls
    # Sections: App Access has no Save button; the others do.
    settings.show_section("access")
    assert not settings.save_button.get_visible()
    settings.show_section("models")
    assert settings.save_button.get_visible()


def check_settings(page):
    settings = clive.CliveSettings(page)
    settings.present(page)
    client = page.client
    check_access(settings)

    # Every stored setting is editable, including the two that previously had
    # no UI at all and were silently dropped on save.
    # The models are dropdowns of the saved models plus what Ollama reports,
    # and the stored model stays selected when that answer arrives after it.
    assert settings._model_value(settings.local) == "qwen3.5:4b", settings._model_value(settings.local)
    assert settings._model_value(settings.cloud) == "gemma4:31b", settings._model_value(settings.cloud)
    assert offered(settings.local) == AVAILABLE["local"], offered(settings.local)
    assert offered(settings.cloud) == AVAILABLE["cloud"], offered(settings.cloud)
    assert settings.local.get_subtitle() == "Installed on this machine", settings.local.get_subtitle()
    assert settings.cloud.get_subtitle() == "Available on Ollama Cloud", settings.cloud.get_subtitle()
    # When Ollama cannot list its models the row says why, and the stored
    # model is still the one offered and selected.
    client.models_error = "The local Ollama service is not running."
    settings._list_models()
    assert settings.local.get_subtitle() == client.models_error, settings.local.get_subtitle()
    assert offered(settings.local) == ["qwen3.5:4b"], offered(settings.local)
    assert settings._model_value(settings.local) == "qwen3.5:4b"
    client.models_error = ""
    settings._list_models()
    assert settings.context.get_value() == 8192, settings.context.get_value()
    assert settings.turns.get_value() == 16, settings.turns.get_value()
    assert not hasattr(settings, "free"), "the free-plan switch must be gone"

    # Approval, extra instructions and always-attached files load as stored.
    assert settings.approval.get_selected() == 0, settings.approval.get_selected()
    assert settings.approval.get_subtitle() == clive.APPROVAL_HINTS[0]
    assert settings._prompt_text() == "", settings._prompt_text()
    assert settings.context_files == [], settings.context_files

    # Nothing is dirty until something changes, and then Save says so.
    assert not settings.save_button.get_sensitive(), "Save was live before any edit"
    settings.turns.set_value(24)
    assert settings.save_button.get_sensitive(), "Save stayed dead after an edit"
    assert settings.window_title.get_subtitle() == "Unsaved changes"

    # Each of the new controls marks the dialog dirty on its own.
    settings.prompt_buffer.set_text("Answer briefly.")
    assert settings.save_button.get_sensitive(), "an edited instruction left Save dead"
    settings.context_files = ["/home/someone/notes.md"]
    settings._fill_context_files()
    assert len(settings.file_rows) == 1, settings.file_rows
    # Picking another installed model is an edit like any other, and a later
    # answer from Ollama does not move the pick.
    settings.local.set_selected(offered(settings.local).index("qwen3.5:9b"))
    settings._list_models()
    assert settings._model_value(settings.local) == "qwen3.5:9b", settings._model_value(settings.local)

    client.calls.clear()
    settings.key.set_text("secret-key")
    settings._save()
    op, args = client.calls[0]
    assert op == "configure", client.calls
    sent = args["settings"]
    assert set(sent) == set(clive.SETTING_KEYS), sent
    assert sent["max_turns"] == 24 and isinstance(sent["max_turns"], int), sent
    assert sent["local_context"] == 8192 and isinstance(sent["local_context"], int), sent
    assert sent["approval_mode"] == "always", sent
    assert sent["system_prompt"] == "Answer briefly.", sent
    assert sent["context_files"] == ["/home/someone/notes.md"], sent
    assert sent["local_model"] == "qwen3.5:9b" and sent["cloud_model"] == "gemma4:31b", sent
    # A key typed into the row rides along, so pressing the obvious button
    # cannot throw the credential away.
    assert args["api_key"] == "secret-key", args
    assert not settings.save_button.get_sensitive(), "Save stayed live after a clean save"
    assert settings.key.get_text() == "", "the saved key was left on screen"

    # A rejected field is marked, the dialog stays dirty, and a key that was
    # refused keeps its text so it can be retried.
    client.calls.clear()
    client.configure_response = {**STORED, "errors": {
        "cloud_model": "Cloud model: enter a valid Ollama model name",
        "api_key": "GNOME Keyring is locked or unavailable. Unlock your login keyring, then try again."}}
    settings.key.set_text("retry-key")
    settings.cloud.set_selected(offered(settings.cloud).index("gpt-oss:120b"))
    settings._save()
    assert "error" in settings.cloud.get_css_classes(), settings.cloud.get_css_classes()
    assert "error" in settings.key.get_css_classes(), settings.key.get_css_classes()
    assert settings.key.get_text() == "retry-key", "a refused key was discarded"
    assert settings._model_value(settings.cloud) == "gpt-oss:120b", "a refused edit was reverted under the user"
    assert settings.save_button.get_sensitive(), "a refused save cleared the dirty state"
    client.configure_response = None

    # The credential has its own path too, carrying nothing else with it.
    client.calls.clear()
    settings.key.set_text("standalone-key")
    settings._save_key()
    op, args = client.calls[0]
    assert op == "configure" and args == {"api_key": "standalone-key"}, client.calls

    # Switching approval off entirely asks once and does not assume the answer.
    assert not settings.unattended
    unattended = clive.APPROVAL_MODES.index("never")
    settings.approval.set_selected(unattended)
    assert not settings.unattended, "unattended mode was taken without an answer"
    settings.unattended_dialog.emit("response", "cancel")
    assert settings.approval.get_selected() == 0, "declining left approval switched off"
    settings.approval.set_selected(unattended)
    settings.unattended_dialog.emit("response", "enable")
    assert settings.unattended and settings.approval.get_selected() == unattended
    assert settings._values()["approval_mode"] == "never", settings._values()

    # Turning on cloud asks about billing once and does not assume the answer.
    assert not settings.confirmed
    settings.enabled.set_active(True)
    assert not settings.confirmed, "cloud was confirmed without an answer"
    settings.billing.emit("response", "cancel")
    assert not settings.enabled.get_active(), "declining left the cloud switch on"
    settings.enabled.set_active(True)
    settings.billing.emit("response", "enable")
    assert settings.confirmed and settings.enabled.get_active()

    # Closing with an edit in flight warns instead of dropping it, which is the
    # whole complaint about settings that "do not save".
    closed = []
    settings.connect("closed", lambda *_: closed.append(True))
    settings.key.set_text("never-saved")
    settings.close()
    assert not closed, "an unsaved edit was discarded silently on close"
    settings.discard.close()

    return settings


def activate(app):
    try:
        window = Adw.ApplicationWindow(application=app, default_width=900, default_height=760)
        overlay = Adw.ToastOverlay()
        page = clive.ClivePage(overlay)
        overlay.set_child(page)
        window.set_content(overlay)
        window.present()
        state = {"id": "test", "status": "awaiting_approval", "version": 1, "conversation": "test",
                 "mode": "local", "messages": [{"role": "user", "content": "Find my document"}],
                 "preview": "Search Documents and open the matching file."}
        page.render(state)
        assert page.approve.get_visible() and not page.send.get_sensitive()
        # The configured model is visible before any task has run.
        assert "qwen3.5:4b" in page.mode.get_text(), page.mode.get_text()
        # The header switcher: cloud and local at a glance, one click apart.
        page.render({**state, "selection": SELECTION})
        assert page.mode.get_text() == "Local · qwen3.5:4b", page.mode.get_text()
        page._fill_model_popover()
        rows = []
        child = page.model_popover.get_child().get_first_child()
        while child:
            if isinstance(child, Gtk.ListBox):
                row = child.get_first_child()
                while row:
                    rows.append(row.get_title())
                    row = row.get_next_sibling()
            child = child.get_next_sibling()
        assert rows == ["gemma4:31b", "qwen3.5:4b", "llama3.2:3b"], rows
        page.client.calls.clear()
        page._select_model("cloud", None)
        assert page.client.calls == [("model_select", {"endpoint": "cloud"})], page.client.calls
        assert page.mode.get_text() == "Cloud · gemma4:31b", page.mode.get_text()
        page._select_model("local", "llama3.2:3b")
        assert page.mode.get_text() == "Local · llama3.2:3b", page.mode.get_text()
        # The confirmation card says what will happen, and answers for all of it.
        page.render({**state, "status": "awaiting_confirmation", "confirmation": {"calls": [
            {"id": "c1", "tool": "file_trash", "capability": "Move files to Trash",
             "app_name": "Files", "target": "/home/someone/old.txt"}]}})
        assert page.confirmation.get_visible() and not page.approve.get_visible()
        texts = []
        child = page.confirm_rows.get_first_child()
        while child:
            texts.append(child.get_label())
            child = child.get_next_sibling()
        assert texts == ["Move files to Trash", "Files · /home/someone/old.txt"], texts
        page.client.calls.clear()
        page._answer_confirmation(True)
        assert page.client.calls == [("confirm", {"id": "test", "approved": ["c1"]})], page.client.calls
        page._answer_confirmation(False)
        assert page.client.calls[-1] == ("confirm", {"id": "test", "approved": []}), page.client.calls
        assert not page.send.get_sensitive(), "the composer accepted a message mid-confirmation"
        settings = check_settings(page)
        page.render({**state, "status": "complete", "messages": state["messages"] + [
            {"role": "assistant", "content": "Found it. [Source](https://example.com)"}]})
        assert page.send.get_sensitive() and not page.approve.get_visible()
        # The task that ran stays readable after its button is gone, which is
        # the only written record of what an auto-approved task was allowed.
        assert page.preview.get_visible(), "the approved task vanished with its button"
        assert page.preview_heading.get_text() == "Approved task", page.preview_heading.get_text()

        # Files are staged in the service (shared with the desktop card), listed
        # with a preview before they are sent, and cleared once they are.
        page.client.calls.clear()
        page._stage(["/home/someone/report.txt"], ["remote.pdf"])
        assert page.client.calls[0] == ("draft_add", {"paths": ["/home/someone/report.txt"]}), \
            page.client.calls
        assert page.attachment_box.get_visible() and len(page.attachments) == 1
        chip = page.attachment_box.get_first_child().get_child()
        opener = [c for c in iter_children(chip) if isinstance(c, Gtk.MenuButton)][0]
        assert opener.get_popover() is not None, "a staged file had no preview"
        # Sending with no text is allowed when files are staged.
        page._set_composer("")
        page.client.calls.clear()
        page._send()
        op, args = page.client.calls[0]
        assert op == "submit" and args["use_draft"] is True and args["message"] == "", page.client.calls
        assert page.attachments == [], "a sent attachment stayed on the composer"
        assert not page.attachment_box.get_visible()
        # A message's files show as chips on it, not as an "Attached:" line.
        page.render({"status": "complete", "messages": [{"role": "user",
            "content": "Summarize\n\nAttached: report.txt",
            "attachments": [{"name": "report.txt", "kind": "text", "label": "Text"}]}]})
        texts = [c.get_label() for c in iter_descendants(page.chat) if isinstance(c, Gtk.Label)]
        assert "Summarize" in texts and "report.txt" in texts and \
            not any("Attached:" in t for t in texts), texts
        # Let Wayland deliver text-input enter/leave before destroying a focused
        # entry window. Present+destroy in one frame races GTK's IM context.
        def finish():
            print("CLIVE GTK conversation, approval modes, attachments, instructions, "
                  "model settings, and links passed.", flush=True)
            page.close()
            window.destroy()
            app.quit()
            return False
        def close_settings():
            settings.set_focus(None)
            settings.force_close()  # past the unsaved-changes guard, deliberately
            GLib.timeout_add(500, finish)
            return False
        GLib.timeout_add(500, close_settings)
    except Exception:
        import traceback
        traceback.print_exc()
        app.quit()
        raise


application.connect("activate", activate)
raise SystemExit(application.run([]))
