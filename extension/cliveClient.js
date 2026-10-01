import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const BUS = 'org.jrf.DesktopForge.Clive';
const PATH = '/org/jrf/DesktopForge/Clive';
const IFACE = 'org.jrf.DesktopForge.Clive1';

const PORTAL = 'org.freedesktop.portal.Desktop';
const PORTAL_PATH = '/org/freedesktop/portal/desktop';
const CHOOSER = 'org.freedesktop.portal.FileChooser';
const REQUEST = 'org.freedesktop.portal.Request';

export class CliveClient {
    constructor() {
        this.listeners = new Set();
        this.state = {status: 'idle', messages: [], notice: 'Connecting to CLIVE…'};
        this.draft = '';
        this.pickSerial = 0;
        this.picks = new Set();
        this.cancellable = new Gio.Cancellable();
        this.subscription = Gio.DBus.session.signal_subscribe(BUS, IFACE, 'Changed', PATH,
            null, Gio.DBusSignalFlags.NONE, (_bus, _sender, _path, _iface, _signal, parameters) => {
                try { this.update(JSON.parse(parameters.deep_unpack()[0])); } catch (_error) { /* Ignore invalid messages. */ }
            });
        this.ownerWatch = Gio.bus_watch_name(Gio.BusType.SESSION, BUS, Gio.BusNameWatcherFlags.NONE,
            () => this.call('state', {}, result => this.update(result)),
            () => this.update({...this.state, status: 'paused', notice: 'CLIVE service is unavailable. Open CLIVE settings to finish setup.'}));
        this.call('state', {}, result => this.update(result));
    }

    update(state) {
        if (!state || this.cancellable.is_cancelled())
            return;
        this.state = state;
        for (const listener of this.listeners)
            listener(state);
    }

    subscribe(listener) {
        this.listeners.add(listener);
        listener(this.state);
        return () => this.listeners.delete(listener);
    }

    call(op, args = {}, callback = () => {}) {
        Gio.DBus.session.call(BUS, PATH, IFACE, 'Call',
            new GLib.Variant('(s)', [JSON.stringify({op, ...args})]), new GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 15000, this.cancellable, (bus, response) => {
                if (this.cancellable.is_cancelled())
                    return;
                try {
                    const reply = JSON.parse(bus.call_finish(response).deep_unpack()[0]);
                    if (!reply.ok)
                        throw new Error(reply.error);
                    callback(reply.result);
                } catch (error) {
                    this.notify(op === 'state'
                        ? 'CLIVE service is unavailable. Run ./install.sh --clive to set it up.'
                        : `${error.message}`);
                }
            });
    }

    /** The one way onto the card's notice line, wherever the failure came from. */
    notify(message) {
        this.update({...this.state, notice: message});
    }

    /**
     * Ask the user for files, through the desktop portal.
     *
     * GNOME Shell has no GTK, so the expanded chat's Gtk.FileDialog is not an
     * option here: the portal is the only file chooser a Shell process can put
     * on screen. Only the chosen paths come back -- the service still reads the
     * files itself, so nothing but a path ever crosses the bus.
     */
    pickFiles(callback) {
        // Subscribing after the call would be a race: the portal is free to
        // answer before OpenFile has returned the handle it answers on. The
        // path is derivable from our own bus name and the token we choose, so
        // it can be watched first. Tokens are [A-Za-z0-9_] only.
        const token = `dfclive${this.pickSerial++}_${GLib.random_int_range(0, 1000000)}`;
        const sender = Gio.DBus.session.get_unique_name().slice(1).replace(/\./g, '_');
        const path = `${PORTAL_PATH}/request/${sender}/${token}`;
        let subscription = 0;
        let moved = 0;
        let answered = false;
        const onResponse = parameters => {
            if (answered)
                return;
            answered = true;
            this._endPick(subscription);
            this._endPick(moved);
            subscription = moved = 0;
            if (this.cancellable.is_cancelled())
                return;
            const [code, results] = parameters.deep_unpack();
            // 1 is the user cancelling, which is an answer, not a failure.
            if (code === 1)
                return;
            if (code !== 0) {
                this.notify('The file chooser failed. Attach files from the expanded chat instead.');
                return;
            }
            // deep_unpack leaves an a{sv}'s values packed, one level down.
            const uris = results.uris?.deep_unpack() ?? [];
            const files = uris.map(uri => Gio.File.new_for_uri(uri));
            const paths = files.map(file => file.get_path()).filter(one => !!one);
            const remote = files.filter(file => !file.get_path()).map(file => file.get_basename());
            if (remote.length)
                this.notify(`${remote.join(', ')} ${remote.length > 1 ? 'are' : 'is'} on a network ` +
                    'location CLIVE cannot read directly; copy it to your computer first.');
            if (paths.length)
                callback(paths);
        };
        const watch = handle => Gio.DBus.session.signal_subscribe(PORTAL, REQUEST, 'Response', handle,
            null, Gio.DBusSignalFlags.NONE,
            (_bus, _sender, _path, _iface, _signal, parameters) => onResponse(parameters));
        subscription = watch(path);
        this.picks.add(subscription);
        const options = new GLib.Variant('a{sv}', {
            handle_token: GLib.Variant.new_string(token),
            multiple: GLib.Variant.new_boolean(true),
            // Modal to nothing: the Shell has no window to be modal to, and a
            // grab against the desktop would only fight the card's own.
            modal: GLib.Variant.new_boolean(false),
        });
        Gio.DBus.session.call(PORTAL, PORTAL_PATH, CHOOSER, 'OpenFile',
            new GLib.Variant('(ssa{sv})', ['', 'Attach files to CLIVE', options]),
            new GLib.VariantType('(o)'), Gio.DBusCallFlags.NONE, -1, this.cancellable,
            (bus, response) => {
                try {
                    const [handle] = bus.call_finish(response).deep_unpack();
                    // An older portal may ignore handle_token and answer on a
                    // path of its own; listen there as well.
                    if (handle && handle !== path && !answered) {
                        moved = watch(handle);
                        this.picks.add(moved);
                    }
                } catch (_error) {
                    if (answered || this.cancellable.is_cancelled())
                        return;
                    answered = true;
                    this._endPick(subscription);
                    subscription = 0;
                    this.notify('Could not open the file chooser. Attach files from the expanded chat instead.');
                }
            });
    }

    _endPick(subscription) {
        if (!this.picks.delete(subscription))
            return;
        Gio.DBus.session.signal_unsubscribe(subscription);
    }

    open() {
        const app = Gio.DesktopAppInfo.new('org.jrf.DesktopForge.desktop');
        if (app)
            app.launch_action('clive', null);
    }

    /** Open CLIVE settings in the app. The section names where to land. */
    openSettings(_section = 'models') {
        const app = Gio.DesktopAppInfo.new('org.jrf.DesktopForge.desktop');
        if (!app)
            return;
        // Older installs have no settings action in their desktop file yet.
        if (app.list_actions().includes('clive-settings'))
            app.launch_action('clive-settings', null);
        else
            app.launch_action('clive', null);
    }

    destroy() {
        this.cancellable.cancel();
        for (const subscription of this.picks)
            Gio.DBus.session.signal_unsubscribe(subscription);
        this.picks.clear();
        Gio.DBus.session.signal_unsubscribe(this.subscription);
        Gio.bus_unwatch_name(this.ownerWatch);
        this.listeners.clear();
    }
}
