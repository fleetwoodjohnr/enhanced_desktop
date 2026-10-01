"""Run the real terminal and report its grid for the isolated compositor test."""
import json
import os
import sys

sys.path.insert(0, os.environ["DF_TEST_ROOT"])
from desktop_forge.terminal import Gtk, GLib, TerminalWindow, main

reported = set()


def report():
    for window in Gtk.Window.get_toplevels():
        if not isinstance(window, TerminalWindow):
            continue
        terminal = window._current()
        if terminal not in reported:
            terminal.feed(b"\r\nTERMINAL LIVE PREVIEW\r\nResize keeps text at its normal size.\r\n")
            reported.add(terminal)
        path = os.path.join(os.environ["DF_TEST_DIR"], f"terminal-{os.getpid()}.json")
        command = path + ".feed"
        if os.path.exists(command):
            with open(command, "rb") as stream:
                terminal.feed(stream.read())
            os.unlink(command)
        adjustment = terminal.get_vadjustment()
        with open(path, "w") as stream:
            json.dump({"columns": terminal.get_column_count(), "rows": terminal.get_row_count(),
                       "font_scale": terminal.get_font_scale(), "scroll": adjustment.get_value(),
                       "bottom": adjustment.get_upper() - adjustment.get_page_size()}, stream)
    return GLib.SOURCE_CONTINUE


GLib.timeout_add(50, report)
sys.exit(main(sys.argv[2:]))  # the host supplies --terminal, then the theme
