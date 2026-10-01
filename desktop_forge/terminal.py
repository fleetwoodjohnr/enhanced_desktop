"""The terminal widget's window: shells in tabs, pinned onto the desktop.

GNOME Shell cannot host a terminal inside a card, so the extension starts
this as its own Wayland client, keeps the window inside the Terminal card's
rectangle, under every other window and out of Alt+Tab and the overview.
The window is see-through: the card's glass behind it is the background.
"""
import math
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Vte

from . import config

# The cards' text colours (DARK_GLASS and LIGHT_GLASS in extension.js).
FOREGROUND = {True: "#f7faff", False: "#172033"}
SCHEMES = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}

CSS = """
window.df-terminal, window.df-terminal tabbar .box {
    background: none;
    box-shadow: none;
}
/* Keep both tab actions usable inside the smallest terminal card. */
.df-terminal tabbar .end-action {
    padding-left: 0;
    padding-right: 0;
}
.df-terminal tabbar .end-action button {
    min-width: 24px;
    padding-left: 0;
    padding-right: 0;
}
/* A short terminal still needs a draggable scrollbar. */
.df-terminal-scroll scrollbar.vertical,
.df-terminal-scroll scrollbar.vertical range,
.df-terminal-scroll scrollbar.vertical trough {
    min-height: 0;
    padding: 0;
    margin: 0;
}
.df-terminal-scroll scrollbar.vertical slider {
    min-height: 18px;
    padding-top: 0;
    padding-bottom: 0;
    border-top-width: 0;
    border-bottom-width: 0;
    margin-top: 2px;
    margin-bottom: 2px;
}
"""


def _rgba(spec: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(spec)
    return color


class TerminalWindow(Adw.ApplicationWindow):
    def __init__(self, app, widget_id=None):
        super().__init__(application=app, title="Terminal", decorated=False,
                         default_width=560, default_height=360)
        # Adwaita's 360×200 minimum is for standalone apps. The desktop card
        # owns our size; request only what the tab bar and terminal need.
        self.set_size_request(-1, -1)
        self._widget_id = widget_id
        self._font_scale = self._load_font_scale()
        self.add_css_class("df-terminal")
        self.tab_view = Adw.TabView()
        self.tab_view.connect("notify::n-pages", self._on_pages)
        bar = Adw.TabBar(view=self.tab_view, autohide=False)
        menu = self._make_menu()
        add_tab = Gtk.Button(icon_name="list-add-symbolic", action_name="win.new-tab",
                             tooltip_text="New tab")
        add_tab.add_css_class("flat")
        add_tab.update_property([Gtk.AccessibleProperty.LABEL], ["New tab"])
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                tooltip_text="Terminal menu: tabs, text size and scrolling")
        button.add_css_class("flat")
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        controls.append(add_tab)
        controls.append(button)
        bar.set_end_action_widget(controls)
        self._menu = menu

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      margin_top=8, margin_bottom=8, margin_start=4, margin_end=4)
        box.append(bar)
        box.append(self.tab_view)
        self.tab_view.set_vexpand(True)
        self.set_content(box)

        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for trigger, action in (("<Control><Shift>t", self.new_tab), ("<Control><Shift>w", self.close_tab),
                                ("<Control><Shift>c", self._copy), ("<Control><Shift>v", self._paste)):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                           action=Gtk.CallbackAction.new(lambda *_a, act=action: act() or True)))
        for trigger, action in (("<Control>minus", "smaller-text"), ("<Control>equal", "larger-text"),
                                ("<Control>plus", "larger-text"), ("<Control>0", "reset-text"),
                                ("<Shift>Page_Up", "scroll-up"), ("<Shift>Page_Down", "scroll-down")):
            keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                                           action=Gtk.NamedAction.new("win." + action)))
        self.add_controller(keys)

        # The extension owns this window's lifetime: Alt+F4 must not take
        # every shell with it.
        self.connect("close-request", lambda _window: True)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_a: self._recolor())
        self.new_tab()

    def _make_menu(self):
        menu = Gio.Menu()
        sections = (
            (("New tab", "new-tab", self.new_tab), ("Close tab", "close-tab", self.close_tab)),
            (("Copy", "copy", self._copy), ("Paste", "paste", self._paste)),
            (("Smaller text", "smaller-text", lambda: self._resize_text(-0.1)),
             ("Larger text", "larger-text", lambda: self._resize_text(0.1)),
             ("Reset text size", "reset-text", lambda: self._set_font_scale(1.0))),
            (("Scroll up", "scroll-up", lambda: self._scroll(-1)),
             ("Scroll down", "scroll-down", lambda: self._scroll(1)),
             ("Scroll to latest output", "scroll-bottom", lambda: self._scroll(None))),
        )
        for entries in sections:
            section = Gio.Menu()
            for label, name, callback in entries:
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", lambda *_a, act=callback: act())
                self.add_action(action)
                section.append(label, "win." + name)
            menu.append_section(None, section)
        return menu

    def _load_font_scale(self):
        raw = config.read_json(config.CONFIG_PATH) if self._widget_id else None
        for entry in (raw or {}).get("widgets", []):
            if entry.get("id") == self._widget_id and entry.get("type") == "terminal":
                value = entry.get("options", {}).get("font_scale", 1.0)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    return max(0.5, min(2.0, value))
        return 1.0

    def _resize_text(self, delta):
        self._set_font_scale(round(self._font_scale + delta, 2))

    def _set_font_scale(self, value):
        value = max(0.5, min(2.0, value))
        if value == self._font_scale:
            return
        self._font_scale = value
        for i in range(self.tab_view.get_n_pages()):
            self.tab_view.get_nth_page(i).get_child().get_child().set_font_scale(self._font_scale)
        if not self._widget_id:
            return
        # Read again so a layout or settings change isn't overwritten.
        raw = config.read_json(config.CONFIG_PATH)
        for entry in (raw or {}).get("widgets", []):
            if entry.get("id") == self._widget_id and entry.get("type") == "terminal":
                entry.setdefault("options", {})["font_scale"] = self._font_scale
                try:
                    config.write_json(config.CONFIG_PATH, raw)
                except OSError as error:
                    print(f"Could not save terminal text size: {error}", file=sys.stderr)
                break

    def _scroll(self, direction):
        if terminal := self._current():
            adjustment = terminal.get_vadjustment()
            bottom = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
            value = bottom if direction is None else adjustment.get_value() + direction * adjustment.get_page_increment()
            adjustment.set_value(max(adjustment.get_lower(), min(bottom, value)))

    def new_tab(self):
        terminal = Vte.Terminal(hexpand=True, vexpand=True, scrollback_lines=10000)
        terminal.set_font_scale(self._font_scale)
        terminal.set_scroll_on_output(False)
        terminal.set_scroll_on_keystroke(True)
        terminal.connect("increase-font-size", lambda *_a: self._resize_text(0.1))
        terminal.connect("decrease-font-size", lambda *_a: self._resize_text(-0.1))
        if hasattr(terminal, "set_context_menu_model"):
            terminal.set_context_menu_model(self._menu)
        self._color(terminal)
        scrolling = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      overlay_scrolling=True, hexpand=True, vexpand=True)
        scrolling.add_css_class("df-terminal-scroll")
        scrolling.set_child(terminal)
        page = self.tab_view.append(scrolling)
        page.set_title("Terminal")
        terminal.connect("termprop-changed::" + Vte.TERMPROP_XTERM_TITLE,
                         lambda term, _prop: page.set_title(
                             term.get_termprop_string(Vte.TERMPROP_XTERM_TITLE)[0] or "Terminal"))
        terminal.connect("child-exited", lambda *_a: self.tab_view.close_page(page))
        shell = os.environ.get("SHELL") or "/bin/bash"
        terminal.spawn_async(pty_flags=Vte.PtyFlags.DEFAULT, working_directory=GLib.get_home_dir(),
                             argv=[shell], envv=None, spawn_flags=GLib.SpawnFlags.DEFAULT,
                             child_setup=None, timeout=-1, cancellable=None, callback=None)
        self.tab_view.set_selected_page(page)
        terminal.grab_focus()
        return page

    def close_tab(self):
        page = self.tab_view.get_selected_page()
        if page:
            self.tab_view.close_page(page)

    def _on_pages(self, *_args):
        # The widget is never empty: closing the last tab opens a fresh one.
        if self.tab_view.get_n_pages() == 0:
            self.new_tab()

    def _current(self):
        page = self.tab_view.get_selected_page()
        return page.get_child().get_child() if page else None

    def _copy(self):
        if terminal := self._current():
            terminal.copy_clipboard_format(Vte.Format.TEXT)

    def _paste(self):
        if terminal := self._current():
            terminal.paste_clipboard()

    def _color(self, terminal):
        dark = Adw.StyleManager.get_default().get_dark()
        terminal.set_colors(_rgba(FOREGROUND[dark]), _rgba("rgba(0,0,0,0)"), None)

    def _recolor(self):
        for i in range(self.tab_view.get_n_pages()):
            self._color(self.tab_view.get_nth_page(i).get_child().get_child())


def main(args=None):
    args = list(args if args is not None else sys.argv[1:])
    app = Adw.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(application):
        Adw.StyleManager.get_default().set_color_scheme(
            SCHEMES.get(args[0] if args else "system", Adw.ColorScheme.DEFAULT))
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        TerminalWindow(application, args[1] if len(args) > 1 else None).present()

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
