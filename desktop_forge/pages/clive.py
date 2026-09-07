"""CLIVE's expanded conversation and setup, using the standard GTK runtime."""
from __future__ import annotations

import json
import re
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..backend import markup
from ..clive.attachments import MAX_FILES, MAX_TEXT
from ..clive.client import Client
from ..clive.policy import APPROVAL_MODES
from ..clive.settings import MAX_SYSTEM_PROMPT, RANGES

# Every field the service accepts, so the dialog always sends a complete set
# rather than whichever rows happen to have been touched.
SETTING_KEYS = ("cloud_model", "local_model", "cloud_enabled",
                "free_account_confirmed", "local_context", "max_turns",
                "approval_mode", "system_prompt", "context_files")

# Parallel to APPROVAL_MODES. The hint is what actually tells the modes apart,
# so it sits on the row rather than in the group description.
APPROVAL_LABELS = ("Always ask", "Auto-approve read-only tools", "Auto-approve everything")
APPROVAL_HINTS = (
    "Every task shows its preview and waits for Approve",
    "Searching, reading files and pages, and listing run straight away. Writing, "
    "moving or trashing files, launching apps and desktop control still wait",
    "Nothing waits. CLIVE acts as soon as it has a plan")

# Shown once, before approval is ever switched off. Kept in step with the CLIVE
# section of README.md.
UNATTENDED_WARNING = (
    "CLIVE will create, move and trash files in your home folder, launch applications, "
    "open pages, and click and type in windows — with no preview and no Approve.\n\n"
    "Its boundaries still hold: no terminal commands, nothing outside your home folder, "
    "no hidden or credential files. Every action is still recorded in the task log."
)

# The composer is a text view, which has no length property of its own, so the
# same ceiling the single-line entry used is applied to the buffer instead.
MAX_MESSAGE = 16000

# Offered on an empty conversation. Each one prefills the composer rather than
# sending, so the first thing CLIVE ever does is still the user's own words.
SUGGESTIONS = (
    ("Search the web", "Search the web for "),
    ("Find a file", "Find the file in my Documents folder that "),
    ("Add a reminder", "Remind me to "),
)

# The model-test row's resting subtitle, restored if a run reports nothing.
TEST_HINT = "A real image, tool and streaming exchange with each one"

# Shown once, before cloud reasoning is ever enabled. Kept in step with the
# CLIVE section of README.md.
CLOUD_BILLING = (
    "CLIVE never buys credits, but an API key attached to a funded account can "
    "spend that account's balance.\n\n"
    "Check your Ollama account is on the Free plan, with no purchased credits and "
    "no automatic top-ups. Ollama controls account billing; CLIVE cannot inspect "
    "or enforce it."
)


def linked_text(text):
    """Escape for Pango, then restore Markdown links as real ones."""
    return re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>',
                  markup(text))


def icon_button(icon_name, tooltip, callback):
    button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", callback)
    return button


def pick_files(parent, title, multiple, callback):
    """Run a Gtk.FileDialog and hand back real paths, treating cancel as normal.

    Both pickers -- the composer's and the settings list's -- want the same
    thing, and Gtk.FileDialog needs a Gtk.Window, which an Adw.Dialog is not.
    """
    dialog = Gtk.FileDialog(title=title)
    root = parent.get_root()
    window = root if isinstance(root, Gtk.Window) else None

    def done(source, result):
        try:
            chosen = source.open_multiple_finish(result) if multiple else source.open_finish(result)
        except GLib.Error:
            return  # The user closed the picker, which is not a failure.
        paths = ([chosen.get_item(i).get_path() for i in range(chosen.get_n_items())]
                 if multiple else [chosen.get_path()])
        callback([path for path in paths if path])

    if multiple:
        dialog.open_multiple(window, None, done)
    else:
        dialog.open(window, None, done)
    # Returned so the pickers can be driven from the UI smoke test.
    return dialog


def confirm(parent, heading, body, action, callback, destructive=True):
    """Ask before something irreversible, then run `callback` if confirmed."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", action)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE
                                   if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, response: response == "confirm" and callback())
    dialog.present(parent)
    return dialog


class ClivePage(Gtk.Box):
    """Conversation, task approval and history in one column.

    An Adw.ToolbarView rather than a plain stack of boxes: the composer belongs
    in a bottom bar so it stays put while the transcript takes every remaining
    pixel, and the window's own view switcher already names this page, so there
    is no in-page title to repeat it.
    """

    def __init__(self, toast_overlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = toast_overlay
        self.state = {}
        self.settings = {}
        self.attachments = []
        self.signature = None
        self.client = Client(self.render)

        view = Adw.ToolbarView(vexpand=True)
        view.add_top_bar(self._build_history_bar())
        view.set_content(self._build_transcript())
        view.add_bottom_bar(self._build_composer())
        self.append(view)

        self.client.call("state", lambda result, error: self._result(result, error) if error else self.render(result))
        self.load_settings()
        self._history()

    # -- construction ------------------------------------------------------

    def _build_history_bar(self):
        bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        self.history_model = Gtk.StringList.new(["Current conversation"])
        self.history_dropdown = Gtk.DropDown(model=self.history_model, hexpand=True)
        self.history_dropdown.connect("notify::selected", self._select_history)
        self.history_ids = [""]
        bar.append(self.history_dropdown)
        bar.append(icon_button("tab-new-symbolic", "Start a new chat", self._new))
        bar.append(icon_button("user-trash-symbolic", "Delete this chat", self._delete))
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL,
                                 margin_top=4, margin_bottom=4))
        self.mode = Gtk.Label(label="Connecting…", ellipsize=Pango.EllipsizeMode.END)
        self.mode.add_css_class("dim-label")
        bar.append(self.mode)
        bar.append(icon_button("emblem-system-symbolic", "Models, API key and history",
                               lambda *_: CliveSettings(self).present(self)))
        return bar

    def _build_transcript(self):
        self.chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.partial = Gtk.Label(xalign=0, wrap=True, selectable=True, visible=False)
        self.partial.add_css_class("chat-bubble")
        self.partial.set_halign(Gtk.Align.START)
        self.task_log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=False)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_top=12, margin_bottom=12, margin_start=18, margin_end=18)
        column.append(self.chat)
        column.append(self.partial)
        column.append(self.task_log)
        column.append(self._build_approval())
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         min_content_height=160, child=column)
        return self.scroll

    def _build_approval(self):
        """Preview and Approve as one card, at the end of what you were reading.

        Previously the preview hid inside a collapser and its button sat two
        rows below it, so the thing you had to judge and the thing you had to
        press were never on screen together.
        """
        self.preview_heading = Gtk.Label(label="Task preview", xalign=0)
        self.preview_heading.add_css_class("heading")
        self.preview_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        preview_scroll = Gtk.ScrolledWindow(max_content_height=220, propagate_natural_height=True,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER,
                                            child=self.preview_label)
        self.approve = Gtk.Button(label="Approve task", halign=Gtk.Align.END, visible=False)
        self.approve.add_css_class("suggested-action")
        self.approve.connect("clicked", lambda *_: self.client.call("approve", self._result,
            id=self.state.get("id"), version=self.state.get("version")))
        self.preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self.preview.add_css_class("approval-card")
        for child in (self.preview_heading, preview_scroll, self.approve):
            self.preview.append(child)
        return self.preview

    def _build_composer(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=6, margin_bottom=10, margin_start=18, margin_end=18)
        self.notice = Gtk.Label(xalign=0, wrap=True, selectable=True, visible=False)
        self.notice.add_css_class("issue-error")
        box.append(self.notice)

        status = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        status.append(self.spinner)
        self.activity = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.activity.add_css_class("dim-label")
        status.append(self.activity)
        # Stop stays out here rather than in the approval card: a running task
        # has no card on screen and still has to be stoppable.
        self.stop = Gtk.Button(label="Stop", visible=False)
        self.stop.add_css_class("destructive-action")
        self.stop.connect("clicked", lambda *_: self.client.call("cancel", self._result))
        status.append(self.stop)
        box.append(status)

        # Above the composer, not beside it: file names are long, and pushing
        # them onto the entry's row is what makes the entry stop being usable.
        self.attachment_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, visible=False,
                                          max_children_per_line=4, row_spacing=4, column_spacing=4)
        box.append(self.attachment_box)

        row = Gtk.Box(spacing=8)
        self.entry = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                  top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        self.buffer = self.entry.get_buffer()
        self.buffer.connect("changed", self._composer_changed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._composer_key)
        self.entry.add_controller(keys)
        composer_scroll = Gtk.ScrolledWindow(hexpand=True, min_content_height=34,
                                             max_content_height=120,
                                             propagate_natural_height=True,
                                             hscrollbar_policy=Gtk.PolicyType.NEVER,
                                             child=self.entry)
        composer_scroll.add_css_class("composer-card")
        # A text view has no placeholder of its own, and dropping the entry's
        # left an unlabelled white box as the first thing on the page.
        # Margins match the text view's own, so the hint sits exactly where the
        # first character will land rather than jumping when you start typing.
        self.placeholder = Gtk.Label(label="Ask CLIVE to help…", xalign=0,
                                     halign=Gtk.Align.START, valign=Gtk.Align.START,
                                     margin_start=7, margin_top=7, can_target=False)
        self.placeholder.add_css_class("dim-label")
        overlay = Gtk.Overlay(child=composer_scroll, hexpand=True)
        overlay.add_overlay(self.placeholder)
        row.append(overlay)
        self.send = Gtk.Button(label="Send", valign=Gtk.Align.END,
                               tooltip_text="Send · Enter. Shift+Enter starts a new line.")
        self.send.add_css_class("suggested-action")
        self.send.connect("clicked", self._send)
        self.attach = Gtk.Button(icon_name="mail-attachment-symbolic", valign=Gtk.Align.END,
                                 tooltip_text="Attach files to this message")
        self.attach.connect("clicked", self._choose_attachments)
        row.append(self.attach)
        row.append(self.send)
        box.append(row)
        return box

    # -- attachments ---------------------------------------------------------

    def _choose_attachments(self, *_args):
        def chosen(paths):
            for path in paths:
                if path not in self.attachments:
                    self.attachments.append(path)
            if len(self.attachments) > MAX_FILES:
                del self.attachments[MAX_FILES:]
                self.toast(f"CLIVE takes at most {MAX_FILES} files with one message")
            self._fill_attachments()

        # Held so the picker can be driven from the UI smoke test.
        self.picker = pick_files(self, "Attach files", True, chosen)

    def _fill_attachments(self):
        while child := self.attachment_box.get_first_child():
            self.attachment_box.remove(child)
        self.attachment_box.set_visible(bool(self.attachments))
        for path in self.attachments:
            chip = Gtk.Box(spacing=2)
            chip.add_css_class("attachment-chip")
            chip.append(Gtk.Label(label=Path(path).name, tooltip_text=path,
                                  ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=22))
            chip.append(icon_button("window-close-symbolic", "Remove this file",
                                    lambda _b, value=path: self._drop_attachment(value)))
            self.attachment_box.append(chip)

    def _drop_attachment(self, path):
        if path in self.attachments:
            self.attachments.remove(path)
        self._fill_attachments()

    # -- composer ----------------------------------------------------------

    def _composer_text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(),
                                    self.buffer.get_end_iter(), False)

    def _set_composer(self, text):
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        self.entry.grab_focus()

    def _composer_changed(self, buffer):
        """Apply the entry's old length ceiling to a widget that has none."""
        self.placeholder.set_visible(buffer.get_char_count() == 0)
        if buffer.get_char_count() <= MAX_MESSAGE:
            return
        end = buffer.get_end_iter()
        buffer.delete(buffer.get_iter_at_offset(MAX_MESSAGE), end)

    def _composer_key(self, _controller, keyval, _code, modifier):
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            return Gdk.EVENT_PROPAGATE
        if modifier & Gdk.ModifierType.SHIFT_MASK:
            return Gdk.EVENT_PROPAGATE
        self._send()
        return Gdk.EVENT_STOP

    # -- service -----------------------------------------------------------

    def _result(self, result, error):
        if error:
            self._notice(error)
            self.toast_overlay.add_toast(Adw.Toast(title=error))
        elif isinstance(result, dict) and "status" in result:
            self.render(result)

    def _notice(self, message):
        self.notice.set_text(message)
        self.notice.set_visible(bool(message))

    def toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=message))

    def close(self):
        self.client.close()

    def load_settings(self):
        """Keep the configured models visible even before a task has run."""
        def loaded(result, error):
            if not error and isinstance(result, dict):
                self.settings = result
                self._update_mode()
        self.client.call("settings", loaded)

    def _update_mode(self):
        # A running task reports the model it actually reached; the rest of the
        # time the header should still say what CLIVE is configured to use.
        model = self.state.get("model")
        cloud = self.state.get("mode") == "cloud"
        if not model and self.settings:
            cloud = bool(self.settings.get("cloud_enabled"))
            model = self.settings.get("cloud_model" if cloud else "local_model")
        self.mode.set_text(("Ollama Cloud" if cloud else "Local Ollama") +
                           (" · " + model if model else ""))

    def _send(self, *_args):
        text = self._composer_text().strip()
        if not text or not self.send.get_sensitive():
            return
        def sent(result, error):
            self._result(result, error)
            if not error:
                self.buffer.set_text("")
                self.attachments.clear()
                self._fill_attachments()
                self._history()
        self.client.call("submit", sent, message=text,
                         conversation=self.state.get("conversation", ""),
                         attachments=list(self.attachments))

    def _new(self, *_args):
        self.client.call("view", self._result, conversation="")

    def _delete(self, *_args):
        conversation = self.state.get("conversation")
        if not conversation:
            return
        index = self.history_ids.index(conversation) if conversation in self.history_ids else 0
        title = self.history_model.get_string(index) if index else "this conversation"
        confirm(self, "Delete this chat?",
                f"“{title}” and its recorded actions are removed permanently.",
                "Delete", lambda: self.client.call(
                    "delete", lambda result, error: (self._result(result, error), self._history()),
                    conversation=conversation))

    def _history(self):
        def loaded(result, error):
            if error:
                return
            self._loading_history = True
            self.history_ids = [""] + [r["id"] for r in result]
            self.history_model.splice(0, self.history_model.get_n_items(), ["Current conversation"] + [r["title"] for r in result])
            current = self.state.get("conversation", "")
            self.history_dropdown.set_selected(self.history_ids.index(current) if current in self.history_ids else 0)
            self._loading_history = False
        self.client.call("history", loaded)

    def _select_history(self, *_args):
        if getattr(self, "_loading_history", False):
            return
        index = self.history_dropdown.get_selected()
        if index < len(self.history_ids) and self.history_ids[index]:
            self.client.call("view", self._result, conversation=self.history_ids[index])

    # -- rendering ---------------------------------------------------------

    def render(self, state):
        if not state:
            return
        self.state = state
        status = state.get("status", "idle")
        active = status in ("planning", "running", "awaiting_approval")
        self._update_mode()
        self._notice(state.get("notice", ""))
        self.activity.set_text(state.get("activity", "Ready"))
        if status in ("planning", "running"):
            self.spinner.start()
            self.spinner.set_visible(True)
        else:
            self.spinner.stop()
            self.spinner.set_visible(False)
        self.partial.set_text(state.get("partial", ""))
        self.partial.set_visible(bool(state.get("partial")))
        # An auto-approved task has no button to press, but the permissions it
        # was granted are still worth showing -- the preview is the only place
        # they are written out in words.
        approving = status == "awaiting_approval"
        preview_text = state.get("preview", "")
        self.approve.set_visible(approving)
        self.preview.set_visible(approving or bool(preview_text))
        self.preview_heading.set_text("Task preview" if approving else "Approved task")
        self.preview_label.set_text(preview_text)
        self.stop.set_visible(active)
        self.send.set_sensitive(not active)
        self.attach.set_sensitive(not active)
        self.entry.set_sensitive(not active)
        self.history_dropdown.set_sensitive(not active)
        messages = state.get("messages", [])
        actions = state.get("actions", [])
        signature = json.dumps([messages, actions])
        if signature == self.signature:
            return
        self.signature = signature
        self._fill_chat(messages)
        self._fill_actions(actions)
        GLib.idle_add(self._scroll_bottom)

    def _fill_chat(self, messages):
        while child := self.chat.get_first_child():
            self.chat.remove(child)
        if not messages:
            self.chat.append(self._empty_state())
            return
        for message in messages:
            mine = message["role"] == "user"
            label = Gtk.Label(wrap=True, xalign=0, selectable=True, max_width_chars=64,
                              halign=Gtk.Align.END if mine else Gtk.Align.START)
            label.set_markup(linked_text(message["content"]))
            label.add_css_class("chat-bubble")
            if mine:
                label.add_css_class("chat-bubble-mine")
            self.chat.append(label)

    def _empty_state(self):
        page = Adw.StatusPage(
            icon_name="system-help-symbolic", title="How can I help?",
            description="Search the web, find a file, add a reminder, or work in an app. "
                        "You see the task before I take any action.")
        page.add_css_class("compact")
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        for text, prefill in SUGGESTIONS:
            button = Gtk.Button(label=text)
            button.add_css_class("pill")
            button.connect("clicked", lambda _b, value=prefill: self._set_composer(value))
            buttons.append(button)
        page.set_child(buttons)
        return page

    def _fill_actions(self, actions):
        """One collapser per action, not one wall of JSON for all of them."""
        while child := self.task_log.get_first_child():
            self.task_log.remove(child)
        self.task_log.set_visible(bool(actions))
        for action in actions[-20:]:
            body = Gtk.Label(xalign=0, wrap=True, selectable=True, margin_start=12, margin_top=4,
                             label=json.dumps(action["result"], ensure_ascii=False, indent=2)[:3000])
            body.add_css_class("monospace-dim")
            self.task_log.append(Gtk.Expander(
                label=action["tool"].replace("_", " ").capitalize(), child=body))

    def _scroll_bottom(self):
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE


class CliveSettings(Adw.Dialog):
    """Models, credential and history.

    Everything the service can store is editable here, each control says what it
    does, and the only thing that saves is the Save button -- so a half-finished
    edit is never mistaken for a stored setting.
    """

    def __init__(self, page):
        super().__init__(title="CLIVE settings", content_width=620, content_height=700)
        self.page = page
        self.client = page.client
        self.saved = {}
        self.confirmed = False
        self.unattended = False
        self.context_files = []
        self.file_rows = []
        self._loading = True

        self.window_title = Adw.WindowTitle(title="CLIVE settings", subtitle="")
        header = Adw.HeaderBar(title_widget=self.window_title)
        self.save_button = Gtk.Button(label="Save", sensitive=False)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self._save)
        header.pack_end(self.save_button)
        self.banner = Adw.Banner(revealed=False)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)

        prefs = Adw.PreferencesPage()
        cloud_group = Adw.PreferencesGroup(
            title="Ollama Cloud",
            description="Your API key is stored in GNOME Keyring. A key also enables live "
                        "web search and page fetching, even with cloud reasoning turned off. "
                        "The cloud model must support tool calling — for example "
                        "gemma4:31b or qwen3.5:235b-cloud.")
        self.key = Adw.PasswordEntryRow(title="Ollama API key")
        save_key = Gtk.Button(label="Save key", valign=Gtk.Align.CENTER, sensitive=False)
        save_key.connect("clicked", self._save_key)
        self.key.add_suffix(save_key)
        self.key.connect("notify::text", self._key_typed)
        self._save_key_button = save_key
        cloud_group.add(self.key)

        self.key_status = Adw.ActionRow(title="Saved key", subtitle="Checking…")
        self.key_spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.key_status.add_suffix(self.key_spinner)
        check = Gtk.Button(label="Check", valign=Gtk.Align.CENTER,
                           tooltip_text="Ask Ollama whether the saved key works")
        check.connect("clicked", self._check_key)
        self.key_status.add_suffix(check)
        remove = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
        remove.add_css_class("destructive-action")
        remove.connect("clicked", self._remove_key)
        self.key_status.add_suffix(remove)
        cloud_group.add(self.key_status)

        self.enabled = Adw.SwitchRow(
            title="Use Ollama Cloud",
            subtitle="Reason on Ollama's servers first, falling back to the local model")
        self.enabled.connect("notify::active", self._cloud_toggled)
        cloud_group.add(self.enabled)
        self.cloud = Adw.EntryRow(title="Cloud model")
        self.cloud.connect("notify::text", self._changed)
        cloud_group.add(self.cloud)
        prefs.add(cloud_group)

        local_group = Adw.PreferencesGroup(
            title="Local model",
            description="Runs on this machine and is used whenever the cloud is off or "
                        "unavailable. Name a model you have pulled with `ollama pull` — "
                        "for example qwen3.5:4b.")
        self.local = Adw.EntryRow(title="Local fallback")
        self.local.connect("notify::text", self._changed)
        local_group.add(self.local)
        prefs.add(local_group)

        approval = Adw.PreferencesGroup(
            title="Task approval",
            description="CLIVE previews a task and waits for Approve before it acts. "
                        "Choose how much of that waiting to skip. Whatever you choose, "
                        "CLIVE still refuses anything outside the task's own files, apps "
                        "and tools, and still has no terminal.")
        self.approval = Adw.ComboRow(title="Ask before acting",
                                     model=Gtk.StringList.new(list(APPROVAL_LABELS)))
        self.approval.set_subtitle(APPROVAL_HINTS[0])
        self.approval.set_subtitle_lines(0)
        self.approval.connect("notify::selected", self._approval_selected)
        approval.add(self.approval)
        prefs.add(approval)

        instructions = Adw.PreferencesGroup(
            title="Extra instructions",
            description="Added to CLIVE's own instructions on every task — how you like "
                        "answers written, which folders you mean by default. They refine "
                        "CLIVE's behaviour; they do not grant it permissions. "
                        f"Up to {MAX_SYSTEM_PROMPT:,} characters.")
        self.prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                   top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        self.prompt_buffer = self.prompt.get_buffer()
        self.prompt_buffer.connect("changed", self._prompt_changed)
        prompt_scroll = Gtk.ScrolledWindow(min_content_height=90, max_content_height=180,
                                           propagate_natural_height=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           margin_top=6, margin_bottom=6,
                                           margin_start=6, margin_end=6, child=self.prompt)
        prompt_scroll.add_css_class("composer-card")
        instructions.add(Adw.PreferencesRow(activatable=False, child=prompt_scroll))
        prefs.add(instructions)

        self.files_group = Adw.PreferencesGroup(
            title="Attached files",
            description=f"Sent with every task, up to {MAX_FILES} files. Text is truncated "
                        f"at {MAX_TEXT:,} characters, and images need a model that reports "
                        "vision — Test below says whether yours does. Files must be in your "
                        "home folder and outside hidden folders.")
        self.add_file = Adw.ButtonRow(title="Add file…", start_icon_name="list-add-symbolic")
        self.add_file.connect("activated", self._add_context_file)
        self.files_group.add(self.add_file)
        prefs.add(self.files_group)

        advanced = Adw.PreferencesGroup(title="Advanced")
        expander = Adw.ExpanderRow(title="Model and task limits",
                                   subtitle="Raise these only if tasks stop short or run out of context")
        low, high = RANGES["local_context"]
        self.context = Adw.SpinRow.new_with_range(low, high, 1024)
        self.context.set_title("Local context window")
        self.context.set_subtitle(f"Tokens the local model can hold ({low}–{high})")
        self.context.connect("notify::value", self._changed)
        expander.add_row(self.context)
        low, high = RANGES["max_turns"]
        self.turns = Adw.SpinRow.new_with_range(low, high, 1)
        self.turns.set_title("Maximum task steps")
        self.turns.set_subtitle(f"Tool rounds before CLIVE stops and reports ({low}–{high})")
        self.turns.connect("notify::value", self._changed)
        expander.add_row(self.turns)
        advanced.add(expander)
        prefs.add(advanced)

        actions = Adw.PreferencesGroup(title="Checks and history")
        # An ActionRow rather than a ButtonRow: the run takes minutes, so it
        # needs a spinner, and its answer covers two models, which the banner
        # would show one truncated line of. The subtitle carries the result
        # instead -- wrapped in full, and selectable so it can be copied.
        self.test_row = Adw.ActionRow(title="Test cloud and local models",
                                      subtitle=TEST_HINT)
        self.test_row.set_subtitle_lines(0)
        self.test_row.set_subtitle_selectable(True)
        self.test_spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.test_row.add_suffix(self.test_spinner)
        self.test_button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self.test_button.connect("clicked", self._test)
        self.test_row.add_suffix(self.test_button)
        self.test_row.set_activatable_widget(self.test_button)
        actions.add(self.test_row)
        account = Adw.ButtonRow(title="Open Ollama account", end_icon_name="adw-external-link-symbolic")
        account.connect("activated", lambda *_: Gio.AppInfo.launch_default_for_uri(
            "https://ollama.com/settings", None))
        actions.add(account)
        clear = Adw.ButtonRow(title="Clear all chat history")
        clear.add_css_class("destructive-action")
        clear.connect("activated", self._clear)
        actions.add(clear)
        prefs.add(actions)

        toolbar.set_content(prefs)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(toolbar)
        self.set_child(self.toasts)
        self.set_can_close(False)
        self.connect("close-attempt", self._closing)
        self.client.call("settings", self._loaded)

    # -- state -------------------------------------------------------------

    def _values(self):
        # get_selected() answers GTK_INVALID_LIST_POSITION when nothing is
        # selected, which must not index past the end of the mode list.
        index = self.approval.get_selected()
        return {"cloud_model": self.cloud.get_text().strip(),
                "local_model": self.local.get_text().strip(),
                "cloud_enabled": self.enabled.get_active(),
                "free_account_confirmed": self.confirmed,
                "local_context": int(self.context.get_value()),
                "max_turns": int(self.turns.get_value()),
                "approval_mode": APPROVAL_MODES[index] if index < len(APPROVAL_MODES) else "always",
                "system_prompt": self._prompt_text().strip(),
                "context_files": list(self.context_files)}

    def _prompt_text(self):
        buffer = self.prompt_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _loaded(self, result, error):
        if error:
            # Leave the dialog usable rather than frozen mid-load: the rows keep
            # whatever they hold, and dirty tracking starts from there.
            self._loading = False
            self.saved = self._values()
            self._say(error)
            return
        self._loading = True
        self.cloud.set_text(result["cloud_model"])
        self.local.set_text(result["local_model"])
        self.confirmed = result["free_account_confirmed"]
        self.enabled.set_active(result["cloud_enabled"])
        self.context.set_value(result["local_context"])
        self.turns.set_value(result["max_turns"])
        # Defaulted rather than indexed: after an upgrade the previous CLIVE
        # service can still be running, and it answers without these keys.
        mode = result.get("approval_mode", "always")
        self.approval.set_selected(APPROVAL_MODES.index(mode) if mode in APPROVAL_MODES else 0)
        # A mode already stored is a decision already taken; do not re-ask for it.
        self.unattended = mode == "never"
        self.prompt_buffer.set_text(result.get("system_prompt", ""))
        self.context_files = list(result.get("context_files", []))
        self._fill_context_files()
        self._loading = False
        self.saved = self._values()
        self._sync_dirty()
        self._sync_key(result)

    def _sync_key(self, result):
        if result.get("keyring_error"):
            self.key_status.set_subtitle(result["keyring_error"])
            self._say(result["keyring_error"])
        elif result.get("has_key"):
            self.key_status.set_subtitle("Stored in GNOME Keyring")
        else:
            self.key_status.set_subtitle("Not set. Paste a key above, then press Save key.")

    def _changed(self, *_args):
        self._sync_dirty()

    def _key_typed(self, *_args):
        self._save_key_button.set_sensitive(bool(self.key.get_text().strip()))
        self._sync_dirty()

    def _dirty(self):
        # A key typed but not yet stored counts: pressing Save has to carry it,
        # and closing the dialog has to warn about it.
        return self._values() != self.saved or bool(self.key.get_text().strip())

    def _sync_dirty(self):
        if self._loading:
            return
        dirty = self._dirty()
        self.save_button.set_sensitive(dirty)
        self.window_title.set_subtitle("Unsaved changes" if dirty else "")

    def _closing(self, *_args):
        if not self._dirty():
            self.force_close()
            return
        # Held so the warning can be driven from the UI smoke test.
        self.discard = confirm(self, "Discard unsaved changes?",
                               "The models, limits, approval mode, instructions, attached "
                               "files and API key you edited have not been saved.",
                               "Discard", self.force_close)

    def _say(self, message):
        self.banner.set_title(message)
        self.banner.set_revealed(bool(message))

    def _toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message))

    def _mark_errors(self, errors):
        for key, row in (("cloud_model", self.cloud), ("local_model", self.local),
                         ("local_context", self.context), ("max_turns", self.turns),
                         ("approval_mode", self.approval), ("system_prompt", self.prompt),
                         ("context_files", self.files_group), ("api_key", self.key)):
            problem = errors.get(key, "")
            if problem:
                row.add_css_class("error")
            else:
                row.remove_css_class("error")
            # The banner concatenates every complaint; the tooltip puts each
            # one back on the row it is actually about.
            row.set_tooltip_text(problem or None)

    # -- actions -----------------------------------------------------------

    def _approval_selected(self, row, _param):
        index = row.get_selected()
        # The subtitle is what actually distinguishes the modes, so it follows
        # the selection even while the dialog is still loading.
        self.approval.set_subtitle(APPROVAL_HINTS[index] if index < len(APPROVAL_HINTS) else "")
        if self._loading:
            return
        if APPROVAL_MODES[index] == "never" and not self.unattended:
            self._confirm_unattended(row)
            return
        self._sync_dirty()

    def _confirm_unattended(self, row):
        """Switching approval off entirely is asked about once, like billing."""
        def accepted():
            self.unattended = True
            self._sync_dirty()

        def declined():
            previous = self.saved.get("approval_mode", "always")
            self._loading = True
            row.set_selected(APPROVAL_MODES.index(previous) if previous in APPROVAL_MODES else 0)
            self._loading = False
            self._sync_dirty()

        dialog = Adw.AlertDialog(heading="Let CLIVE act without asking?", body=UNATTENDED_WARNING)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("enable", "Approve everything")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response:
                       accepted() if response == "enable" else declined())
        # Held so the confirmation can be driven from the UI smoke test.
        self.unattended_dialog = dialog
        dialog.present(self)

    def _prompt_changed(self, buffer):
        """Apply the stored ceiling here, where the text can still be corrected."""
        if buffer.get_char_count() > MAX_SYSTEM_PROMPT:
            # The delete re-enters this handler, which then syncs the dirty state.
            buffer.delete(buffer.get_iter_at_offset(MAX_SYSTEM_PROMPT), buffer.get_end_iter())
            return
        self._sync_dirty()

    def _add_context_file(self, *_args):
        def chosen(paths):
            for path in paths:
                if path not in self.context_files and len(self.context_files) < MAX_FILES:
                    self.context_files.append(path)
            self._fill_context_files()
            self._sync_dirty()

        if len(self.context_files) >= MAX_FILES:
            self._toast(f"CLIVE attaches at most {MAX_FILES} files to every task")
            return
        # Held so the picker can be driven from the UI smoke test.
        self.picker = pick_files(self, "Attach a file to every task", False, chosen)

    def _drop_context_file(self, path):
        if path in self.context_files:
            self.context_files.remove(path)
        self._fill_context_files()
        self._sync_dirty()

    def _fill_context_files(self):
        for row in self.file_rows:
            self.files_group.remove(row)
        self.file_rows = []
        for path in self.context_files:
            row = Adw.ActionRow(title=markup(Path(path).name), subtitle=markup(path))
            row.set_subtitle_lines(0)
            row.add_suffix(icon_button("user-trash-symbolic", "Stop attaching this file",
                                       lambda _b, value=path: self._drop_context_file(value)))
            self.files_group.add(row)
            self.file_rows.append(row)
        # A preferences group appends, so re-adding keeps "Add file…" last.
        self.files_group.remove(self.add_file)
        self.files_group.add(self.add_file)

    def _cloud_toggled(self, row, _param):
        if self._loading:
            return
        if row.get_active() and not self.confirmed:
            self._confirm_cloud(row)
            return
        self._sync_dirty()

    def _confirm_cloud(self, row):
        def accepted():
            self.confirmed = True
            self._sync_dirty()

        def declined():
            self._loading = True
            row.set_active(False)
            self._loading = False
            self._sync_dirty()

        dialog = Adw.AlertDialog(heading="Enable Ollama Cloud?", body=CLOUD_BILLING)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("enable", "Enable cloud")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response:
                       accepted() if response == "enable" else declined())
        # Held so the confirmation can be driven from the UI smoke test.
        self.billing = dialog
        dialog.present(self)

    def _save(self, *_args):
        values = self._values()
        arguments = {"settings": {k: values[k] for k in SETTING_KEYS}}
        key = self.key.get_text().strip()
        if key:
            arguments["api_key"] = key

        def saved(result, error):
            if error:
                self._say(error)
                self._toast(error)
                return
            errors = result.get("errors", {})
            self._mark_errors(errors)
            self._sync_key(result)
            if errors:
                # Nothing in the settings block was written, so every typed
                # value stays on screen to be corrected -- including the key,
                # unless it was the one field that did get stored.
                if "api_key" not in errors:
                    self.key.set_text("")
                self._say("  ".join(errors.values()))
                self._toast("Some settings were not saved")
                self._sync_dirty()
                return
            self.key.set_text("")
            self._loaded(result, None)
            if values["cloud_enabled"] and not result["cloud_enabled"]:
                self._say("Cloud reasoning stayed off: confirm the account terms first.")
            else:
                self._say("")
                self._toast("Settings saved")
            self.page.load_settings()

        self.client.call("configure", saved, **arguments)

    def _save_key(self, *_args):
        """Store the credential on its own, so no other field can discard it."""
        value = self.key.get_text().strip()
        if not value:
            return

        def saved(result, error):
            if error:
                self._say(error)
                self._toast(error)
                return
            problem = result.get("errors", {}).get("api_key")
            if problem:
                self._say(problem)
                self._toast(problem)
                return
            self.key.set_text("")
            self._sync_key(result)
            self._say("")
            self._toast("API key saved")
            self._check_key()

        self.client.call("configure", saved, api_key=value)

    def _check_key(self, *_args):
        self._say("Asking Ollama whether the saved key works…")
        self._spin(self.key_spinner, True)

        def checked(result, error):
            self._spin(self.key_spinner, False)
            if error:
                self._say(error)
                return
            self._say(result["message"])
            self.key_status.set_subtitle(result["message"])

        self.client.call("check_key", checked)

    def _remove_key(self, *_args):
        def removed(result, error):
            if error:
                self._say(error)
                return
            self._sync_key(result)
            self._toast("API key removed")

        confirm(self, "Remove the saved API key?",
                "Cloud reasoning and live web research stop working until you add a key again.",
                "Remove", lambda: self.client.call("configure", removed, api_key=""))

    def _spin(self, spinner, running):
        """Show a check is still going: both of these take real network time."""
        spinner.set_visible(running)
        if running:
            spinner.start()
        else:
            spinner.stop()

    def _test(self, *_args):
        self.test_button.set_sensitive(False)
        self._spin(self.test_spinner, True)
        self._say("Testing both models with a real image, tool and streaming exchange. "
                  "The local model can take several minutes.")

        def tested(result, error):
            self.test_button.set_sensitive(True)
            self._spin(self.test_spinner, False)
            self._say("")
            self.test_row.set_subtitle(markup(error or result["message"]) or TEST_HINT)

        self.client.call("validate", tested)

    def _clear(self, *_args):
        def cleared(result, error):
            if error:
                self._say(error)
                return
            self.page._history()
            self._toast("Chat history cleared")

        confirm(self, "Clear all chat history?",
                "Every conversation, its recorded actions and the agent's saved checkpoints "
                "are deleted permanently.",
                "Clear everything", lambda: self.client.call("delete", cleared))
