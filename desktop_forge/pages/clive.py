"""CLIVE's expanded conversation and setup, using the standard GTK runtime."""
from __future__ import annotations

import base64
import json
import re
import time
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
from .clive_access import AppAccessPage, AskFirstGroup

# Statuses in which a task holds CLIVE, mirroring desktop_forge/clive/agent.py
# (which this page cannot import: its dependencies are optional).
ACTIVE = ("planning", "running", "awaiting_approval", "awaiting_confirmation")

# Every field the service accepts, so the dialog always sends a complete set
# rather than whichever rows happen to have been touched.
SETTING_KEYS = ("cloud_model", "local_model", "cloud_enabled",
                "free_account_confirmed", "local_context", "max_turns",
                "approval_mode", "system_prompt", "context_files", "fallback_to_local",
                "card_density", "show_action_details", "notify_finished", "notify_waiting",
                "cloud_images", "cloud_attachments", "context_turns", "history_days",
                "debug_logging", "followup_hours", "followup_days")
FOLLOWUP_HOURS = (0, 1, 3, 6, 24)
FOLLOWUP_LABELS = ("Off", "Every hour", "Every 3 hours", "Every 6 hours", "Once a day")
FOLLOWUP_PROMPT = ("Look through my emails and tell me which ones are important and which ones "
                   "I need to follow up on.")
DENSITIES = ("comfortable", "compact")
# (key, title, icon) for the settings sidebar, App Access second.
SECTIONS = (
    ("models", "AI Models", "applications-science-symbolic"),
    ("access", "App Access", "view-grid-symbolic"),
    ("permissions", "App Permissions", "security-medium-symbolic"),
    ("attachments", "Attachments", "mail-attachment-symbolic"),
    ("memory", "Memory & Context", "document-open-recent-symbolic"),
    ("appearance", "Appearance", "preferences-desktop-appearance-symbolic"),
    ("notifications", "Notifications", "preferences-system-notifications-symbolic"),
    ("automation", "Automation", "media-playlist-repeat-symbolic"),
    ("privacy", "Privacy", "preferences-system-privacy-symbolic"),
    ("advanced", "Advanced", "applications-engineering-symbolic"),
)

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


def texture_from_base64(data):
    if not data:
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(base64.b64decode(data)))
    except (GLib.Error, ValueError):
        return None


def size_label(size):
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def save_pasted_image(png: bytes) -> str:
    """A pasted image as a file in CLIVE's own folder, where only CLIVE looks."""
    from ..clive.attachments import pasted_folder
    folder = pasted_folder()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"Pasted image {time.strftime('%Y-%m-%d %H.%M.%S')}.png"
    path.write_bytes(png)
    return str(path)


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
        files = ([chosen.get_item(i) for i in range(chosen.get_n_items())] if multiple else [chosen])
        # A remote location without a local mount has no path; say so rather
        # than dropping the file without a word.
        callback([f.get_path() for f in files if f.get_path()],
                 [f.get_basename() or f.get_uri() for f in files if not f.get_path()])

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
        self.followup_banner = Adw.Banner(button_label="Review", revealed=False)
        self.followup_banner.connect("button-clicked", self._review_followups)
        self._followups_dismissed = 0
        view.add_top_bar(self.followup_banner)
        view.set_content(self._build_transcript())
        view.add_bottom_bar(self._build_composer())
        self.append(view)
        self._install_drop_and_paste()

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
        bar.append(self._build_model_button())
        bar.append(icon_button("emblem-system-symbolic", "Models, API key and history",
                               lambda *_: self.open_settings()))
        return bar

    def _build_model_button(self):
        """The model switcher: cloud or local at a glance, and one click away."""
        content = Gtk.Box(spacing=6)
        self.mode_icon = Gtk.Image(icon_name="computer-symbolic")
        self.mode = Gtk.Label(label="Connecting…", ellipsize=Pango.EllipsizeMode.END,
                              max_width_chars=30)
        content.append(self.mode_icon)
        content.append(self.mode)
        content.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.model_popover = Gtk.Popover()
        self.model_popover.connect("show", lambda *_: self._fill_model_popover())
        self.model_button = Gtk.MenuButton(child=content, popover=self.model_popover,
                                           tooltip_text="Switch between the cloud and local model")
        self.model_button.add_css_class("flat")
        return self.model_button

    def _fill_model_popover(self):
        chosen = self.state.get("selection") or {}
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        endpoint = chosen.get("endpoint", "local")
        toggles = Gtk.Box(spacing=0, homogeneous=True, css_classes=["linked"])
        for value, label, icon in (("cloud", "Cloud", "weather-overcast-symbolic"),
                                   ("local", "Local", "computer-symbolic")):
            button = Gtk.ToggleButton(active=endpoint == value)
            inner = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
            inner.append(Gtk.Image(icon_name=icon))
            inner.append(Gtk.Label(label=label))
            button.set_child(inner)
            if value == "cloud" and not chosen.get("cloud_ready"):
                button.set_sensitive(False)
                button.set_tooltip_text("Set up Ollama Cloud in CLIVE settings first")
            button.connect("clicked", lambda _b, v=value: self._select_model(v, None))
            toggles.append(button)
        box.append(toggles)
        for value, title in (("cloud", "Cloud models"), ("local", "Local models")):
            heading = Gtk.Label(label=title, xalign=0, margin_top=4)
            heading.add_css_class("heading")
            box.append(heading)
            listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            listbox.add_css_class("boxed-list")
            active_name = chosen.get(f"{value}_model")
            for name in chosen.get(f"{value}_models", []):
                row = Adw.ActionRow(title=name, activatable=True)
                if endpoint == value and name == active_name:
                    row.add_suffix(Gtk.Image(icon_name="object-select-symbolic"))
                row.set_sensitive(value == "local" or bool(chosen.get("cloud_ready")))
                row.connect("activated", lambda _r, v=value, n=name: self._select_model(v, n))
                listbox.append(row)
            box.append(listbox)
        if not chosen.get("cloud_ready"):
            hint = Gtk.Label(label="Ollama Cloud needs an API key and a one-time billing "
                                   "confirmation in CLIVE settings.",
                             wrap=True, max_width_chars=34, xalign=0)
            hint.add_css_class("dim-label")
            box.append(hint)
        manage = Gtk.Button(label="Manage models…")
        manage.add_css_class("flat")
        manage.connect("clicked", lambda *_: (self.model_popover.popdown(),
                                              self.open_settings("models")))
        box.append(manage)
        self.model_popover.set_child(box)

    def _select_model(self, endpoint, model):
        self.model_popover.popdown()

        def done(result, error):
            if error:
                self._toast(error)
            elif isinstance(result, dict):
                self.state = {**self.state, "selection": result}
                self._update_mode()
        self.client.call("model_select", done, endpoint=endpoint,
                         **({"model": model} if model else {}))

    def open_settings(self, section=""):
        self.settings_dialog = CliveSettings(self, section)
        self.settings_dialog.present(self)

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
        column.append(self._build_confirmation())
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

    def _build_confirmation(self):
        """The stop no approval mode skips: what will happen, and Confirm beside it."""
        heading = Gtk.Label(label="Confirm before CLIVE continues", xalign=0)
        heading.add_css_class("heading")
        heading.add_css_class("warning")
        self.confirm_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        decline = Gtk.Button(label="Don\u2019t do it")
        decline.connect("clicked", lambda *_: self._answer_confirmation(False))
        confirm = Gtk.Button(label="Confirm")
        confirm.add_css_class("suggested-action")
        confirm.connect("clicked", lambda *_: self._answer_confirmation(True))
        buttons.append(decline)
        buttons.append(confirm)
        self.confirmation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self.confirmation.add_css_class("approval-card")
        for child in (heading, self.confirm_rows, buttons):
            self.confirmation.append(child)
        self._confirm_signature = None
        return self.confirmation

    def _answer_confirmation(self, approve):
        calls = (self.state.get("confirmation") or {}).get("calls", [])
        self.client.call("confirm", self._result, id=self.state.get("id"),
                         approved=[call["id"] for call in calls] if approve else [])

    def _render_confirmation(self, state):
        waiting = state.get("status") == "awaiting_confirmation"
        self.confirmation.set_visible(waiting)
        signature = json.dumps(state.get("confirmation"), sort_keys=True)
        if not waiting or signature == self._confirm_signature:
            return
        self._confirm_signature = signature
        while child := self.confirm_rows.get_first_child():
            self.confirm_rows.remove(child)
        for call in (state.get("confirmation") or {}).get("calls", []):
            title = Gtk.Label(label=call.get("capability") or call.get("label") or call.get("tool", ""),
                              xalign=0, wrap=True)
            title.add_css_class("heading")
            self.confirm_rows.append(title)
            detail = " · ".join(part for part in (call.get("app_name"), call.get("target")) if part)
            if detail:
                label = Gtk.Label(label=detail, xalign=0, wrap=True, selectable=True)
                label.add_css_class("dim-label")
                self.confirm_rows.append(label)

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
    #
    # The staged files live in the CLIVE service, not here: the desktop card
    # and this chat stage into the same list, so a file dropped here shows on
    # the card and the other way round. The service reads each file as it is
    # added and names any it cannot take, so nothing waits until Send to fail.

    def _stage(self, paths, skipped=()):
        for name in skipped:
            self._toast(f"{name} is on a network location CLIVE cannot read directly; copy it "
                        "to your computer first")
        if not paths:
            return

        def staged(result, error):
            if error:
                self._toast(error)
                return
            for problem in result.get("errors", []):
                self._toast(problem)
            self._fill_attachments(result.get("draft", []))
        self.client.call("draft_add", staged, paths=list(paths))

    def _choose_attachments(self, *_args):
        # Held so the picker can be driven from the UI smoke test.
        self.picker = pick_files(self, "Attach files", True, self._stage)

    def _fill_attachments(self, draft=None):
        draft = self.state.get("draft", []) if draft is None else draft
        self.attachments = list(draft)
        signature = json.dumps([item.get("id") for item in draft])
        if signature == getattr(self, "_draft_signature", None):
            return
        self._draft_signature = signature
        while child := self.attachment_box.get_first_child():
            self.attachment_box.remove(child)
        self.attachment_box.set_visible(bool(draft))
        for item in draft:
            self.attachment_box.append(self._attachment_chip(item))

    def _attachment_chip(self, item):
        chip = Gtk.Box(spacing=6)
        chip.add_css_class("attachment-chip")
        picture = texture_from_base64(item.get("thumbnail", ""))
        if picture is not None:
            chip.append(Gtk.Picture(paintable=picture, can_shrink=True, width_request=28,
                                    height_request=28, content_fit=Gtk.ContentFit.COVER))
        else:
            chip.append(Gtk.Image(icon_name="image-x-generic-symbolic" if item["kind"] == "image"
                                  else "text-x-generic-symbolic"))
        details = " · ".join(part for part in (item.get("label"), size_label(item.get("size", 0)),
                                               f"{item['pages']} pages" if item.get("pages") else "")
                             if part)
        opener = Gtk.MenuButton(child=Gtk.Label(label=item["name"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                                                max_width_chars=22), tooltip_text=details)
        opener.add_css_class("flat")
        opener.set_popover(self._attachment_preview(item, details))
        chip.append(opener)
        chip.append(icon_button("window-close-symbolic", "Remove this file",
                                lambda _b, value=item["id"]: self._drop_attachment(value)))
        return chip

    @staticmethod
    def _attachment_preview(item, details):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8,
                      margin_bottom=8, margin_start=8, margin_end=8)
        title = Gtk.Label(label=item["name"], xalign=0, wrap=True)
        title.add_css_class("heading")
        box.append(title)
        box.append(Gtk.Label(label=details, xalign=0, css_classes=["dim-label"]))
        picture = texture_from_base64(item.get("thumbnail", ""))
        if picture is not None:
            box.append(Gtk.Picture(paintable=picture, can_shrink=True, width_request=192,
                                   height_request=192, content_fit=Gtk.ContentFit.CONTAIN))
        if item.get("preview"):
            preview = Gtk.Label(label=item["preview"] + ("…" if item.get("chars", 0) > len(item["preview"])
                                                         else ""),
                                xalign=0, wrap=True, max_width_chars=48, selectable=True)
            preview.add_css_class("monospace-dim")
            box.append(preview)
        return Gtk.Popover(child=box)

    def _drop_attachment(self, identifier):
        self.client.call("draft_remove", lambda result, error: self._toast(error) if error
                         else self._fill_attachments(result.get("draft", [])), id=identifier)

    def _install_drop_and_paste(self):
        """Files can be dropped anywhere on the chat, and pasted into the composer."""
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("enter", lambda *_: (self.add_css_class("drop-hover"), Gdk.DragAction.COPY)[1])
        drop.connect("leave", lambda *_: self.remove_css_class("drop-hover"))
        drop.connect("drop", self._dropped)
        self.add_controller(drop)

    def _dropped(self, _target, value, _x, _y):
        self.remove_css_class("drop-hover")
        files = value.get_files() if hasattr(value, "get_files") else []
        paths = [f.get_path() for f in files if f.get_path()]
        skipped = [f.get_basename() or f.get_uri() for f in files if not f.get_path()]
        self._stage(paths, skipped)
        return True

    def _paste(self):
        """Ctrl+V with files or an image on the clipboard attaches them."""
        clipboard = self.get_clipboard()
        formats = clipboard.get_formats()
        if formats.contain_gtype(Gdk.FileList):
            def got_files(source, result):
                try:
                    value = source.read_value_finish(result)
                except GLib.Error:
                    return
                files = value.get_files()
                self._stage([f.get_path() for f in files if f.get_path()],
                            [f.get_basename() for f in files if not f.get_path()])
            clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got_files)
            return True
        if formats.contain_gtype(Gdk.Texture) and not formats.contain_mime_type("text/plain"):
            def got_image(source, result):
                try:
                    texture = source.read_texture_finish(result)
                except GLib.Error:
                    return
                if texture is not None:
                    self._stage([save_pasted_image(texture.save_to_png_bytes().get_data())])
            clipboard.read_texture_async(None, got_image)
            return True
        return False

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
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and modifier & Gdk.ModifierType.CONTROL_MASK:
            # Files or an image on the clipboard attach; text pastes as usual.
            return Gdk.EVENT_STOP if self._paste() else Gdk.EVENT_PROPAGATE
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

    def _toast(self, message):
        self.toast_overlay.add_toast(Adw.Toast(title=markup(message)))

    def _review_followups(self, *_args):
        self._followups_dismissed = (self.state.get("followups") or {}).get("checked", 0)
        self.followup_banner.set_revealed(False)
        self._set_composer(FOLLOWUP_PROMPT)

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
        # A running task reports the model it actually reached (a fallback
        # included); the rest of the time the header says what is selected.
        running = self.state.get("status") in ACTIVE
        chosen = self.state.get("selection") or {}
        if running and self.state.get("model"):
            cloud = self.state.get("mode") == "cloud"
            model = self.state.get("model")
        elif chosen:
            cloud = chosen.get("endpoint") == "cloud"
            model = chosen.get("cloud_model" if cloud else "local_model")
        else:
            cloud = bool(self.settings.get("cloud_enabled"))
            model = self.settings.get("cloud_model" if cloud else "local_model")
        self.mode_icon.set_from_icon_name("weather-overcast-symbolic" if cloud else "computer-symbolic")
        self.mode.set_text(("Cloud" if cloud else "Local") + (" · " + model if model else ""))

    def _send(self, *_args):
        text = self._composer_text().strip()
        # Staged files on their own are a message too.
        if (not text and not self.attachments) or not self.send.get_sensitive():
            return
        def sent(result, error):
            self._result(result, error)
            if not error:
                self.buffer.set_text("")
                self._fill_attachments([])
                self._history()
        self.client.call("submit", sent, message=text,
                         conversation=self.state.get("conversation", ""), use_draft=True)

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
        found = state.get("followups") or {}
        if found.get("count") and found.get("checked", 0) > self._followups_dismissed:
            count = found["count"]
            self.followup_banner.set_title(f"{count} email{'s' if count != 1 else ''} may need a reply")
            self.followup_banner.set_revealed(True)
        else:
            self.followup_banner.set_revealed(False)
        dialog = getattr(self, "settings_dialog", None)
        if dialog is not None and state.get("pull"):
            dialog.saved_models.pull_progress(state["pull"])
        active = status in ACTIVE
        self._render_confirmation(state)
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
        self._fill_attachments(state.get("draft", []))
        self.attach.set_sensitive(not active)
        self.entry.set_sensitive(not active)
        self.history_dropdown.set_sensitive(not active)
        messages = state.get("messages", [])
        actions = state.get("actions", [])
        if not (state.get("preferences") or {}).get("show_action_details", True):
            actions = []
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
            content = message["content"]
            files = message.get("attachments") or []
            if files:
                # The stored turn names its files for the model; here they show as chips.
                content = content.rsplit("\n\nAttached: ", 1)[0]
            label = Gtk.Label(wrap=True, xalign=0, selectable=True, max_width_chars=64,
                              halign=Gtk.Align.END if mine else Gtk.Align.START)
            label.set_markup(linked_text(content))
            label.add_css_class("chat-bubble")
            if mine:
                label.add_css_class("chat-bubble-mine")
            self.chat.append(label)
            if files:
                row = Gtk.Box(spacing=4, halign=Gtk.Align.END if mine else Gtk.Align.START)
                for item in files:
                    chip = Gtk.Box(spacing=4)
                    chip.add_css_class("attachment-chip")
                    chip.append(Gtk.Image(icon_name="image-x-generic-symbolic" if item["kind"] == "image"
                                          else "text-x-generic-symbolic"))
                    chip.append(Gtk.Label(label=item["name"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                                          max_width_chars=24, tooltip_text=item.get("label", "")))
                    row.append(chip)
                self.chat.append(row)

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
        """What CLIVE did, one row per action, naming the app it used."""
        while child := self.task_log.get_first_child():
            self.task_log.remove(child)
        self.task_log.set_visible(bool(actions))
        if not actions:
            return
        apps = list(dict.fromkeys(a.get("app_name") for a in actions if a.get("app_name")))
        summary = Gtk.Label(xalign=0, wrap=True, label=(
            (f"Used {', '.join(apps)} · " if apps else "") +
            f"{len(actions)} action{'s' if len(actions) != 1 else ''}"))
        summary.add_css_class("dim-label")
        self.task_log.append(summary)
        for action in actions[-20:]:
            self.task_log.append(self._action_row(action))

    def _action_row(self, action):
        status = action.get("status", "done")
        what = action.get("capability") or action["tool"].replace("_", " ").capitalize()
        title = " · ".join(part for part in (action.get("app_name"), what) if part)
        mark = {"blocked": "Blocked: ", "declined": "Declined: "}.get(status, "")
        label = mark + title + (f" — {action['target']}" if action.get("target") else "")
        text = action.get("preview") if action.get("result") is None and action.get("preview") \
            else json.dumps(action.get("result"), ensure_ascii=False, indent=2)[:3000]
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=12, margin_top=4)
        result = Gtk.Label(xalign=0, wrap=True, selectable=True, label=text or "")
        result.add_css_class("monospace-dim")
        body.append(result)
        if action.get("truncated") and action.get("call"):
            more = Gtk.Button(label="Show the full result", halign=Gtk.Align.START)
            more.add_css_class("flat")

            def shown(detail, error, label=result, button=more):
                if error:
                    self._toast(error)
                    return
                label.set_label(json.dumps(detail.get("result"), ensure_ascii=False, indent=2))
                button.set_visible(False)

            more.connect("clicked", lambda *_: self.client.call("action_detail", shown,
                                                                call=action["call"]))
            body.append(more)
        expander = Gtk.Expander(label=label, child=body)
        if status in ("blocked", "declined"):
            expander.add_css_class("warning")
        return expander

    def _scroll_bottom(self):
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE


class SavedModelsGroup(Adw.PreferencesGroup):
    """The models the switchers offer: add, remove, and download local ones."""

    def __init__(self, dialog):
        super().__init__(title="Models in the switcher",
                         description="The model button in the chat and on the desktop card "
                                     "lists these. Changes here apply at once.")
        self.dialog = dialog
        self.client = dialog.client
        self.rows = []
        self._last_pull = None
        self.adder = Adw.EntryRow(title="Add a model by name, for example llama3.2:3b")
        self.endpoint = Gtk.DropDown(model=Gtk.StringList.new(["Local", "Cloud"]),
                                     valign=Gtk.Align.CENTER)
        self.adder.add_suffix(self.endpoint)
        add = Gtk.Button(label="Add", valign=Gtk.Align.CENTER)
        add.connect("clicked", lambda *_: self._add())
        self.adder.add_suffix(add)
        self.adder.connect("entry-activated", lambda *_: self._add())
        self.add(self.adder)
        self.download = Adw.ActionRow(title="Download a local model",
                                      subtitle="Uses the name above. Local models are several gigabytes.")
        self.download.set_subtitle_lines(0)
        get = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        get.connect("clicked", lambda *_: self._pull())
        self.download.add_suffix(get)
        self.add(self.download)

    def fill(self, settings):
        for row in self.rows:
            self.remove(row)
        self.rows = []
        for endpoint, label in (("local", "Local"), ("cloud", "Cloud")):
            active = settings.get(f"{endpoint}_model")
            for name in settings.get(f"{endpoint}_models", []) or [active]:
                row = Adw.ActionRow(title=markup(name), subtitle=label + (" · in use" if name == active else ""))
                if name != active:
                    row.add_suffix(icon_button("user-trash-symbolic", "Remove from the switcher",
                                               lambda _b, e=endpoint, n=name: self._remove(e, n)))
                self.rows.append(row)
                self.add(row)

    def _refresh(self, result, error):
        if error:
            self.dialog._toast(error)
            return
        def reloaded(settings, error):
            if not error:
                self.fill(settings)
                self.dialog._on_saved_models(settings)
        self.client.call("settings", reloaded)
        self.dialog.page.load_settings()

    def _add(self):
        name = self.adder.get_text().strip()
        if not name:
            return
        endpoint = ("local", "cloud")[self.endpoint.get_selected()]
        self.client.call("model_add", self._refresh, endpoint=endpoint, model=name)
        self.adder.set_text("")

    def _remove(self, endpoint, name):
        self.client.call("model_remove", self._refresh, endpoint=endpoint, model=name)

    def _pull(self):
        name = self.adder.get_text().strip()
        if not name:
            self.dialog._toast("Type the model's name above first")
            return
        def started(_result, error):
            self.download.set_subtitle(markup(error) if error else
                                       f"Downloading {markup(name)}… it is added when done.")
        self.client.call("model_pull", started, model=name)

    def pull_progress(self, pull):
        if not pull:
            return
        # The service keeps reporting a finished download with every state
        # update; react to the change, not to each repeat of it.
        seen = (pull.get("model"), pull.get("status"))
        if seen == self._last_pull and pull.get("status") in ("done", "failed"):
            return
        self._last_pull = seen
        if pull.get("status") == "failed":
            self.download.set_subtitle(markup(pull.get("error", "The download failed")))
        elif pull.get("status") == "done":
            self.download.set_subtitle(f"{markup(pull['model'])} is ready and in the switcher")
            self._refresh({}, None)
            # Now installed, so the Local model dropdown can say so.
            self.dialog._list_models()
        elif pull.get("total"):
            percent = round(100 * (pull.get("completed") or 0) / pull["total"])
            self.download.set_subtitle(f"Downloading {markup(pull['model'])}: {percent}%")


class CliveSettings(Adw.Dialog):
    """Models, credential and history.

    Everything the service can store is editable here, each control says what it
    does, and the only thing that saves is the Save button -- so a half-finished
    edit is never mistaken for a stored setting.
    """

    def __init__(self, page, section=""):
        super().__init__(title="CLIVE Settings", content_width=900, content_height=720)
        self.page = page
        self.client = page.client
        self.saved = {}
        self.confirmed = False
        self.unattended = False
        self.context_files = []
        self.file_rows = []
        self._loading = True
        self.pages = {}

        # -- AI Models ---------------------------------------------------------
        models = self._section("models")
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
            subtitle="Reason on Ollama's servers instead of this machine. The model "
                     "button in the chat and on the desktop card switches this too.")
        self.enabled.connect("notify::active", self._cloud_toggled)
        cloud_group.add(self.enabled)
        # What each dropdown offers: the saved switcher list, and what Ollama
        # last said it can serve (None until it answers).
        self.model_lists = {"cloud": [], "local": []}
        self.model_choices = {"cloud": None, "local": None}
        self.cloud = self._model_row("cloud", "Cloud model")
        cloud_group.add(self.cloud)
        models.add(cloud_group)

        local_group = Adw.PreferencesGroup(
            title="Local model",
            description="Runs on this machine and is used whenever the cloud is off or "
                        "unavailable. The list shows the models Ollama has installed; "
                        "download more under Models in the switcher.")
        self.local = self._model_row("local", "Local model")
        local_group.add(self.local)
        self.fallback = Adw.SwitchRow(
            title="Fall back to the local model",
            subtitle="When the cloud is unreachable or out of allowance, finish the task locally")
        self.fallback.connect("notify::active", self._changed)
        local_group.add(self.fallback)
        models.add(local_group)
        self.saved_models = SavedModelsGroup(self)
        models.add(self.saved_models)

        actions = Adw.PreferencesGroup(title="Checks")
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
        models.add(actions)

        # -- App Access: switches apply at once, no Save needed --------------
        self.access = AppAccessPage(self.client, self._toast)
        self.pages["access"] = self.access

        # -- App Permissions --------------------------------------------------
        permissions = self._section("permissions")
        approval = Adw.PreferencesGroup(
            title="Task approval",
            description="CLIVE previews a task and waits for Approve before it acts. "
                        "Choose how much of that waiting to skip. Whatever you choose, "
                        "CLIVE still refuses apps switched off in App Access and anything "
                        "outside the task's own files, apps and tools.")
        self.approval = Adw.ComboRow(title="Ask before acting",
                                     model=Gtk.StringList.new(list(APPROVAL_LABELS)))
        self.approval.set_subtitle(APPROVAL_HINTS[0])
        self.approval.set_subtitle_lines(0)
        self.approval.connect("notify::selected", self._approval_selected)
        approval.add(self.approval)
        permissions.add(approval)
        self.ask_first = AskFirstGroup(self.client, self._toast)
        permissions.add(self.ask_first)

        # -- Attachments --------------------------------------------------------
        attachments = self._section("attachments")
        self.files_group = Adw.PreferencesGroup(
            title="Always-attached files",
            description=f"Sent with every task, up to {MAX_FILES} files. Text is truncated "
                        f"at {MAX_TEXT:,} characters, and images need a model that reports "
                        "vision — Test under AI Models says whether yours does. Files must be "
                        "in your home folder and outside hidden folders.")
        self.add_file = Adw.ButtonRow(title="Add files…", start_icon_name="list-add-symbolic")
        self.add_file.connect("activated", self._add_context_file)
        self.files_group.add(self.add_file)
        attachments.add(self.files_group)
        about = Adw.PreferencesGroup(
            title="Attaching to one message",
            description="Use the paperclip in the chat or on the desktop card. Attached files "
                        "are yours to hand over, so App Access switches do not apply to them.")
        attachments.add(about)

        # -- Memory & Context --------------------------------------------------
        memory = self._section("memory")
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
        memory.add(instructions)
        history = Adw.PreferencesGroup(title="Conversation memory")
        low, high = RANGES["context_turns"]
        self.memory_turns = Adw.SpinRow.new_with_range(low, high, 1)
        self.memory_turns.set_title("Messages remembered")
        self.memory_turns.set_subtitle("Earlier messages in the chat CLIVE reads with each new one")
        self.memory_turns.connect("notify::value", self._changed)
        history.add(self.memory_turns)
        low, high = RANGES["history_days"]
        self.history_days = Adw.SpinRow.new_with_range(low, high, 1)
        self.history_days.set_title("Keep chat history for")
        self.history_days.set_subtitle("Days; 0 keeps every conversation until you delete it")
        self.history_days.connect("notify::value", self._changed)
        history.add(self.history_days)
        clear = Adw.ButtonRow(title="Clear all chat history")
        clear.add_css_class("destructive-action")
        clear.connect("activated", self._clear)
        history.add(clear)
        memory.add(history)

        # -- Appearance ----------------------------------------------------------
        appearance = self._section("appearance")
        look = Adw.PreferencesGroup(title="Chat appearance",
                                    description="Card size, colors and blur are under Widgets.")
        self.density = Adw.ComboRow(title="Desktop card density",
                                    subtitle="Compact fits more conversation on a small card",
                                    model=Gtk.StringList.new(["Comfortable", "Compact"]))
        self.density.connect("notify::selected", self._changed)
        look.add(self.density)
        self.action_details = Adw.SwitchRow(
            title="Show action details",
            subtitle="List each action CLIVE took, with the app it used, under its answer")
        self.action_details.connect("notify::active", self._changed)
        look.add(self.action_details)
        appearance.add(look)

        # -- Notifications -------------------------------------------------------
        notifications = self._section("notifications")
        notify = Adw.PreferencesGroup(
            title="Tell me when",
            description="Only while the desktop card is covered or hidden; when you can see "
                        "the card, it already shows what happened.")
        self.notify_waiting = Adw.SwitchRow(title="CLIVE needs you",
                                            subtitle="A task preview or a confirmation is waiting")
        self.notify_waiting.connect("notify::active", self._changed)
        notify.add(self.notify_waiting)
        self.notify_finished = Adw.SwitchRow(title="A task finishes or stops",
                                             subtitle="Including when it paused on an error")
        self.notify_finished.connect("notify::active", self._changed)
        notify.add(self.notify_finished)
        notifications.add(notify)

        # -- Automation ----------------------------------------------------------
        automation = self._section("automation")
        limits = Adw.PreferencesGroup(title="Task limits")
        low, high = RANGES["max_turns"]
        self.turns = Adw.SpinRow.new_with_range(low, high, 1)
        self.turns.set_title("Maximum task steps")
        self.turns.set_subtitle(f"Tool rounds before CLIVE stops and reports ({low}–{high})")
        self.turns.connect("notify::value", self._changed)
        limits.add(self.turns)
        automation.add(limits)
        followups = Adw.PreferencesGroup(
            title="Follow-ups",
            description="CLIVE can check your connected email accounts for messages that may need "
                        "a reply, and tell you. It reads only message headers, sends nothing to a "
                        "model, and skips accounts whose follow-up tracking is off in App Access.")
        self.followup_hours = Adw.ComboRow(title="Check email for follow-ups",
                                           model=Gtk.StringList.new(list(FOLLOWUP_LABELS)))
        self.followup_hours.connect("notify::selected", self._changed)
        followups.add(self.followup_hours)
        low, high = RANGES["followup_days"]
        self.followup_days = Adw.SpinRow.new_with_range(low, high, 1)
        self.followup_days.set_title("Look back")
        self.followup_days.set_subtitle("Days of mail to consider")
        self.followup_days.connect("notify::value", self._changed)
        followups.add(self.followup_days)
        automation.add(followups)

        # -- Privacy ---------------------------------------------------------------
        privacy = self._section("privacy")
        cloud_data = Adw.PreferencesGroup(
            title="What cloud models may see",
            description="Applies only while a cloud model is in use. Local models run on this "
                        "machine and see everything a task gathers. When something is withheld, "
                        "CLIVE is told so rather than guessing.")
        self.cloud_images = Adw.SwitchRow(title="Images and screenshots",
                                          subtitle="App screenshots and attached pictures")
        self.cloud_images.connect("notify::active", self._changed)
        cloud_data.add(self.cloud_images)
        self.cloud_attachments = Adw.SwitchRow(title="Attached files",
                                               subtitle="The text of files you attach")
        self.cloud_attachments.connect("notify::active", self._changed)
        cloud_data.add(self.cloud_attachments)
        privacy.add(cloud_data)
        records = Adw.PreferencesGroup(
            title="Records",
            description="CLIVE keeps its chats, actions and when it last used each app on this "
                        "machine only.")
        clear_usage = Adw.ButtonRow(title="Clear when apps were last used")
        clear_usage.connect("activated", self._clear_usage)
        records.add(clear_usage)
        privacy.add(records)

        # -- Advanced ------------------------------------------------------------------
        advanced = self._section("advanced")
        model_limits = Adw.PreferencesGroup(title="Local model")
        low, high = RANGES["local_context"]
        self.context = Adw.SpinRow.new_with_range(low, high, 1024)
        self.context.set_title("Local context window")
        self.context.set_subtitle(f"Tokens the local model can hold ({low}–{high}); larger uses more memory")
        self.context.connect("notify::value", self._changed)
        model_limits.add(self.context)
        advanced.add(model_limits)
        diagnostics = Adw.PreferencesGroup(title="Diagnostics")
        self.debug = Adw.SwitchRow(
            title="Log model replies",
            subtitle="Writes a shortened copy of every planning reply to the service journal")
        self.debug.connect("notify::active", self._changed)
        diagnostics.add(self.debug)
        restart = Adw.ButtonRow(title="Restart the CLIVE service")
        restart.connect("activated", self._restart_service)
        diagnostics.add(restart)
        advanced.add(diagnostics)

        self.set_child(self._build_layout(section))
        self.set_can_close(False)
        self.connect("close-attempt", self._closing)
        self.client.call("settings", self._loaded)
        self._list_models()

    # -- layout --------------------------------------------------------------

    def _section(self, key):
        page = Adw.PreferencesPage()
        self.pages[key] = page
        return page

    def _build_layout(self, section):
        """Sections down the side, the chosen one beside them; one column when narrow."""
        self.window_title = Adw.WindowTitle(title="", subtitle="")
        header = Adw.HeaderBar(title_widget=self.window_title)
        self.save_button = Gtk.Button(label="Save", sensitive=False)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self._save)
        header.pack_end(self.save_button)
        self.banner = Adw.Banner(revealed=False)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for key, _title, _icon in SECTIONS:
            self.stack.add_named(self.pages[key], key)
        content_view = Adw.ToolbarView(content=self.stack)
        content_view.add_top_bar(header)
        content_view.add_top_bar(self.banner)
        self.toasts = Adw.ToastOverlay(child=content_view)
        self.content_page = Adw.NavigationPage(title="", child=self.toasts)

        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class("navigation-sidebar")
        for key, title, icon in SECTIONS:
            box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            row = Gtk.ListBoxRow(child=box, name=key)
            self.sidebar.append(row)
        self.sidebar.connect("row-selected", self._section_selected)
        sidebar_view = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=self.sidebar, hscrollbar_policy=Gtk.PolicyType.NEVER))
        sidebar_view.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="CLIVE Settings", child=sidebar_view),
            content=self.content_page, min_sidebar_width=200, max_sidebar_width=240)
        breakpoint_ = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)
        keys = [key for key, _t, _i in SECTIONS]
        self.show_section(section if section in keys else "models")
        return self.split

    def show_section(self, key):
        index = [k for k, _t, _i in SECTIONS].index(key)
        self.sidebar.select_row(self.sidebar.get_row_at_index(index))

    def _section_selected(self, _list, row):
        if row is None:
            return
        key = row.get_name()
        title = dict((k, t) for k, t, _i in SECTIONS)[key]
        self.stack.set_visible_child_name(key)
        self.window_title.set_title(title)
        self.content_page.set_title(title)
        # App Access and its permissions apply the moment they change; the
        # Save button belongs to the form-like sections only.
        self.save_button.set_visible(key != "access")
        if key == "access":
            self.access.reload()
        elif key == "permissions":
            self.ask_first.reload()
        self.split.set_show_content(True)

    # -- state -------------------------------------------------------------

    def _values(self):
        # get_selected() answers GTK_INVALID_LIST_POSITION when nothing is
        # selected, which must not index past the end of the mode list.
        index = self.approval.get_selected()
        return {"cloud_model": self._model_value(self.cloud),
                "local_model": self._model_value(self.local),
                "cloud_enabled": self.enabled.get_active(),
                "free_account_confirmed": self.confirmed,
                "local_context": int(self.context.get_value()),
                "max_turns": int(self.turns.get_value()),
                "approval_mode": APPROVAL_MODES[index] if index < len(APPROVAL_MODES) else "always",
                "system_prompt": self._prompt_text().strip(),
                "context_files": list(self.context_files),
                "fallback_to_local": self.fallback.get_active(),
                "card_density": DENSITIES[min(self.density.get_selected(), len(DENSITIES) - 1)],
                "show_action_details": self.action_details.get_active(),
                "notify_finished": self.notify_finished.get_active(),
                "notify_waiting": self.notify_waiting.get_active(),
                "cloud_images": self.cloud_images.get_active(),
                "cloud_attachments": self.cloud_attachments.get_active(),
                "context_turns": int(self.memory_turns.get_value()),
                "history_days": int(self.history_days.get_value()),
                "debug_logging": self.debug.get_active(),
                "followup_hours": FOLLOWUP_HOURS[min(self.followup_hours.get_selected(),
                                                     len(FOLLOWUP_HOURS) - 1)],
                "followup_days": int(self.followup_days.get_value())}

    def _prompt_text(self):
        buffer = self.prompt_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    # -- model dropdowns ---------------------------------------------------

    def _model_row(self, endpoint, title):
        row = Adw.ComboRow(title=title, enable_search=True, model=Gtk.StringList.new([]),
                           expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        row.set_subtitle_lines(0)
        row.connect("notify::selected", lambda *_: (self._model_status(endpoint), self._changed()))
        row.add_suffix(icon_button("view-refresh-symbolic", "Ask Ollama again",
                                   lambda *_: self._list_models()))
        return row

    @staticmethod
    def _model_value(row):
        item = row.get_selected_item()
        return item.get_string() if item is not None else ""

    def _fill_model_row(self, endpoint, value):
        """Offer the saved and available models, keeping `value` selected.

        Replacing the list resets the selection, so it is rebuilt as a load:
        an answer from Ollama arriving late must neither dirty the dialog nor
        quietly change which model Save would store.
        """
        row = self.cloud if endpoint == "cloud" else self.local
        choices = self.model_choices[endpoint]
        # A string is the reason Ollama could not be asked, not a model list.
        names = set(self.model_lists[endpoint]) | set(choices if isinstance(choices, list) else ())
        if value:
            names.add(value)
        names = sorted(names)
        loading, self._loading = self._loading, True
        row.set_model(Gtk.StringList.new(names))
        if value in names:
            row.set_selected(names.index(value))
        self._loading = loading
        self._model_status(endpoint)
        self._sync_dirty()

    def _model_status(self, endpoint):
        row = self.cloud if endpoint == "cloud" else self.local
        choices = self.model_choices[endpoint]
        name = self._model_value(row)
        if choices is None:
            subtitle = "Checking what Ollama has…"
        elif isinstance(choices, str):
            subtitle = choices
        elif endpoint == "local":
            subtitle = ("Installed on this machine" if name in choices else
                        "Not downloaded yet — use Download under Models in the switcher")
        else:
            subtitle = "Available on Ollama Cloud" if name in choices else "Not listed by Ollama Cloud"
        row.set_subtitle(markup(subtitle))

    def _list_models(self):
        for endpoint in ("cloud", "local"):
            def listed(result, error, endpoint=endpoint):
                row = self.cloud if endpoint == "cloud" else self.local
                # An error is kept as text: the dropdown still offers the
                # saved models, and the subtitle says why nothing else shows.
                self.model_choices[endpoint] = error or list((result or {}).get("models", []))
                self._fill_model_row(endpoint, self._model_value(row) or self.saved.get(f"{endpoint}_model", ""))
            self.model_choices[endpoint] = None
            self._model_status(endpoint)
            self.client.call("models_available", listed, endpoint=endpoint)

    def _on_saved_models(self, settings):
        """The switcher list changed: offer its models without moving the selection."""
        for endpoint in ("cloud", "local"):
            row = self.cloud if endpoint == "cloud" else self.local
            self.model_lists[endpoint] = list(settings.get(f"{endpoint}_models", []))
            self._fill_model_row(endpoint, self._model_value(row) or settings.get(f"{endpoint}_model", ""))

    def _loaded(self, result, error):
        if error:
            # Leave the dialog usable rather than frozen mid-load: the rows keep
            # whatever they hold, and dirty tracking starts from there.
            self._loading = False
            self.saved = self._values()
            self._say(error)
            return
        self._loading = True
        for endpoint in ("cloud", "local"):
            self.model_lists[endpoint] = list(result.get(f"{endpoint}_models", []))
            self._fill_model_row(endpoint, result[f"{endpoint}_model"])
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
        # Defaults for each, for the same reason as approval_mode above.
        self.fallback.set_active(result.get("fallback_to_local", True))
        density = result.get("card_density", "comfortable")
        self.density.set_selected(DENSITIES.index(density) if density in DENSITIES else 0)
        self.action_details.set_active(result.get("show_action_details", True))
        self.notify_finished.set_active(result.get("notify_finished", True))
        self.notify_waiting.set_active(result.get("notify_waiting", True))
        self.cloud_images.set_active(result.get("cloud_images", True))
        self.cloud_attachments.set_active(result.get("cloud_attachments", True))
        self.memory_turns.set_value(result.get("context_turns", 12))
        self.history_days.set_value(result.get("history_days", 0))
        self.debug.set_active(result.get("debug_logging", False))
        hours = result.get("followup_hours", 0)
        self.followup_hours.set_selected(FOLLOWUP_HOURS.index(hours) if hours in FOLLOWUP_HOURS else 0)
        self.followup_days.set_value(result.get("followup_days", 7))
        self.saved_models.fill(result)
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
                         ("context_files", self.files_group), ("api_key", self.key),
                         ("context_turns", self.memory_turns), ("history_days", self.history_days)):
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
        def chosen(paths, skipped=()):
            for name in skipped:
                self._toast(f"{name} is on a network location CLIVE cannot read directly")
            for path in paths:
                if path not in self.context_files and len(self.context_files) < MAX_FILES:
                    self.context_files.append(path)
            self._fill_context_files()
            self._sync_dirty()

        if len(self.context_files) >= MAX_FILES:
            self._toast(f"CLIVE attaches at most {MAX_FILES} files to every task")
            return
        # Held so the picker can be driven from the UI smoke test.
        self.picker = pick_files(self, "Attach files to every task", True, chosen)

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

    def _clear_usage(self, *_args):
        def cleared(_result, error):
            self._toast(error or "Cleared when apps were last used")
        self.client.call("usage_clear", cleared)

    def _restart_service(self, *_args):
        def restart():
            try:
                Gio.Subprocess.new(["systemctl", "--user", "restart", "desktop-forge-clive.service"],
                                   Gio.SubprocessFlags.NONE)
            except GLib.Error as error:
                self._toast(f"Could not restart CLIVE: {error.message}")
                return
            self._toast("CLIVE is restarting")
        confirm(self, "Restart the CLIVE service?",
                "A running task stops, and stays paused after the restart.", "Restart", restart)

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
