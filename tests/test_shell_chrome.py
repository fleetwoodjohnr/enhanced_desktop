from __future__ import annotations

import unittest

from desktop_forge.backend.shell_chrome import DashToDock


class FakeSettings:
    def __init__(self, **values):
        self.values = {
            "dock-fixed": False,
            "autohide": True,
            "intellihide": True,
            "manualhide": False,
            "dock-position": "BOTTOM",
            "dash-max-icon-size": 48,
            "height-fraction": 0.9,
            "extend-height": False,
            **values,
        }
        self.locked = set()

    def is_writable(self, key):
        return key not in self.locked

    def get_boolean(self, key):
        return self.values[key]

    def get_string(self, key):
        return self.values[key]

    def get_int(self, key):
        return self.values[key]

    def get_double(self, key):
        return self.values[key]

    def set_boolean(self, key, value):
        self.values[key] = value
        return True

    def set_string(self, key, value):
        self.values[key] = value
        return True

    def set_int(self, key, value):
        self.values[key] = value
        return True

    def set_double(self, key, value):
        self.values[key] = value
        return True

    def delay(self):
        pass

    def apply(self):
        pass


class DashToDockTests(unittest.TestCase):
    def test_reads_native_state_and_extended_length(self) -> None:
        settings = FakeSettings(**{
            "dock-fixed": True,
            "dock-position": "LEFT",
            "dash-max-icon-size": 56,
            "extend-height": True,
        })
        state = DashToDock(settings).read()
        self.assertEqual(state.visibility, "always")
        self.assertEqual(state.position, "LEFT")
        self.assertEqual(state.icon_size, 56)
        self.assertEqual(state.maximum_length, 1.0)

    def test_visibility_modes_have_deterministic_native_mappings(self) -> None:
        settings = FakeSettings()
        dock = DashToDock(settings)
        dock.set_visibility("intelligent")
        self.assertFalse(settings.values["dock-fixed"])
        self.assertTrue(settings.values["autohide"])
        self.assertTrue(settings.values["intellihide"])
        dock.set_visibility("auto")
        self.assertFalse(settings.values["intellihide"])
        dock.set_visibility("always")
        self.assertTrue(settings.values["dock-fixed"])
        self.assertFalse(settings.values["autohide"])
        self.assertFalse(settings.values["manualhide"])

    def test_geometry_is_clamped_and_full_edge_mode_is_disabled(self) -> None:
        settings = FakeSettings()
        dock = DashToDock(settings)
        dock.set_icon_size(200)
        dock.set_maximum_length(0.1)
        dock.set_position("RIGHT")
        self.assertEqual(settings.values["dash-max-icon-size"], 64)
        self.assertEqual(settings.values["height-fraction"], 0.33)
        self.assertFalse(settings.values["extend-height"])
        self.assertEqual(settings.values["dock-position"], "RIGHT")

    def test_locked_settings_fail_without_partial_visibility_update(self) -> None:
        settings = FakeSettings()
        settings.locked.add("autohide")
        with self.assertRaises(PermissionError):
            DashToDock(settings).set_visibility("always")
        self.assertFalse(settings.values["dock-fixed"])


if __name__ == "__main__":
    unittest.main()
