import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .window import DesktopForgeWindow

APP_ID = "org.jrf.DesktopForge"
STYLE_PATH = os.path.join(os.path.dirname(__file__), "style.css")


class DesktopForgeApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("clive", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open the CLIVE assistant", None)
        self.add_main_option("clive-settings", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             "Open CLIVE settings at a section (for example models)", "SECTION")
        self._window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._load_stylesheet()
        action = Gio.SimpleAction.new("clive", None)
        action.connect("activate", lambda *_: self._open_clive())
        self.add_action(action)

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        if options.contains("clive-settings"):
            section = options.lookup_value("clive-settings", GLib.VariantType.new("s"))
            self._open_clive_settings(section.get_string() if section else "")
        elif options.contains("clive"):
            self._open_clive()
        else:
            self.activate()
        return 0

    def _open_clive(self):
        self.activate()
        self._window._stack.set_visible_child_name("clive")

    def _open_clive_settings(self, section: str):
        self._open_clive()
        page = self._window._pages.get("clive")
        if hasattr(page, "open_settings"):
            page.open_settings(section)

    def do_shutdown(self):
        if self._window:
            page = self._window._pages.get("clive")
            if hasattr(page, "close"):
                page.close()
        Adw.Application.do_shutdown(self)

    def _load_stylesheet(self):
        """Load style.css at APPLICATION priority, so it overrides Adwaita's
        defaults but a user's own ~/.config/gtk-4.0/gtk.css can still win.

        A missing or broken stylesheet must never stop the app starting -- the
        app is fully usable unstyled, and a colour is not worth a crash.
        """
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(STYLE_PATH)
        except GLib.Error:
            return
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self):
        if self._window is None:
            self._window = DesktopForgeWindow(application=self)
        self._window.present()
