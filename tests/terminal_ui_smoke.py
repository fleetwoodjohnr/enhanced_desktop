"""Build the terminal widget's window and exercise its tabs (needs a display)."""
import os
import sys
import tempfile
from unittest.mock import patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from desktop_forge.terminal import CSS, Gdk, TerminalWindow
from desktop_forge import config


def settle():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def main():
    Adw.init()
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    window = TerminalWindow(None)
    def check_minimum():
        width = window.measure(Gtk.Orientation.HORIZONTAL, -1).minimum
        height = window.measure(Gtk.Orientation.VERTICAL, -1).minimum
        assert width <= 160 and height <= 90, f"terminal minimum {width}×{height} exceeds widget minimum"

    check_minimum()
    terminal = window._current()
    assert isinstance(terminal.get_parent(), Gtk.ScrolledWindow), "terminal has no scrollbar"
    assert not terminal.get_scroll_on_output(), "new output interrupts scrollback"
    window.activate_action("win.smaller-text", None)
    assert terminal.get_font_scale() == 0.9, "smaller text action did not zoom"
    tabs = window.tab_view
    assert tabs.get_n_pages() == 1, f"started with {tabs.get_n_pages()} tabs"
    bar = window.get_content().get_first_child()
    controls = bar.get_end_action_widget()
    assert isinstance(controls, Gtk.Box), "tab bar has no new-tab control beside its menu"
    add_tab = controls.get_first_child()
    menu = add_tab.get_next_sibling()
    assert isinstance(add_tab, Gtk.Button) and add_tab.get_icon_name() == "list-add-symbolic"
    assert isinstance(menu, Gtk.MenuButton) and menu.get_next_sibling() is None, "+ is not immediately left of the menu"
    assert add_tab.get_tooltip_text() == "New tab" and add_tab.get_focusable()
    add_tab.emit("clicked")
    assert tabs.get_n_pages() == 2, f"new tab gave {tabs.get_n_pages()} tabs"
    assert tabs.get_selected_page() == tabs.get_nth_page(1), "new-tab button did not select the new tab"
    assert window.get_focus() == window._current(), "new-tab button did not focus the terminal"
    assert window._current().get_font_scale() == 0.9, "new tab lost text size"
    for _ in range(20):
        window.activate_action("win.smaller-text", None)
    assert window._current().get_font_scale() == 0.5, "text size has no lower bound"
    window.activate_action("win.reset-text", None)
    assert terminal.get_font_scale() == window._current().get_font_scale() == 1.0
    check_minimum()
    window.close_tab()
    window.close_tab()
    settle()
    assert tabs.get_n_pages() == 1, f"closing every tab left {tabs.get_n_pages()}, not a fresh one"
    assert window.emit("close-request") is True, "the window let itself be closed"
    with tempfile.TemporaryDirectory() as directory, patch.object(config, "CONFIG_PATH", os.path.join(directory, "config.json")):
        original = {"widgets": [{"id": "one", "type": "terminal", "options": {"keep": True}},
                                {"id": "two", "type": "terminal", "options": {"font_scale": 1.2}}],
                    "unknown": {"preserved": True}}
        config.write_json(config.CONFIG_PATH, original)
        persistent = TerminalWindow(None, "one")
        persistent.activate_action("win.smaller-text", None)
        saved = config.read_json(config.CONFIG_PATH)
        assert saved["widgets"][0]["options"] == {"keep": True, "font_scale": 0.9}
        assert saved["widgets"][1] == original["widgets"][1] and saved["unknown"] == original["unknown"]
        reopened = TerminalWindow(None, "one")
        assert reopened._current().get_font_scale() == 0.9, "restarted terminal lost text size"
        reopened.destroy()
        persistent.destroy()
    print("terminal UI smoke: ok")


if __name__ == "__main__":
    main()
