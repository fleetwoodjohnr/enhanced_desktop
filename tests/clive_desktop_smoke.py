"""Exercise real AT-SPI actions in a dedicated test window on the current desktop."""
import json
import sys
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop_forge.clive.desktop import Desktop

GLib.set_prgname("clive-desktop-test")
app = Adw.Application(application_id="org.jrf.DesktopForge.CliveDesktopTest")
result = {"ok": False}


def activate(application):
    window = Adw.ApplicationWindow(application=application, title="CLIVE desktop test", default_width=420, default_height=220)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
    entry = Gtk.Entry(placeholder_text="CLIVE test input")
    button = Gtk.Button(label="Verify CLIVE")
    label = Gtk.Label(label="Waiting")
    button.connect("clicked", lambda *_: label.set_text("Verified: " + entry.get_text()))
    box.append(entry); box.append(button); box.append(label)
    window.set_content(box)
    window.present()

    def run():
        desktop = Desktop(threading.Event())
        try:
            name = "clive-desktop-test"
            for _ in range(30):
                if name in desktop.app_names():
                    break
                time.sleep(0.1)
            tree = desktop.inspect(name)
            element = next(r for r in tree["elements"] if r["editable"])
            tree = desktop.call("type", app=name, element=element["element"], text="CLIVE works")
            control = next(r for r in tree["elements"] if r["name"] == "Verify CLIVE" and r["actions"])
            desktop.call("action", app=name, element=control["element"], action=control["actions"][0])
            time.sleep(0.25)
            verified = desktop.inspect(name)
            assert any("Verified: CLIVE works" in r.get("name", "") + r.get("text", "") for r in verified["elements"]), verified
            result.update(ok=True, app=name, verified="Typed into a real GTK entry and activated its button via AT-SPI")
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            desktop.close()
            GLib.idle_add(lambda: (window.destroy(), app.quit(), False)[-1])
    GLib.timeout_add(800, lambda: (threading.Thread(target=run, daemon=True).start(), False)[1])


app.connect("activate", activate)
app.run([])
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["ok"] else 1)
