"""Groups the three shortcut tools behind one top-level tab.

Six tabs across the header was too many to scan; the three shortcut views are
one task seen from three angles, so they belong together with an inline
switcher rather than competing for space with Widgets.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .custom_launcher import CustomLauncherPage
from .installed_apps import InstalledAppsPage
from .manage import ManagePage


class ShortcutsPage(Gtk.Box):
    def __init__(self, toast_overlay: Adw.ToastOverlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._stack = Adw.ViewStack()
        self.manage = ManagePage(toast_overlay)

        self._stack.add_titled_with_icon(
            InstalledAppsPage(toast_overlay), "installed", "Installed Apps",
            "view-grid-symbolic",
        )
        self._stack.add_titled_with_icon(
            CustomLauncherPage(toast_overlay, on_created=self.manage.refresh),
            "custom", "Script or Program", "document-new-symbolic",
        )
        self._stack.add_titled_with_icon(
            self.manage, "manage", "Manage", "view-list-symbolic",
        )

        switcher = Adw.InlineViewSwitcher(stack=self._stack, display_mode=Adw.InlineViewSwitcherDisplayMode.BOTH)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(12)
        switcher.set_margin_bottom(6)

        self.append(switcher)
        self.append(self._stack)
        self._stack.set_vexpand(True)
