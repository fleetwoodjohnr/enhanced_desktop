from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_forge import config
from desktop_forge.backend import desktop_icons


class FakeSettings:
    def __init__(self, values, defaults=None):
        self.values = dict(values)
        self.defaults = defaults or dict(values)
        self.locked = set()

    def get_string(self, key):
        return self.values[key]

    def get_default_value(self, key):
        class Value:
            def __init__(self, value):
                self.value = value
            def unpack(self):
                return self.value
        return Value(self.defaults[key])

    def is_writable(self, key):
        return key not in self.locked

    def set_string(self, key, value):
        self.values[key] = value
        return True

    def reset(self, key):
        self.values[key] = self.defaults[key]

    def apply(self):
        pass


class DesktopIconTests(unittest.TestCase):
    def test_native_size_and_system_theme_are_independent(self):
        ding = FakeSettings({"icon-size": "standard"})
        interface = FakeSettings({"icon-theme": "Papirus"}, {"icon-theme": "Adwaita"})
        bridge = desktop_icons.DesktopIcons(ding, interface)
        bridge.set_icon_size("large")
        bridge.set_icon_theme(None)
        self.assertEqual(ding.values["icon-size"], "large")
        self.assertEqual(interface.values["icon-theme"], "Adwaita")
        with self.assertRaises(ValueError):
            bridge.set_icon_size("enormous")

    def test_glass_styles_are_scoped_and_safe(self):
        options = config.Config(desktop_icons={
            "material": "liquid", "tint": "#336699", "opacity": 0.4,
            "artwork": "monochrome", "foreground_mode": "custom",
            "foreground": "#ffffff",
        }).desktop_icon_options()
        css = desktop_icons.render_stylesheet(options)
        desktop_icons.validate_stylesheet(css)
        self.assertIn("window.desktopwindow", css)
        self.assertIn("linear-gradient", css)
        self.assertIn("grayscale(1)", css)
        self.assertNotIn("#panel", css)

    def test_managed_import_preserves_user_css_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gtk_css = root / "gtk-4.0" / "gtk.css"
            generated = root / "desktop-forge" / "desktop-icons.css"
            gtk_css.parent.mkdir(parents=True)
            original = "button { color: rebeccapurple; }\n"
            gtk_css.write_text(original, encoding="utf-8")
            with patch.object(desktop_icons, "GTK_CSS_PATH", gtk_css), \
                 patch.object(desktop_icons, "GENERATED_CSS_PATH", generated):
                options = config.Config(desktop_icons={"material": "frosted"}).desktop_icon_options()
                self.assertTrue(desktop_icons.sync_stylesheet(options))
                installed = gtk_css.read_text(encoding="utf-8")
                self.assertIn(desktop_icons.START_MARKER, installed)
                self.assertIn(original, installed)
                self.assertTrue(generated.exists())
                self.assertFalse(desktop_icons.sync_stylesheet(options))
                changed_tint = config.Config(desktop_icons={
                    "material": "frosted", "tint": "#ff3366",
                }).desktop_icon_options()
                self.assertTrue(desktop_icons.sync_stylesheet(changed_tint))
                self.assertIn("255,51,102", generated.read_text(encoding="utf-8"))

                reset = config.Config().desktop_icon_options()
                self.assertTrue(desktop_icons.sync_stylesheet(reset))
                self.assertEqual(gtk_css.read_text(encoding="utf-8"), original)
                self.assertFalse(generated.exists())

    def test_restart_does_not_enable_a_disabled_ding(self):
        disabled = unittest.mock.Mock(returncode=0, stdout="other@example.com\n")
        with patch.object(desktop_icons.shutil, "which", return_value="/bin/gnome-extensions"), \
             patch.object(desktop_icons.subprocess, "run", return_value=disabled) as run:
            self.assertTrue(desktop_icons.restart_ding())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["/bin/gnome-extensions", "list", "--enabled"])


if __name__ == "__main__":
    unittest.main()
