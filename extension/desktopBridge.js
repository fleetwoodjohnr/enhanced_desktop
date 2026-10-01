import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';


export const BUS_NAME = 'org.jrf.DesktopForge.Shell';
export const OBJECT_PATH = '/org/jrf/DesktopForge/Shell';
export const INTERFACE = 'org.jrf.DesktopForge.Shell1';

const XML = `<node>
  <interface name="${INTERFACE}">
    <method name="Windows"><arg name="windows" type="s" direction="out"/></method>
    <method name="Activate">
      <arg name="desktop_id" type="s" direction="in"/>
      <arg name="activated" type="b" direction="out"/>
    </method>
    <method name="Features"><arg name="features" type="s" direction="out"/></method>
  </interface>
</node>`;

const WINDOW_TYPES = new Set([
    Meta.WindowType.NORMAL,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
]);

/** Give CLIVE stable app identity/focus data that Wayland withholds from clients. */
export class DesktopBridge {
    /** @param {() => object} features - what this extension build can apply */
    constructor(features = () => ({})) {
        this._features = features;
        this._tracker = Shell.WindowTracker.get_default();
        this._export = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._export.export(Gio.DBus.session, OBJECT_PATH);
        this._owner = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    _record(window) {
        if (!window || !WINDOW_TYPES.has(window.get_window_type()))
            return null;
        const app = this._tracker.get_window_app(window);
        const desktopId = app?.get_id?.() ?? '';
        // Windows without an application identity cannot be placed into a
        // meaningful approval preview and are intentionally not controllable.
        if (!desktopId)
            return null;
        return {
            desktop_id: desktopId,
            name: app.get_name?.() ?? desktopId,
            title: window.get_title?.() ?? '',
            pid: window.get_pid?.() ?? 0,
            wm_class: window.get_wm_class?.() ?? '',
            wm_class_instance: window.get_wm_class_instance?.() ?? '',
            // Window rules match on this; the same words rulesLogic.js uses.
            type: window.get_window_type() === Meta.WindowType.NORMAL ? 'normal' : 'dialog',
            focused: global.display.focus_window === window,
            minimized: !!window.minimized,
            monitor: window.get_monitor?.() ?? -1,
            // Where the window is, in global logical pixels, so CLIVE can crop
            // a screenshot to this one window rather than the whole display.
            frame: (rect => [rect.x, rect.y, rect.width, rect.height])(window.get_frame_rect()),
        };
    }

    Windows() {
        const records = global.get_window_actors()
            .map(actor => this._record(actor.meta_window))
            .filter(record => !!record);
        return JSON.stringify(records);
    }

    /**
     * The customization modules this running extension has, so the app can
     * tell the user to log out and back in when it is an older build.
     */
    Features() {
        return JSON.stringify(this._features());
    }

    Activate(desktopId) {
        const candidates = global.get_window_actors()
            .map(actor => ({window: actor.meta_window, record: this._record(actor.meta_window)}))
            .filter(candidate => candidate.record?.desktop_id === desktopId);
        if (!candidates.length)
            return false;
        const workspace = global.workspace_manager.get_active_workspace();
        const candidate = candidates.find(one =>
            !one.record.minimized && one.window.get_workspace() === workspace) ?? candidates[0];
        candidate.window.activate(global.get_current_time());
        return true;
    }

    destroy() {
        if (this._owner) {
            Gio.bus_unown_name(this._owner);
            this._owner = 0;
        }
        this._export?.unexport();
        this._export = null;
        this._tracker = null;
    }
}
