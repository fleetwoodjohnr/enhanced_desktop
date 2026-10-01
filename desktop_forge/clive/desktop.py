"""GNOME accessibility and consented Wayland screen/input sessions.

Methods run on the agent worker. D-Bus signal delivery uses the service's GLib
main loop; no network, image capture, or accessibility traversal runs in Shell.
"""
from __future__ import annotations

import base64
import os
import threading
import time
import uuid

import gi
gi.require_version("Atspi", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Atspi, Gdk, GdkPixbuf, Gio, GLib, Gst, GstVideo

from .models import Cancelled
from .shell import ShellBridge

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
CAST = "org.freedesktop.portal.ScreenCast"
APP_READY_TIMEOUT = 10


def crop_rect(frame, stream_position, stream_size):
    """The window's rectangle inside the shared stream, or None if it is not on it.

    All in logical pixels: frame is global, the stream is one monitor placed at
    stream_position. A window hanging off the monitor is cut to the part the
    stream actually shows.
    """
    if not frame or not stream_size:
        return None
    sx, sy = stream_position or (0, 0)
    width, height = stream_size
    x1 = max(frame[0] - sx, 0)
    y1 = max(frame[1] - sy, 0)
    x2 = min(frame[0] - sx + frame[2], width)
    y2 = min(frame[1] - sy + frame[3], height)
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def map_point(crop, x, y):
    """A 0-1000 point in the cropped screenshot, as stream coordinates."""
    cx, cy, cw, ch = crop
    return (cx + x * cw / 1000, cy + y * ch / 1000)


class Desktop:
    def __init__(self, cancel: threading.Event, shell=None):
        self.cancel = cancel
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.shell = shell or ShellBridge(self.bus)
        self.session = None
        self.pending = None
        self.pipeline = None
        self.fd = None
        self.stream = None
        self.stream_size = None
        self.stream_position = None
        # The part of the stream the last screenshot showed; pointer input is
        # mapped back through it.
        self.crop = None
        self.elements = {}
        self.inspected_at = 0
        self.shot = None
        self.shot_time = 0
        self.closed_signal = 0
        self.lock = threading.RLock()
        Atspi.set_timeout(1500, 2000)
        Gst.init(None)

    def app_names(self):
        desktop = Atspi.get_desktop(0)
        return [desktop.get_child_at_index(i).get_name() for i in range(desktop.get_child_count())]

    def running_apps(self):
        """Return Shell's stable desktop IDs, falling back to AT-SPI labels."""
        try:
            windows = self.shell.windows()
        except RuntimeError:
            return [{"desktop_id": "", "name": name, "accessibility_name": name,
                     "focused": False} for name in self.app_names()]
        seen = set()
        result = []
        for window in sorted(windows, key=lambda row: not row.get("focused", False)):
            desktop_id = window["desktop_id"]
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            result.append(window)
        return result

    def wait_for_app(self, desktop_id, timeout=APP_READY_TIMEOUT):
        """Wait for launch registration without ever repeating the launch."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise Cancelled()
            try:
                if any(self._matches_target(desktop_id, row) for row in self.shell.windows()):
                    return True
            except RuntimeError:
                # The Shell bridge may be unavailable on an old installation;
                # AT-SPI can still prove that an accessible app has started.
                try:
                    self._app(desktop_id)
                except ValueError:
                    pass
                else:
                    return True
            self.cancel.wait(0.1)
        return False

    def focus(self, desktop_id):
        target = desktop_id
        windows = self.shell.windows()
        if not any(row.get("desktop_id") == target for row in windows):
            alias = next((row.get("desktop_id") for row in windows
                          if self._matches_target(desktop_id, row)), None)
            target = alias or target
        if not self.shell.activate(target):
            raise ValueError(f"{desktop_id} is not running")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise Cancelled()
            if any(row.get("focused") and self._matches_target(desktop_id, row)
                   for row in self.shell.windows()):
                return {"focused": desktop_id}
            self.cancel.wait(0.05)
        raise RuntimeError("The application did not take focus")

    def window_titles(self, desktop_id):
        """Titles of the app's windows, for App Access's window guards."""
        return [str(row.get("title", "")) for row in self._target_windows(desktop_id)]

    def _target_frame(self, desktop_id):
        windows = sorted(self._target_windows(desktop_id), key=lambda row: not row.get("focused"))
        frame = windows[0].get("frame") if windows else None
        return frame if isinstance(frame, list) and len(frame) == 4 else None

    def _target_windows(self, desktop_id):
        try:
            return [row for row in self.shell.windows()
                    if self._matches_target(desktop_id, row)]
        except RuntimeError:
            return []

    @staticmethod
    def _matches_target(desktop_id, window):
        running_id = window.get("desktop_id", "")
        if running_id == desktop_id:
            return True
        try:
            info = Gio.DesktopAppInfo.new(desktop_id)
        except TypeError:
            info = None
        startup = (info.get_startup_wm_class() if info else "") or ""
        classes = {str(window.get(key, "")).casefold()
                   for key in ("wm_class", "wm_class_instance")}
        if startup and startup.casefold() in classes:
            return True
        # LibreOffice may group all module windows under its start-center ID
        # even when Writer/Calc/Impress was the desktop entry that launched it.
        requested = desktop_id.casefold()
        running = running_id.casefold()
        libre_prefixes = ("libreoffice-", "org.libreoffice.libreoffice")
        return (requested.startswith(libre_prefixes) and running.startswith(libre_prefixes))

    def _app(self, name):
        desktop = Atspi.get_desktop(0)
        target_pids = {row.get("pid") for row in self._target_windows(name) if row.get("pid")}
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app.get_name() == name or app.get_process_id() in target_pids:
                pid = app.get_process_id()
                try:
                    executable = os.path.basename(os.readlink(f"/proc/{pid}/exe")).lower()
                except OSError:
                    executable = name.lower()
                if any(term in executable for term in ("terminal", "ptyxis", "konsole", "xterm", "alacritty", "kitty", "wezterm")):
                    raise ValueError("Terminal control is not enabled in CLIVE")
                return app
        raise ValueError(
            f"{name} does not expose accessibility controls; use screenshot controls instead"
        )

    def _active(self, name):
        try:
            windows = self.shell.windows()
        except RuntimeError:
            windows = None
        if windows is not None:
            if any(row.get("focused") and self._matches_target(name, row) for row in windows):
                return True
            raise RuntimeError("The approved application is not active. Focus it and retry.")

        # An old or disabled extension can still operate applications that do
        # publish a complete accessibility tree.
        app = self._app(name)
        for i in range(min(app.get_child_count(), 30)):
            child = app.get_child_at_index(i)
            if child.get_state_set().contains(Atspi.StateType.ACTIVE):
                return app
        raise RuntimeError("The approved application is not active. Focus it and start a new task.")

    def inspect(self, app):
        """Inspect an app, or return a safe route to visual control.

        Launching and accessibility registration are asynchronous. A missing
        tree is an ordinary capability result, not a fatal agent exception:
        when Shell can see the window, screenshot tools can still operate it.
        """
        deadline = time.monotonic() + APP_READY_TIMEOUT
        running = False
        root = None
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise Cancelled()
            running = running or bool(self._target_windows(app))
            try:
                root = self._app(app)
                break
            except ValueError:
                self.cancel.wait(0.1)
        if root is None:
            reason = "accessibility_unavailable" if running else "not_running"
            return {
                "app": app,
                "accessibility_available": False,
                "reason": reason,
                "fallback": "desktop_screenshot" if running else "app_launch",
                "elements": [],
                "truncated": False,
            }
        self.elements.clear()
        self.inspected_at = time.monotonic()
        self.inspected_app = app
        items = []
        queue = [(root, 0)]
        while queue and len(items) < 180:
            if self.cancel.is_set():
                raise Cancelled()
            node, depth = queue.pop(0)
            try:
                states = node.get_state_set()
                if states.contains(Atspi.StateType.DEFUNCT):
                    continue
                role = node.get_role_name()
                if "password" in role.lower():
                    continue
                identifier = uuid.uuid4().hex[:8]
                actions = []
                action = node.get_action_iface()
                if action:
                    actions = [action.get_action_name(i) for i in range(action.get_n_actions())]
                row = {"element": identifier, "role": role, "name": node.get_name()[:300],
                       "actions": actions, "editable": states.contains(Atspi.StateType.EDITABLE)}
                text = node.get_text_iface()
                if text:
                    row["text"] = Atspi.Text.get_text(text, 0, min(Atspi.Text.get_character_count(text), 1500))
                items.append(row)
                self.elements[identifier] = node
                if depth < 10:
                    for i in range(min(node.get_child_count(), 60)):
                        child = node.get_child_at_index(i)
                        if child:
                            queue.append((child, depth + 1))
            except GLib.Error:
                continue
        return {"app": app, "accessibility_available": True,
                "elements": items, "truncated": bool(queue)}

    def _element(self, app, identifier):
        if app != getattr(self, "inspected_app", None) or time.monotonic() - self.inspected_at > 120:
            raise ValueError("Inspect the application again before using its controls")
        node = self.elements.get(identifier)
        if not node or node.get_state_set().contains(Atspi.StateType.DEFUNCT):
            raise ValueError("The control changed. Inspect the application again")
        return node

    def call(self, operation, app, **a):
        if self.cancel.is_set():
            raise Cancelled()
        if operation == "inspect":
            return self.inspect(app)
        if operation in ("action", "type"):
            self._active(app)
            node = self._element(app, a["element"])
            if operation == "action":
                interface = node.get_action_iface()
                indices = [i for i in range(interface.get_n_actions())
                           if interface.get_action_name(i) == a["action"]] if interface else []
                if not indices:
                    raise ValueError("This action is not available on the control")
                if not interface.do_action(indices[0]):
                    raise RuntimeError("The application did not accept the action")
            else:
                interface = node.get_editable_text_iface()
                if not interface or not interface.set_text_contents(a["text"]):
                    raise RuntimeError("The control did not accept the text")
            # A fresh tree is also the verification result; old element IDs expire.
            return self.inspect(app)
        self._active(app)
        self._ensure_session()
        if self.cancel.is_set():
            raise Cancelled()
        if operation == "screenshot":
            return self.screenshot(app)
        if operation in ("click", "scroll"):
            if self.shot != a["screenshot"] or self.shot_app != app or time.monotonic() - self.shot_time > 120:
                raise ValueError("Take a fresh screenshot before using the pointer")
        if operation == "click":
            x, y = map_point(self.crop or (0, 0, *self.stream_size), a["x"], a["y"])
            self._notify("NotifyPointerMotionAbsolute", "(oa{sv}udd)",
                         (self.session, {}, self.stream, x, y))
            self._notify("NotifyPointerButton", "(oa{sv}iu)", (self.session, {}, 272, 1))
            self._notify("NotifyPointerButton", "(oa{sv}iu)", (self.session, {}, 272, 0), release=True)
        elif operation == "scroll":
            self._notify("NotifyPointerAxis", "(oa{sv}dd)", (self.session, {}, 0.0, float(a["dy"])))
        elif operation == "key":
            keys = {"Tab": 0xff09, "Return": 0xff0d, "Escape": 0xff1b, "BackSpace": 0xff08,
                    "Delete": 0xffff, "Home": 0xff50, "End": 0xff57,
                    "PageUp": 0xff55, "PageDown": 0xff56,
                    "Left": 0xff51, "Up": 0xff52, "Right": 0xff53, "Down": 0xff54}
            self._send_keysym(keys[a["key"]])
        elif operation == "type_text":
            for character in a["text"]:
                if character == "\n":
                    keysym = 0xff0d
                elif character == "\t":
                    keysym = 0xff09
                else:
                    keysym = Gdk.unicode_to_keyval(ord(character))
                if not keysym or (ord(character) < 32 and character not in "\n\t"):
                    raise ValueError("Text contains a character the desktop portal cannot type")
                self._send_keysym(keysym)
        elif operation == "shortcut":
            self._shortcut(a["key"], a.get("modifiers", []))
        else:
            raise ValueError("Unknown desktop operation")
        if self.cancel.wait(0.25):
            raise Cancelled()
        return self.screenshot(app)

    def _send_keysym(self, keysym):
        self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)",
                     (self.session, {}, keysym, 1))
        self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)",
                     (self.session, {}, keysym, 0), release=True)

    def _shortcut(self, key, modifiers):
        modifier_keys = {
            "Control": 0xffe3, "Alt": 0xffe9, "Shift": 0xffe1, "Super": 0xffeb,
        }
        named = {
            "Tab": 0xff09, "Return": 0xff0d, "Escape": 0xff1b,
            "BackSpace": 0xff08, "Delete": 0xffff, "Home": 0xff50,
            "End": 0xff57, "PageUp": 0xff55, "PageDown": 0xff56,
            "Left": 0xff51, "Up": 0xff52, "Right": 0xff53, "Down": 0xff54,
        }
        if key in named:
            keysym = named[key]
        elif len(key) == 1 and ord(key) >= 32:
            keysym = Gdk.unicode_to_keyval(ord(key))
        else:
            raise ValueError("Unsupported shortcut key")
        pressed = []
        try:
            for modifier in modifiers:
                value = modifier_keys[modifier]
                self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)",
                             (self.session, {}, value, 1))
                pressed.append(value)
            self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)",
                         (self.session, {}, keysym, 1))
            self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)",
                         (self.session, {}, keysym, 0), release=True)
        finally:
            for value in reversed(pressed):
                self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)",
                             (self.session, {}, value, 0), release=True)

    def _notify(self, method, signature, args, release=False):
        if self.cancel.is_set() and not release:
            raise Cancelled()
        self.bus.call_sync(PORTAL, PORTAL_PATH, REMOTE, method, GLib.Variant(signature, args),
                           None, Gio.DBusCallFlags.NONE, 5000, None)

    def _request(self, interface, method, signature, args):
        if self.cancel.is_set():
            raise Cancelled()
        token = "clive" + uuid.uuid4().hex
        options = args[-1]
        options["handle_token"] = GLib.Variant("s", token)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{token}"
        event, result = threading.Event(), []

        def response(_bus, _sender, _path, _iface, _signal, parameters):
            result.extend(parameters.unpack())
            event.set()

        sub = self.bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", path,
                                       None, Gio.DBusSignalFlags.NONE, response)
        self.pending = path
        try:
            self.bus.call_sync(PORTAL, PORTAL_PATH, interface, method, GLib.Variant(signature, args),
                               None, Gio.DBusCallFlags.NONE, 10000, None)
            deadline = time.monotonic() + 90
            while not event.wait(0.1):
                if self.cancel.is_set():
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise RuntimeError("Desktop sharing request timed out")
            if result[0] != 0:
                raise RuntimeError("Desktop sharing was declined or is unavailable")
            if self.cancel.is_set():
                raise Cancelled()
            return result[1]
        finally:
            if not event.is_set():
                self._close_path(path, "org.freedesktop.portal.Request")
            self.pending = None
            self.bus.signal_unsubscribe(sub)

    def _ensure_session(self):
        if self.session:
            return
        created = self._request(REMOTE, "CreateSession", "(a{sv})", ({
            "session_handle_token": GLib.Variant("s", "clive" + uuid.uuid4().hex)},))
        self.session = created["session_handle"]
        try:
            self.closed_signal = self.bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Session", "Closed",
                self.session, None, Gio.DBusSignalFlags.NONE, lambda *a: self._revoked())
            self._request(REMOTE, "SelectDevices", "(oa{sv})", (self.session, {"types": GLib.Variant("u", 3)}))
            self._request(CAST, "SelectSources", "(oa{sv})", (self.session, {
                "types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", 1)}))
            result = self._request(REMOTE, "Start", "(osa{sv})", (self.session, "", {}))
            if result.get("devices", 0) & 3 != 3 or not result.get("streams"):
                raise RuntimeError("Keyboard, pointer, and screen sharing are required")
            self.stream, properties = result["streams"][0]
            self.stream_size = properties.get("logical_size") or properties.get("size")
            self.stream_position = properties.get("position")
            value, descriptors = self.bus.call_with_unix_fd_list_sync(PORTAL, PORTAL_PATH, CAST,
                "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType.new("(h)"),
                Gio.DBusCallFlags.NONE, 10000, None, None)
            self.fd = descriptors.get(value.unpack()[0])
            self.pipeline = Gst.parse_launch(f"pipewiresrc fd={self.fd} path={self.stream} do-timestamp=true "
                "! videoconvert ! video/x-raw,format=RGB ! appsink name=frame max-buffers=1 drop=true sync=false")
            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Screen capture could not start")
        except BaseException:
            self.close()
            raise

    def screenshot(self, app):
        self._active(app)
        sample = self.pipeline.get_by_name("frame").emit("try-pull-sample", 5 * Gst.SECOND)
        if not sample:
            raise RuntimeError("No screen frame arrived; desktop control has paused")
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        pixels = buffer.extract_dup(0, buffer.get_size())
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB,
                                               False, 8, info.width, info.height, info.stride[0])
        if not self.stream_size:
            self.stream_size = (info.width, info.height)
        # Only the approved app's window is sent: anything else on the shared
        # monitor -- another app, one App Access has switched off -- stays out.
        # An older Shell extension reports no frame; then the whole stream is
        # all there is to send, as before.
        frame = self._target_frame(app)
        self.crop = crop_rect(frame, self.stream_position, self.stream_size) if frame else None
        if frame and self.crop is None:
            raise RuntimeError("The app's window is not on the shared screen")
        if self.crop:
            scale = info.width / self.stream_size[0]
            cx, cy, cw, ch = (round(v * scale) for v in self.crop)
            cw = max(1, min(cw, info.width - cx))
            ch = max(1, min(ch, info.height - cy))
            pixbuf = pixbuf.new_subpixbuf(cx, cy, cw, ch).copy()
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width > 1280:
            pixbuf = pixbuf.scale_simple(1280, max(1, round(height * 1280 / width)), GdkPixbuf.InterpType.BILINEAR)
        ok, png = pixbuf.save_to_bufferv("png", [], [])
        if not ok:
            raise RuntimeError("Screen image could not be encoded")
        self.shot, self.shot_app, self.shot_time = uuid.uuid4().hex, app, time.monotonic()
        return {"app": app, "screenshot": self.shot, "width": pixbuf.get_width(), "height": pixbuf.get_height(),
                "image": base64.b64encode(png).decode("ascii")}

    def _revoked(self):
        # Closing a portal session externally must stop the task too.
        self.cancel.set()

    def _close_path(self, path, interface):
        self.bus.call(PORTAL, path, interface, "Close", None, None, Gio.DBusCallFlags.NONE, 5000, None, None)

    def close(self):
        with self.lock:
            if self.closed_signal:
                self.bus.signal_unsubscribe(self.closed_signal)
                self.closed_signal = 0
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
                self.pipeline = None
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if self.pending:
                self._close_path(self.pending, "org.freedesktop.portal.Request")
                self.pending = None
            if self.session:
                self._close_path(self.session, "org.freedesktop.portal.Session")
                self.session = None
            self.shot = None
            self.crop = None
            self.stream_position = None
            self.elements.clear()
