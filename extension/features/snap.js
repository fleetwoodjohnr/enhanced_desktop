import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {snapRect, zoneAt} from '../tilingLogic.js';
import {isAppWindow, unmaximizeAny} from '../windowInfo.js';

const POLL_MS = 40;
const MOVE_OPS = new Set([Meta.GrabOp.MOVING, Meta.GrabOp.MOVING_UNCONSTRAINED]);

/**
 * Snapping with gaps and to quarters, replacing GNOME's edge tiling while on
 * (the app switches GNOME's off in the same change, so the two never fight).
 * Tiling switches GNOME's off too, so dragging snaps then as well: for the
 * windows tiling leaves free.
 *
 * Dragging a window to a screen edge or corner shows where it will go and
 * puts it there on release; dragging it away again gives back its size.
 * The snap shortcuts work whether or not dragging is on.
 */
export class Snapping {
    constructor(isTiled = () => false) {
        this._isTiled = isTiled;
        this._values = {};
        this._tilingOn = false;
        this._dragging = null;
        this._zone = null;
        this._pollId = 0;
        this._preview = null;
        this._restore = new WeakMap();
        global.display.connectObject(
            'grab-op-begin', (_display, window, op) => this._begin(window, op),
            'grab-op-end', (_display, window, op) => this._end(window, op), this);
    }

    update({snap = {}, tiling = {}}) {
        this._values = snap;
        this._tilingOn = tiling.enabled === true;
    }

    get _active() {
        return this._values.quarters === true || (this._values.gaps ?? 0) > 0 || this._tilingOn;
    }

    get _gap() {
        return Number.isFinite(this._values.gaps) ? Math.max(0, Math.min(32, this._values.gaps)) : 0;
    }

    /** Snap the focused window (keyboard shortcuts). */
    snapFocused(zone) {
        const window = global.display.focus_window;
        if (isAppWindow(window) && !this._isTiled(window))
            this._snap(window, zone);
    }

    centerFocused() {
        const window = global.display.focus_window;
        if (!isAppWindow(window) || this._isTiled(window) || window.is_fullscreen())
            return;
        unmaximizeAny(window);
        const area = window.get_work_area_current_monitor();
        const frame = window.get_frame_rect();
        window.move_frame(true, area.x + Math.round((area.width - frame.width) / 2),
            area.y + Math.round((area.height - frame.height) / 2));
    }

    _snap(window, zone) {
        const area = window.get_work_area_current_monitor();
        if (zone === 'maximize' && this._gap === 0) {
            window.maximize();
            return;
        }
        const target = snapRect(zone, area, this._gap);
        if (!target)
            return;
        if (!this._restore.has(window)) {
            const frame = window.get_frame_rect();
            this._restore.set(window, {width: frame.width, height: frame.height});
        }
        unmaximizeAny(window);
        window.move_resize_frame(true, target.x, target.y, target.width, target.height);
    }

    _begin(window, op) {
        if (!this._active || !isAppWindow(window) || !MOVE_OPS.has(op) || this._isTiled(window))
            return;
        this._dragging = window;
        this._zone = null;
        // A snapped window dragged away gets its own size back.
        const size = this._restore.get(window);
        if (size) {
            this._restore.delete(window);
            const [px] = global.get_pointer();
            const frame = window.get_frame_rect();
            const offset = (px - frame.x) / Math.max(1, frame.width);
            window.move_resize_frame(true, Math.round(px - offset * size.width), frame.y,
                size.width, size.height);
        }
        if (this._pollId)
            GLib.source_remove(this._pollId);
        this._pollId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POLL_MS, () => {
            this._track();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _track() {
        const window = this._dragging;
        if (!window)
            return;
        const [px, py] = global.get_pointer();
        const index = global.display.get_current_monitor();
        const monitor = Main.layoutManager.monitors[index];
        const zone = zoneAt(px, py, monitor, {quarters: this._values.quarters === true});
        if (zone === this._zone)
            return;
        this._zone = zone;
        if (!zone) {
            this._hidePreview();
            return;
        }
        const area = Main.layoutManager.getWorkAreaForMonitor(index);
        const target = snapRect(zone, area, this._gap);
        if (!this._preview) {
            this._preview = new St.Widget({style_class: 'df-snap-preview', reactive: false});
            global.window_group.add_child(this._preview);
        }
        global.window_group.set_child_below_sibling(this._preview, window.get_compositor_private());
        this._preview.set_position(target.x, target.y);
        this._preview.set_size(target.width, target.height);
        this._preview.show();
    }

    _hidePreview() {
        this._preview?.hide();
    }

    _end(window) {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        const zone = this._zone;
        const dragged = this._dragging;
        this._dragging = null;
        this._zone = null;
        this._hidePreview();
        if (dragged && dragged === window && zone)
            this._snap(window, zone);
    }

    destroy() {
        global.display.disconnectObject(this);
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        this._preview?.destroy();
        this._preview = null;
    }
}
