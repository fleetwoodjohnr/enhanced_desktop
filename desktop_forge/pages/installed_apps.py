"""Pick an installed application and drop a launcher on the desktop."""
from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..backend import desktop_entry as de
from ..backend import markup
from ..backend.app_index import AppIndex, InstalledApp

ICON_SIZE = 48


class InstalledAppsPage(Gtk.Box):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._toasts = toast_overlay
        self._index = AppIndex()
        self._selected: InstalledApp | None = None

        self._search = Gtk.SearchEntry(placeholder_text="Search installed applications")
        self._search.set_margin_top(12)
        self._search.set_margin_bottom(6)
        self._search.set_margin_start(12)
        self._search.set_margin_end(12)
        self._search.connect("search-changed", lambda _e: self._refill())
        self.append(self._search)

        self._flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            homogeneous=True,
            max_children_per_line=8,
            min_children_per_line=2,
            row_spacing=6,
            column_spacing=6,
        )
        self._flow.set_margin_start(12)
        self._flow.set_margin_end(12)
        self._flow.set_valign(Gtk.Align.START)
        self._flow.connect("selected-children-changed", self._on_selection_changed)
        self._flow.connect("child-activated", lambda _fb, _child: self._add_selected())

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self._flow)

        self._stack = Gtk.Stack()
        self._stack.add_named(
            Adw.StatusPage(title="Loading applications…", icon_name="content-loading-symbolic"),
            "loading",
        )
        self._stack.add_named(scroller, "grid")
        self._stack.add_named(
            Adw.StatusPage(
                title="No matches",
                description="No installed application matches that search.",
                icon_name="system-search-symbolic",
            ),
            "empty",
        )
        self._stack.set_visible_child_name("loading")
        self.append(self._stack)

        self._add_button = Gtk.Button(label="Add to Desktop")
        self._add_button.add_css_class("suggested-action")
        self._add_button.set_sensitive(False)
        self._add_button.connect("clicked", lambda _b: self._add_selected())

        action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action_bar.set_halign(Gtk.Align.END)
        action_bar.set_margin_top(6)
        action_bar.set_margin_bottom(12)
        action_bar.set_margin_start(12)
        action_bar.set_margin_end(12)
        action_bar.append(self._add_button)
        self.append(action_bar)

        self._index.build_async(self._on_index_ready)

    def _on_index_ready(self) -> None:
        self._refill()

    def _refill(self) -> None:
        child = self._flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._flow.remove(child)
            child = nxt

        self._selected = None
        self._add_button.set_sensitive(False)

        matches = self._index.search(self._search.get_text())
        if not matches:
            self._stack.set_visible_child_name("empty" if self._index.apps else "loading")
            return

        for app in matches:
            self._flow.append(_AppTile(app))
        self._stack.set_visible_child_name("grid")

    def _on_selection_changed(self, _flow) -> None:
        children = self._flow.get_selected_children()
        self._selected = children[0].get_child().app if children else None
        self._add_button.set_sensitive(self._selected is not None)

    def _add_selected(self) -> None:
        if self._selected is None:
            return
        try:
            path, trusted = de.copy_to_desktop(self._selected.filename)
        except (ValueError, OSError) as exc:
            self._toasts.add_toast(Adw.Toast(title=markup(f"Could not add: {exc}")))
            return

        problems = de.validate(path)
        if problems:
            message = f"Added {self._selected.name}, but it failed validation"
        elif not trusted:
            message = f"{self._selected.name} added. {de.TRUST_HINT}"
        else:
            message = f"{self._selected.name} added to Desktop"

        toast = Adw.Toast(title=markup(message))
        toast.set_timeout(8 if not trusted else 5)
        self._toasts.add_toast(toast)


class _AppTile(Gtk.Box):
    def __init__(self, app: InstalledApp):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.app = app
        self.add_css_class("app-tile")
        self.set_tooltip_text(app.comment or app.name)

        image = Gtk.Image(pixel_size=ICON_SIZE)
        if app.icon is not None:
            image.set_from_gicon(app.icon)
        else:
            image.set_from_icon_name("application-x-executable")
        self.append(image)

        label = Gtk.Label(label=app.name)
        label.add_css_class("app-tile-name")
        label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        label.set_max_width_chars(14)
        label.set_justify(Gtk.Justification.CENTER)
        self.append(label)
