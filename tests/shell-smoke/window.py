"""Synthetic repainting application confined to the isolated test session.

An optional argument names the instance, so the smoke test can open two
independent windows instead of re-activating a single GtkApplication.
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

name = sys.argv[1] if len(sys.argv) > 1 else "repaint"
title = "DF repaint test" if name == "repaint" else f"DF {name} test"
app = Gtk.Application(application_id=f"org.jrf.DesktopForge.RenderTest.{name}")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=title)
    window.set_default_size(320, 300)
    entry = Gtk.Entry()
    entry.set_placeholder_text("Typing repaint test")
    window.set_child(entry)
    window.present()
    counter = 0

    def type_text():
        nonlocal counter
        counter += 1
        entry.set_text("Typing redraw " + "a" * (counter % 40))
        entry.set_position(-1)
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(80, type_text)


app.connect("activate", activate)
app.run([sys.argv[0]])
