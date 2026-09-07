"""Create and manage reminders.

The daemon fires the notifications; this is purely the editor for the store
they both read.
"""
from __future__ import annotations

import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..providers import reminders as store

REPEAT_LABELS = ["Never", "Daily", "Weekly", "Monthly"]
REPEAT_VALUES = ["none", "daily", "weekly", "monthly"]


class RemindersPage(Adw.PreferencesPage):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__()
        self._toasts = toast_overlay
        self._rows: list[Gtk.Widget] = []

        self._group = Adw.PreferencesGroup(
            title="Reminders",
            description="You are notified when one comes due, whether or not the widget is showing.",
        )
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text("New reminder")
        add.connect("clicked", lambda _b: _ReminderDialog(self).present(self.get_root()))
        self._group.set_header_suffix(add)
        self.add(self._group)

        self.refresh()

    def refresh(self) -> None:
        for row in self._rows:
            self._group.remove(row)
        self._rows.clear()

        reminders = sorted(store.load(), key=lambda r: r.get("due") or "")
        pending = [r for r in reminders if not r.get("done")]

        if not pending:
            row = Adw.ActionRow(
                title="No reminders",
                subtitle="Use + to add one.",
            )
            self._group.add(row)
            self._rows.append(row)
            return

        now = datetime.datetime.now()
        for reminder in pending:
            due = store.parse_due(reminder)
            overdue = due is not None and due < now

            row = Adw.ActionRow(
                title=GLib.markup_escape_text(reminder.get("text", "")),
                subtitle=_describe(due, reminder.get("repeat", "none"), overdue),
            )
            if overdue:
                icon = Gtk.Image(icon_name="alarm-symbolic", valign=Gtk.Align.CENTER)
                icon.add_css_class("issue-error")
                row.add_prefix(icon)

            done = Gtk.Button(icon_name="object-select-symbolic", valign=Gtk.Align.CENTER)
            done.add_css_class("flat")
            done.set_tooltip_text("Mark as done")
            done.connect("clicked", self._on_done, reminder)
            row.add_suffix(done)

            delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            delete.add_css_class("flat")
            delete.set_tooltip_text("Delete")
            delete.connect("clicked", self._on_delete, reminder)
            row.add_suffix(delete)

            self._group.add(row)
            self._rows.append(row)

    def _on_done(self, _button, reminder: dict) -> None:
        # A repeating reminder is re-armed rather than closed, so "done" means
        # "done for this occurrence".
        if store.advance(reminder):
            store.update(reminder["id"], due=reminder["due"], notified=False, done=False)
            self._toasts.add_toast(Adw.Toast(title="Rescheduled for the next occurrence"))
        else:
            store.update(reminder["id"], done=True)
            self._toasts.add_toast(Adw.Toast(title="Marked as done"))
        self._changed()

    def _on_delete(self, _button, reminder: dict) -> None:
        store.remove(reminder["id"])
        self._changed()

    def _changed(self) -> None:
        self.refresh()
        # No nudge needed: the daemon watches the reminder store and re-polls
        # on the write above, so the desktop widget updates within moments.
        # This used to restart the whole service on every add and tick.


def _describe(due: datetime.datetime | None, repeat: str, overdue: bool) -> str:
    if due is None:
        return "No due date"
    when = due.strftime("%a %-d %b, %-I:%M %p")
    parts = [f"Overdue — was {when}" if overdue else when]
    if repeat != "none":
        parts.append(f"repeats {repeat}")
    return "  ·  ".join(parts)


class _ReminderDialog(Adw.Dialog):
    def __init__(self, page: RemindersPage):
        super().__init__(title="New Reminder", content_width=460)
        self._page = page

        # Default to the next round half hour -- almost always closer to what
        # the user wants than "right now", which would fire immediately.
        start = datetime.datetime.now() + datetime.timedelta(minutes=30)
        start = start.replace(minute=0 if start.minute < 30 else 30, second=0, microsecond=0)

        self._text = Adw.EntryRow(title="Reminder")
        self._repeat = Adw.ComboRow(title="Repeat", model=Gtk.StringList.new(REPEAT_LABELS))

        self._calendar = Gtk.Calendar()
        self._calendar.select_day(
            GLib.DateTime.new_local(start.year, start.month, start.day, 0, 0, 0)
        )

        self._hour = Adw.SpinRow.new_with_range(0, 23, 1)
        self._hour.set_title("Hour")
        self._hour.set_value(start.hour)

        self._minute = Adw.SpinRow.new_with_range(0, 59, 5)
        self._minute.set_title("Minute")
        self._minute.set_value(start.minute)

        details = Adw.PreferencesGroup()
        details.add(self._text)
        details.add(self._repeat)

        when = Adw.PreferencesGroup(title="Due")
        calendar_row = Gtk.ListBoxRow(activatable=False, selectable=False)
        calendar_row.set_child(self._calendar)
        when.add(calendar_row)
        when.add(self._hour)
        when.add(self._minute)

        content = Adw.PreferencesPage()
        content.add(details)
        content.add(when)

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        self._save = Gtk.Button(label="Add")
        self._save.add_css_class("suggested-action")
        self._save.set_sensitive(False)
        self._save.connect("clicked", lambda _b: self._add())
        header.pack_end(self._save)

        self._text.connect(
            "changed", lambda row: self._save.set_sensitive(bool(row.get_text().strip()))
        )

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(content)
        self.set_child(view)

    def _add(self) -> None:
        selected = self._calendar.get_date()
        due = datetime.datetime(
            selected.get_year(),
            selected.get_month(),
            selected.get_day_of_month(),
            int(self._hour.get_value()),
            int(self._minute.get_value()),
        )
        repeat = REPEAT_VALUES[self._repeat.get_selected()]
        store.add(self._text.get_text().strip(), due, repeat)

        self._page._toasts.add_toast(Adw.Toast(title="Reminder added"))
        self.close()
        self._page._changed()
