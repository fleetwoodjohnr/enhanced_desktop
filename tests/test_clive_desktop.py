import threading
import time
import unittest
from unittest.mock import Mock, patch

try:
    from desktop_forge.clive import desktop as desktop_module
    from gi.repository import GLib
    from desktop_forge.clive.desktop import Desktop
    from desktop_forge.clive.models import Cancelled
    HAS_DESKTOP = True
except (ImportError, ValueError):
    HAS_DESKTOP = False


@unittest.skipUnless(HAS_DESKTOP, "GNOME desktop bindings unavailable")
class DesktopTests(unittest.TestCase):
    def desktop(self):
        desktop = Desktop.__new__(Desktop)
        desktop.cancel = threading.Event()
        desktop.bus = Mock()
        desktop.session = "/org/freedesktop/portal/desktop/session/test"
        desktop.stream = 27
        desktop.stream_size = (960, 540)
        desktop.shot = "frame"
        desktop.shot_app = "test"
        desktop.shot_time = time.monotonic()
        desktop._active = Mock()
        desktop._ensure_session = Mock()
        desktop.screenshot = Mock(return_value={"screenshot": "after"})
        return desktop

    def test_scaled_click_uses_logical_stream_coordinates_and_releases_button(self):
        desktop = self.desktop()
        result = desktop.call("click", app="test", screenshot="frame", x=500, y=250)
        calls = desktop.bus.call_sync.call_args_list
        self.assertEqual(calls[0].args[4].unpack(), (desktop.session, {}, 27, 480.0, 135.0))
        self.assertEqual(calls[1].args[4].unpack()[-2:], (272, 1))
        self.assertEqual(calls[2].args[4].unpack()[-2:], (272, 0))
        self.assertEqual(result["screenshot"], "after")

    def test_changed_focus_and_stale_screenshot_never_send_input(self):
        desktop = self.desktop()
        desktop._active.side_effect = RuntimeError("Focus changed")
        with self.assertRaises(RuntimeError):
            desktop.call("click", app="test", screenshot="frame", x=0, y=0)
        desktop.bus.call_sync.assert_not_called()
        desktop._active.side_effect = None
        desktop.shot_time -= 150
        with self.assertRaises(ValueError):
            desktop.call("click", app="test", screenshot="frame", x=0, y=0)
        desktop.bus.call_sync.assert_not_called()

    def test_cancellation_never_sends_new_input(self):
        desktop = self.desktop()
        desktop.cancel.set()
        with self.assertRaises(Cancelled):
            desktop.call("key", app="test", key="Return")
        desktop.bus.call_sync.assert_not_called()

    def test_text_fallback_types_unicode_and_returns_a_fresh_frame(self):
        desktop = self.desktop()
        result = desktop.call("type_text", app="org.example.Editor.desktop", text="Hi ✓")
        calls = desktop.bus.call_sync.call_args_list
        # Each character is pressed and released through the portal.
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(call.args[3] == "NotifyKeyboardKeysym" for call in calls))
        self.assertEqual(result["screenshot"], "after")

    def test_shortcut_releases_modifiers_in_reverse_order(self):
        desktop = self.desktop()
        desktop.call("shortcut", app="org.example.Editor.desktop", key="s",
                     modifiers=["Control", "Shift"])
        events = [call.args[4].unpack()[-2:] for call in desktop.bus.call_sync.call_args_list]
        self.assertEqual(events, [
            (0xffe3, 1), (0xffe1, 1), (ord("s"), 1), (ord("s"), 0),
            (0xffe1, 0), (0xffe3, 0),
        ])

    def test_visual_controls_use_shell_focus_without_accessibility(self):
        desktop = Desktop.__new__(Desktop)
        desktop.cancel = threading.Event()
        desktop.shell = Mock()
        desktop.shell.windows.return_value = [{
            "desktop_id": "org.libreoffice.LibreOffice.writer.desktop",
            "focused": True,
        }]
        self.assertTrue(desktop._active("org.libreoffice.LibreOffice.writer.desktop"))
        desktop.shell.windows.return_value[0]["focused"] = False
        with self.assertRaisesRegex(RuntimeError, "not active"):
            desktop._active("org.libreoffice.LibreOffice.writer.desktop")

    def test_launch_wait_accepts_libreoffice_shared_window_identity(self):
        desktop = Desktop.__new__(Desktop)
        desktop.cancel = Mock()
        desktop.cancel.is_set.return_value = False
        desktop.cancel.wait.return_value = False
        desktop.shell = Mock()
        desktop.shell.windows.side_effect = [[], [{
            "desktop_id": "libreoffice-startcenter.desktop",
            "wm_class": "libreoffice",
        }]]
        self.assertTrue(desktop.wait_for_app("libreoffice-calc.desktop", timeout=1))

    def test_missing_accessibility_is_a_recoverable_screenshot_fallback(self):
        desktop = Desktop.__new__(Desktop)
        desktop.cancel = threading.Event()
        desktop._target_windows = Mock(return_value=[{
            "desktop_id": "libreoffice-startcenter.desktop",
        }])
        desktop._app = Mock(side_effect=ValueError("no accessibility tree"))
        with patch.object(desktop_module.time, "monotonic", side_effect=[0, 0, 11]):
            result = desktop.inspect("libreoffice-calc.desktop")
        self.assertEqual(result, {
            "app": "libreoffice-calc.desktop",
            "accessibility_available": False,
            "reason": "accessibility_unavailable",
            "fallback": "desktop_screenshot",
            "elements": [],
            "truncated": False,
        })

    def test_portal_denial_is_reported_and_subscription_removed(self):
        desktop = self.desktop()
        desktop.bus.get_unique_name.return_value = ":1.42"
        callbacks = []
        desktop.bus.signal_subscribe.side_effect = lambda *args: callbacks.append(args[-1]) or 17
        def respond(*args):
            callbacks[0](None, None, None, None, None, GLib.Variant("(ua{sv})", (1, {})))
        desktop.bus.call_sync.side_effect = respond
        with self.assertRaisesRegex(RuntimeError, "declined"):
            desktop._request("org.freedesktop.portal.RemoteDesktop", "Start", "(osa{sv})", (desktop.session, "", {}))
        desktop.bus.signal_unsubscribe.assert_called_once_with(17)
        self.assertIsNone(desktop.pending)


if __name__ == "__main__":
    unittest.main()
