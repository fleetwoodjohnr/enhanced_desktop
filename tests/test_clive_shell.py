from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from gi.repository import GLib

from desktop_forge.clive.shell import ShellBridge


class ShellBridgeTests(unittest.TestCase):
    def test_window_records_are_validated(self):
        bus = Mock()
        bus.call_sync.return_value = GLib.Variant("(s)", (json.dumps([
            {"desktop_id": "org.mozilla.thunderbird.desktop", "focused": True},
            {"title": "unidentified"},
            "not a record",
        ]),))
        self.assertEqual(ShellBridge(bus).windows(), [
            {"desktop_id": "org.mozilla.thunderbird.desktop", "focused": True}
        ])

    def test_activate_returns_shell_result(self):
        bus = Mock()
        bus.call_sync.return_value = GLib.Variant("(b)", (True,))
        self.assertTrue(ShellBridge(bus).activate("libreoffice-writer.desktop"))
        arguments = bus.call_sync.call_args.args
        self.assertEqual(arguments[3], "Activate")
        self.assertEqual(arguments[4].unpack(), ("libreoffice-writer.desktop",))


if __name__ == "__main__":
    unittest.main()
