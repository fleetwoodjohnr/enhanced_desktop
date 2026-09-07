"""Synthetic repainting application confined to the isolated test session."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

app = Gtk.Application(application_id="org.jrf.DesktopForge.RenderTest")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="DF repaint test")
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
app.run([])
