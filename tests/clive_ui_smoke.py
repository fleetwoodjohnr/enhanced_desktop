"""Construct and exercise CLIVE's GTK UI inside the isolated Shell smoke session."""
import json
import os
import sys
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


class FakeClient:
    """Records what the dialog asks the service to do, and answers it."""

    def __init__(self, changed):
        self.changed = changed
        self.calls = []
        self.configure_response = None

    def call(self, op, callback=lambda result, error: None, **args):
        self.calls.append((op, args))
        if op == "configure":
            callback(self.configure_response or dict(STORED), None)
            return
        responses = {"state": {"status": "idle", "messages": []}, "history": [],
                     "settings": dict(STORED),
                     "check_key": {"ok": True, "message": "API key accepted."},
                     "validate": {"message": "Cloud: turned off."}}
        callback(responses.get(op, {}), None)

    def close(self):
        pass


clive.Client = FakeClient
application = Adw.Application(application_id="org.jrf.DesktopForge.CliveSmoke")


def check_settings(page):
    settings = clive.CliveSettings(page)
    settings.present(page)
    client = page.client

    # Every stored setting is editable, including the two that previously had
    # no UI at all and were silently dropped on save.
    assert settings.local.get_text() == "qwen3.5:4b", settings.local.get_text()
    assert settings.cloud.get_text() == "gemma4:31b", settings.cloud.get_text()
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
    settings.cloud.set_text("bad name")
    settings._save()
    assert "error" in settings.cloud.get_css_classes(), settings.cloud.get_css_classes()
    assert "error" in settings.key.get_css_classes(), settings.key.get_css_classes()
    assert settings.key.get_text() == "retry-key", "a refused key was discarded"
    assert settings.cloud.get_text() == "bad name", "a refused edit was reverted under the user"
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
        settings = check_settings(page)
        page.render({**state, "status": "complete", "messages": state["messages"] + [
            {"role": "assistant", "content": "Found it. [Source](https://example.com)"}]})
        assert page.send.get_sensitive() and not page.approve.get_visible()
        # The task that ran stays readable after its button is gone, which is
        # the only written record of what an auto-approved task was allowed.
        assert page.preview.get_visible(), "the approved task vanished with its button"
        assert page.preview_heading.get_text() == "Approved task", page.preview_heading.get_text()

        # Attachments are listed before they are sent, and cleared once they are.
        page.attachments.append("/home/someone/report.txt")
        page._fill_attachments()
        assert page.attachment_box.get_visible()
        page._set_composer("Summarize this")
        page.client.calls.clear()
        page._send()
        op, args = page.client.calls[0]
        assert op == "submit" and args["attachments"] == ["/home/someone/report.txt"], page.client.calls
        assert page.attachments == [], "a sent attachment stayed on the composer"
        assert not page.attachment_box.get_visible()
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
