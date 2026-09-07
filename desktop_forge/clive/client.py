"""GTK client: async session D-Bus only, no optional agent dependencies."""
import json

from gi.repository import Gio, GLib

from .settings import BUS_NAME, INTERFACE, OBJECT_PATH

# The model test runs real inference on a local model that may manage only a
# few tokens a second; the key check is one round trip to Ollama Cloud; storing
# a credential blocks while a locked login keyring prompts the user.
TIMEOUTS = {"validate": 2400000, "check_key": 30000, "configure": 90000}


class Client:
    def __init__(self, changed):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.changed = changed
        self.subscription = self.bus.signal_subscribe(BUS_NAME, INTERFACE, "Changed", OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._signal)

    def _signal(self, _bus, _sender, _path, _interface, _signal, parameters):
        self.changed(json.loads(parameters.unpack()[0]))

    def call(self, op, callback=lambda result, error: None, **arguments):
        def complete(bus, result):
            try:
                reply = json.loads(bus.call_finish(result).unpack()[0])
                callback(reply.get("result"), reply.get("error"))
            except GLib.Error as error:
                # A timeout is not a missing service, and saying so sends people
                # to reinstall when the login keyring is simply waiting to be
                # unlocked.
                if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
                    callback(None, "CLIVE did not answer in time. If your login keyring is "
                                   "locked, unlock it and try again.")
                else:
                    callback(None, "CLIVE is not available. Run ./install.sh --clive to install its service.")
        self.bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, "Call",
            GLib.Variant("(s)", (json.dumps({"op": op, **arguments}),)), GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE, TIMEOUTS.get(op, 15000), None, complete)

    def close(self):
        if self.subscription:
            self.bus.signal_unsubscribe(self.subscription)
            self.subscription = 0
