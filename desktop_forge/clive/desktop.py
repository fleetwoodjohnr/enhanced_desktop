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
from gi.repository import Atspi, GdkPixbuf, Gio, GLib, Gst, GstVideo

from .models import Cancelled

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
CAST = "org.freedesktop.portal.ScreenCast"


class Desktop:
    def __init__(self, cancel: threading.Event):
        self.cancel = cancel
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = None
        self.pending = None
        self.pipeline = None
        self.fd = None
        self.stream = None
        self.stream_size = None
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

    def _app(self, name):
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app.get_name() == name:
                pid = app.get_process_id()
                try:
                    executable = os.path.basename(os.readlink(f"/proc/{pid}/exe")).lower()
                except OSError:
                    executable = name.lower()
                if any(term in executable for term in ("terminal", "ptyxis", "konsole", "xterm", "alacritty", "kitty", "wezterm")):
                    raise ValueError("Terminal control is not enabled in CLIVE")
                return app
        raise ValueError(f"{name} is not running or does not expose accessibility controls")

    def _active(self, name):
        app = self._app(name)
        for i in range(min(app.get_child_count(), 30)):
            child = app.get_child_at_index(i)
            if child.get_state_set().contains(Atspi.StateType.ACTIVE):
                return app
        raise RuntimeError("The approved application is not active. Focus it and start a new task.")

    def inspect(self, app):
        root = self._app(app)
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
        return {"app": app, "elements": items, "truncated": bool(queue)}

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
            width, height = self.stream_size
            self._notify("NotifyPointerMotionAbsolute", "(oa{sv}udd)",
                         (self.session, {}, self.stream, a["x"] * width / 1000, a["y"] * height / 1000))
            self._notify("NotifyPointerButton", "(oa{sv}iu)", (self.session, {}, 272, 1))
            self._notify("NotifyPointerButton", "(oa{sv}iu)", (self.session, {}, 272, 0), release=True)
        elif operation == "scroll":
            self._notify("NotifyPointerAxis", "(oa{sv}dd)", (self.session, {}, 0.0, float(a["dy"])))
        elif operation == "key":
            keys = {"Tab": 0xff09, "Return": 0xff0d, "Escape": 0xff1b, "BackSpace": 0xff08,
                    "Left": 0xff51, "Up": 0xff52, "Right": 0xff53, "Down": 0xff54}
            self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)", (self.session, {}, keys[a["key"]], 1))
            self._notify("NotifyKeyboardKeysym", "(oa{sv}iu)", (self.session, {}, keys[a["key"]], 0), release=True)
        else:
            raise ValueError("Unknown desktop operation")
        if self.cancel.wait(0.25):
            raise Cancelled()
        return self.screenshot(app)

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
        if info.width > 1280:
            pixbuf = pixbuf.scale_simple(1280, max(1, round(info.height * 1280 / info.width)), GdkPixbuf.InterpType.BILINEAR)
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
            self.elements.clear()
