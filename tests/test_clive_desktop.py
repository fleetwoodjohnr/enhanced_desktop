import threading
import time
import unittest
from unittest.mock import Mock, patch

try:
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
