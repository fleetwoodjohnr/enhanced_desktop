import Meta from 'gi://Meta';

import {actionsFor, NEUTRAL_ACTIONS} from '../rulesLogic.js';
import {afterFirstFrame, describeWindow, isAppWindow, maximizeFlags, unmaximizeAny} from '../windowInfo.js';

// The rules last applied to open windows. It outlives the module, which is
// rebuilt after every unlock: without it, every unlock would re-centre,
// resize and move every window a rule matches.
let appliedKey = '';

/**
 * Window rules: workspace, display, size, centring, maximize, fullscreen,
 * always on top and every workspace. (Opacity and "no effects" are read by
 * the window effects module, floating and tiled by tiling.)
 *
 * Rules apply once a new window has drawn its first frame -- earlier, Mutter
 * is still placing it and would undo a move -- and to open windows whenever
 * the rules change.
 */
export class WindowRules {
    constructor() {
        this._rules = [];
        this._waiting = new Set();
        global.display.connectObject('window-created', (_display, window) => this._created(window), this);
    }

    update({rules = {}}) {
        const list = Array.isArray(rules.list) ? rules.list : [];
        const key = JSON.stringify(list);
        const changed = key !== appliedKey;
        this._rules = list;
        appliedKey = key;
        if (changed && list.length) {
            for (const actor of global.get_window_actors())
                this._apply(actor.meta_window);
        }
    }

    _created(window) {
        if (!this._rules.length || !isAppWindow(window))
            return;
        const cancel = afterFirstFrame(window, () => {
            this._waiting.delete(cancel);
            this._apply(window);
        });
        this._waiting.add(cancel);
    }

    /** The actions that apply to a window, for other modules and tests. */
    actionsFor(window) {
        return actionsFor(this._rules, describeWindow(window));
    }

    _apply(window) {
        if (!window || !isAppWindow(window) || !window.get_compositor_private())
            return;
        let actions;
        try {
            actions = this.actionsFor(window);
        } catch (error) {
            logError(error, 'desktop-forge: could not read window rules');
            return;
        }
        if (JSON.stringify(actions) === JSON.stringify(NEUTRAL_ACTIONS))
            return;
        const manager = global.workspace_manager;
        if (actions.workspace > 0 && !window.is_on_all_workspaces()) {
            const index = Math.min(actions.workspace, manager.n_workspaces) - 1;
            if (window.get_workspace()?.index() !== index)
                window.change_workspace_by_index(index, false);
        }
        if (actions.monitor >= 0 && actions.monitor < global.display.get_n_monitors() &&
            window.get_monitor() !== actions.monitor)
            window.move_to_monitor(actions.monitor);
        if (actions.sticky && !window.is_on_all_workspaces())
            window.stick();
        if (actions.above && !window.is_above())
            window.make_above();
        if ((actions.width > 0 && actions.height > 0) || actions.center)
            this._place(window, actions);
        if (actions.maximize && maximizeFlags(window) !== Meta.MaximizeFlags.BOTH && window.can_maximize())
            window.maximize();
        if (actions.fullscreen && !window.is_fullscreen())
            window.make_fullscreen();
    }

    _place(window, actions) {
        if (window.is_fullscreen())
            return;
        unmaximizeAny(window);
        const area = window.get_work_area_current_monitor();
        const frame = window.get_frame_rect();
        const width = actions.width > 0 ? Math.min(actions.width, area.width) : frame.width;
        const height = actions.height > 0 ? Math.min(actions.height, area.height) : frame.height;
        let {x, y} = frame;
        if (actions.center) {
            x = area.x + Math.round((area.width - width) / 2);
            y = area.y + Math.round((area.height - height) / 2);
        }
        window.move_resize_frame(true, x, y, width, height);
    }

    destroy() {
        global.display.disconnectObject(this);
        for (const cancel of this._waiting)
            cancel();
        this._waiting.clear();
    }
}

