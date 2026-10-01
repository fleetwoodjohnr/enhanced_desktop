import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const DIRECTIONS = ['up', 'down', 'left', 'right'];
const SWIPE_DISTANCE = 60;
const PHASE = Clutter.TouchpadGesturePhase;

/**
 * Three- and four-finger swipes and three-finger pinches.
 *
 * Shell handles 3+ finger swipes on the stage as they bubble up; this
 * handler sees them first, while they are captured. As soon as any swipe
 * with a given number of fingers is changed from "GNOME default", every
 * swipe with that many fingers is handled here -- the ones still on default
 * then do GNOME's own action (overview, app grid, next workspace), without
 * the finger-following animation. Two-finger gestures are never touched:
 * apps use them to scroll and zoom.
 */
export class Gestures {
    constructor(context) {
        this._context = context;
        this._values = {};
        this._swipe = null;
        this._pinch = null;
        this._hidden = [];
        global.stage.connectObject('captured-event::touchpad', (_stage, event) => this._event(event), this);
    }

    update({gestures = {}}) {
        this._values = gestures;
    }

    _mapping(name) {
        return this._values[name] ?? 'default';
    }

    _taken(fingers) {
        const prefix = fingers >= 4 ? 'four' : 'three';
        return DIRECTIONS.some(direction => this._mapping(`${prefix}_${direction}`) !== 'default');
    }

    _event(event) {
        if (Main.actionMode !== Shell.ActionMode.NORMAL && Main.actionMode !== Shell.ActionMode.OVERVIEW)
            return Clutter.EVENT_PROPAGATE;
        const type = event.type();
        if (type === Clutter.EventType.TOUCHPAD_SWIPE)
            return this._swiped(event);
        if (type === Clutter.EventType.TOUCHPAD_PINCH)
            return this._pinched(event);
        return Clutter.EVENT_PROPAGATE;
    }

    _swiped(event) {
        const phase = event.get_gesture_phase();
        const fingers = event.get_touchpad_gesture_finger_count();
        if (phase === PHASE.BEGIN)
            this._swipe = fingers >= 3 && this._taken(fingers) ? {fingers, dx: 0, dy: 0} : null;
        if (!this._swipe)
            return Clutter.EVENT_PROPAGATE;
        if (phase === PHASE.UPDATE) {
            const [dx, dy] = event.get_gesture_motion_delta_unaccelerated();
            this._swipe.dx += dx;
            this._swipe.dy += dy;
        } else if (phase === PHASE.END) {
            const {fingers: count, dx, dy} = this._swipe;
            this._swipe = null;
            if (Math.hypot(dx, dy) >= SWIPE_DISTANCE) {
                const direction = Math.abs(dx) > Math.abs(dy)
                    ? dx < 0 ? 'left' : 'right' : dy < 0 ? 'up' : 'down';
                const name = `${count >= 4 ? 'four' : 'three'}_${direction}`;
                const action = this._mapping(name);
                this.run(action === 'default' ? gnomeDefault(direction) : action);
            }
        } else if (phase === PHASE.CANCEL) {
            this._swipe = null;
        }
        return Clutter.EVENT_STOP;
    }

    _pinched(event) {
        const phase = event.get_gesture_phase();
        if (phase === PHASE.BEGIN) {
            const mapped = ['pinch_in', 'pinch_out'].some(name => !['default', 'none'].includes(this._mapping(name)));
            this._pinch = event.get_touchpad_gesture_finger_count() >= 3 && mapped ? {scale: 1} : null;
        }
        if (!this._pinch)
            return Clutter.EVENT_PROPAGATE;
        if (phase === PHASE.UPDATE) {
            this._pinch.scale = event.get_gesture_pinch_scale();
        } else if (phase === PHASE.END) {
            const {scale} = this._pinch;
            this._pinch = null;
            if (scale < 0.8)
                this.run(this._mapping('pinch_in'));
            else if (scale > 1.25)
                this.run(this._mapping('pinch_out'));
        } else if (phase === PHASE.CANCEL) {
            this._pinch = null;
        }
        return Clutter.EVENT_STOP;
    }

    /** Do one gesture action; also used by the smoke test. */
    run(action) {
        const time = global.get_current_time();
        const window = global.display.focus_window;
        const workspace = global.workspace_manager.get_active_workspace();
        switch (action) {
        case 'overview':
            Main.overview.toggle();
            break;
        case 'overview_up':
            if (!Main.overview.visible)
                Main.overview.show();
            else if (!Main.overview.dash.showAppsButton.checked)
                Main.overview.showApps();
            break;
        case 'overview_down':
            if (Main.overview.visible && Main.overview.dash.showAppsButton.checked)
                Main.overview.dash.showAppsButton.checked = false;
            else if (Main.overview.visible)
                Main.overview.hide();
            break;
        case 'app_grid':
            if (Main.overview.visible)
                Main.overview.hide();
            else
                Main.overview.showApps();
            break;
        case 'show_desktop':
            this._showDesktop(workspace, time);
            break;
        case 'workspace_next':
            workspace.get_neighbor(Meta.MotionDirection.RIGHT).activate(time);
            break;
        case 'workspace_previous':
            workspace.get_neighbor(Meta.MotionDirection.LEFT).activate(time);
            break;
        case 'maximize':
            if (window?.is_maximized())
                window.unmaximize();
            else if (window?.can_maximize())
                window.maximize();
            break;
        case 'minimize':
            window?.minimize();
            break;
        case 'close':
            window?.delete(time);
            break;
        case 'tiling':
            this._context.tiling()?.toggle();
            break;
        case 'tiling_layout':
            this._context.tiling()?.nextLayout();
            break;
        default:
            break;
        }
    }

    /** Minimize this workspace's windows; the next time, bring them back. */
    _showDesktop(workspace, time) {
        const hidden = this._hidden.filter(window => window.minimized && window.get_workspace() === workspace);
        if (hidden.length) {
            for (const window of hidden)
                window.unminimize();
            hidden.at(-1).activate(time);
            this._hidden = [];
            return;
        }
        this._hidden = workspace.list_windows().filter(window =>
            !window.minimized && window.get_window_type() === Meta.WindowType.NORMAL &&
            !window.is_skip_taskbar());
        for (const window of this._hidden)
            window.minimize();
    }

    destroy() {
        global.stage.disconnectObject(this);
        this._swipe = null;
        this._pinch = null;
    }
}

/** GNOME's own action for a swipe direction. */
function gnomeDefault(direction) {
    return {up: 'overview_up', down: 'overview_down', left: 'workspace_next', right: 'workspace_previous'}[direction];
}
