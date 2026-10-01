import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import St from 'gi://St';

import {afterFirstFrame, unmaximizeAny} from './windowInfo.js';

const RESTART_MS = 2000;
const FLAPPING_US = 5 * GLib.USEC_PER_SEC;

/** The real window stays in Mutter's window group; only its paint is cloned. */
const TerminalPreview = GObject.registerClass(
class TerminalPreview extends St.Widget {
    _init(window) {
        super._init({
            style_class: 'df-terminal-preview',
            x_expand: true, y_expand: true, reactive: false,
            clip_to_allocation: true,
        });
        this._window = window;
        this._source = window.get_compositor_private();
        this._clone = new Clutter.Clone({source: this._source, reactive: false});
        this.add_child(this._clone);
        this._source.connectObject('notify::allocation', () => this.queue_relayout(), this);
    }

    vfunc_get_preferred_width(_forHeight) {
        return [0, 0];
    }

    vfunc_get_preferred_height(_forWidth) {
        return [0, 0];
    }

    vfunc_allocate(box) {
        this.set_allocation(box);
        const frame = this._window.get_frame_rect();
        const buffer = this._window.get_buffer_rect();
        const [width, height] = this._source.get_size();
        const left = buffer.x - frame.x;
        const top = buffer.y - frame.y;
        // Keep glyphs at their normal size while Wayland commits a resize.
        // The card clips the previous buffer until the new one arrives.
        this._clone.allocate(new Clutter.ActorBox({
            x1: left, y1: top, x2: left + width, y2: top + height,
        }));
    }
});

/**
 * The Terminal cards' windows. GNOME Shell cannot draw a terminal, so each
 * card gets a `desktop-forge --terminal` process (desktop_forge/terminal.py)
 * whose see-through window is pinned over the card: on every workspace,
 * under every other window, out of Alt+Tab and the overview.
 *
 * Only windows of a Meta.WaylandClient may be hidden from the window list,
 * so the processes start through one, as Desktop Icons NG does. They stay
 * ordinary windows rather than desktop ones: DING's full-screen desktop
 * window would stack over a desktop-type terminal and take its clicks.
 *
 * The host outlives the lock screen, so shells keep running behind it.
 */
export class TerminalHost {
    /** @param {(id: string) => void} onChange - a terminal started or stopped */
    constructor(onChange = () => {}) {
        this._onChange = onChange;
        this._terminals = new Map();  // widget id -> record
        this._editing = false;
        this._dirty = new Set();
        this._updateId = 0;
        global.display.connectObject(
            'window-created', (_display, window) => this._claim(window),
            'grab-op-end', (_display, window) => this._enforce(window), this);
    }

    /** Run one terminal per entry ({id, theme}); stop the rest. */
    sync(entries) {
        const wanted = new Map(entries.map(entry => [entry.id, entry]));
        for (const id of [...this._terminals.keys()]) {
            if (!wanted.has(id))
                this._stop(id);
        }
        for (const [id, {theme}] of wanted) {
            if (this._terminals.has(id))
                continue;
            const record = {id, theme, client: null, window: null, rect: null, started: 0,
                restartId: 0, cancelWait: null, failed: false, widget: null, preview: null};
            this._terminals.set(id, record);
            this._start(record);
        }
    }

    /** What the card shows underneath: null once the terminal is up. */
    status(id) {
        const record = this._terminals.get(id);
        if (record?.window && !record.cancelWait)
            return null;
        return record?.failed ? 'The terminal could not start. Is VTE for GTK 4 installed?'
            : 'Starting terminal…';
    }

    /** Keep the terminal inside its card, `rect` in stage coordinates. */
    place(id, rect) {
        const record = this._terminals.get(id);
        if (!record)
            return;
        record.rect = rect;
        this._place(record);
    }

    /** Follow the card across placement, resizing and edit-mode reparenting. */
    bindWidget(id, widget) {
        const record = this._terminals.get(id);
        if (!record || record.widget === widget)
            return;
        this.unbindWidget(id);
        record.widget = widget;
        const update = () => this._queueUpdate(record);
        widget.connectObject(
            'notify::allocation', update,
            'notify::x', update, 'notify::y', update,
            'notify::width', update, 'notify::height', update,
            'notify::mapped', update, 'resource-scale-changed', update,
            'destroy', () => this.unbindWidget(id), record);
        update();
    }

    unbindWidget(id) {
        const record = this._terminals.get(id);
        if (!record)
            return;
        this._dropPreview(record);
        record.widget?.disconnectObject(record);
        record.widget = null;
        record.rect = null;
        this._dirty.delete(record);
        if (!this._dirty.size && this._updateId) {
            global.compositor.get_laters().remove(this._updateId);
            this._updateId = 0;
        }
        const actor = record.window?.get_compositor_private();
        if (actor)
            actor.opacity = 0;
    }

    /** Paint the terminal inside the modal card, below its move/resize controls. */
    setEditing(editing) {
        this._editing = editing;
        for (const record of this._terminals.values())
            this._place(record);
    }

    _queueUpdate(record) {
        this._dirty.add(record);
        if (this._updateId)
            return;
        this._updateId = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._updateId = 0;
            const dirty = [...this._dirty];
            this._dirty.clear();
            for (const item of dirty) {
                const widget = item.widget;
                if (widget?.mapped && widget.has_allocation()) {
                    const [x, y] = widget.get_transformed_position();
                    const [width, height] = widget.get_transformed_size();
                    if ([x, y, width, height].every(Number.isFinite) && width > 0 && height > 0)
                        item.rect = {x: Math.round(x), y: Math.round(y),
                            width: Math.round(width), height: Math.round(height)};
                }
                this._place(item);
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _dropPreview(record) {
        record.preview?.destroy();
        record.preview = null;
    }

    _start(record) {
        const command = GLib.find_program_in_path('desktop-forge') ??
            GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'desktop-forge']);
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        let client;
        try {
            client = Meta.WaylandClient.new_subprocess(global.context,
                launcher, [command, '--terminal', record.theme, record.id]);
        } catch (error) {
            logError(error, 'desktop-forge: could not start the terminal');
            record.failed = true;
            this._onChange(record.id);
            return;
        } finally {
            // Meta passes the private Wayland socket through this launcher.
            // Keeping its duplicate fd open leaves an orphan window when the
            // process exits, until GJS happens to collect the launcher.
            launcher.close();
        }
        record.client = client;
        record.started = GLib.get_monotonic_time();
        client.get_subprocess().wait_async(null, () => this._exited(record, client));
    }

    _exited(record, client) {
        if (record.client !== client)
            return;  // stopped on purpose
        record.client = null;
        this._release(record);
        // ponytail: simple crash guard, backoff if it flaps
        if (GLib.get_monotonic_time() - record.started < FLAPPING_US) {
            console.warn('desktop-forge: the terminal exited right after starting; not restarting it');
            record.failed = true;
        } else {
            record.restartId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, RESTART_MS, () => {
                record.restartId = 0;
                this._start(record);
                return GLib.SOURCE_REMOVE;
            });
        }
        this._onChange(record.id);
    }

    _stop(id) {
        const record = this._terminals.get(id);
        this.unbindWidget(id);
        this._terminals.delete(id);
        if (record.restartId)
            GLib.source_remove(record.restartId);
        this._release(record);
        const client = record.client;
        record.client = null;
        client?.get_subprocess()?.force_exit();
    }

    _release(record) {
        this._dropPreview(record);
        record.cancelWait?.();
        record.cancelWait = null;
        record.window?.disconnectObject(this);
        record.window = null;
    }

    _claim(window) {
        if (window.get_window_type() !== Meta.WindowType.NORMAL)
            return;  // menus and other popups must not replace the terminal
        const record = [...this._terminals.values()].find(item => item.client?.owns_window(window));
        if (!record)
            return;
        // Before any feature module sees the window: skip-taskbar windows
        // are not app windows, so tiling, effects and rules leave it alone.
        window.hide_from_window_list();
        this._release(record);
        record.window = window;
        window.connectObject(
            // Clicking raises it; a desktop card stays under the windows.
            'raised', () => this._queueUpdate(record),
            'position-changed', () => this._queueUpdate(record),
            'size-changed', () => this._queueUpdate(record),
            'notify::maximized-horizontally', () => this._queueUpdate(record),
            'notify::maximized-vertically', () => this._queueUpdate(record),
            'unmanaged', () => {
                if (record.window === window)
                    this._release(record);
            }, this);
        // Mutter is still placing a new window until its first frame.
        record.cancelWait = afterFirstFrame(window, () => {
            record.cancelWait = null;
            window.stick();
            this._queueUpdate(record);
            this._onChange(record.id);
        });
    }

    _enforce(window) {
        const record = [...this._terminals.values()].find(item => item.window && item.window === window);
        if (record)
            this._queueUpdate(record);
    }

    _place(record) {
        const {window, rect, widget} = record;
        if (!window || record.cancelWait)
            return;
        const actor = window.get_compositor_private();
        if (!actor)
            return;
        if (!rect || !widget?.mapped) {
            actor.opacity = 0;
            return;
        }
        unmaximizeAny(window);
        let frame = window.get_frame_rect();
        if (frame.x !== rect.x || frame.y !== rect.y || frame.width !== rect.width || frame.height !== rect.height)
            window.move_resize_frame(false, rect.x, rect.y, rect.width, rect.height);
        frame = window.get_frame_rect();
        const arrived = frame.x === rect.x && frame.y === rect.y &&
            frame.width === rect.width && frame.height === rect.height;
        if (this._editing && !record.preview) {
            record.preview = new TerminalPreview(window);
            widget.add_child(record.preview);
        } else if (!this._editing && arrived) {
            this._dropPreview(record);
        }
        actor.opacity = this._editing || !arrived ? 0 : 255;
        window.lower();
    }

    destroy() {
        global.display.disconnectObject(this);
        for (const id of [...this._terminals.keys()])
            this._stop(id);
        if (this._updateId)
            global.compositor.get_laters().remove(this._updateId);
        this._updateId = 0;
        this._dirty.clear();
    }
}
