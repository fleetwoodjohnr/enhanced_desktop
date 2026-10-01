import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {unmaximizeAny} from '../windowInfo.js';

// The window in the scratchpad. It outlives the module, which is rebuilt
// after every unlock.
const pad = {window: null};

const WIDTH = 0.7;
const HEIGHT = 0.6;

/**
 * A drop-down scratchpad, like Hyprland's special workspace: one window,
 * kept minimized on every workspace and above the rest, that a shortcut
 * shows over whatever is open and hides again. Tiling leaves it alone
 * (always-on-top windows float), and it rejoins its old place in the layout
 * when sent back.
 */
export class Scratchpad {
    constructor() {
        if (pad.window && !pad.window.get_compositor_private())
            pad.window = null;
        this._watch();
    }

    get window() {
        return pad.window;
    }

    update() {
        // Nothing to configure: the shortcuts are in the extension's schema.
    }

    /** Put the focused window in the scratchpad, or take it back out. */
    send() {
        const window = global.display.focus_window;
        if (!window || window.get_window_type() !== Meta.WindowType.NORMAL)
            return;
        const current = pad.window;
        this._release();
        if (window === current)
            return;
        pad.window = window;
        this._watch();
        window.make_above();
        window.stick();
        window.minimize();
    }

    /** Show the scratchpad window over everything, or hide it. */
    toggle() {
        const window = pad.window;
        if (!window)
            return;
        if (!window.minimized && global.display.focus_window === window) {
            window.minimize();
            return;
        }
        const monitor = global.display.get_current_monitor();
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor);
        const width = Math.round(area.width * WIDTH);
        const height = Math.round(area.height * HEIGHT);
        window.unminimize();
        unmaximizeAny(window);
        if (window.get_monitor() !== monitor)
            window.move_to_monitor(monitor);
        window.move_resize_frame(true, area.x + Math.round((area.width - width) / 2), area.y + Math.round(area.height * 0.04),
            width, height);
        window.activate(global.get_current_time());
    }

    _watch() {
        pad.window?.connectObject('unmanaged', () => {
            pad.window = null;
        }, this);
    }

    _release() {
        const window = pad.window;
        if (!window)
            return;
        window.disconnectObject(this);
        pad.window = null;
        window.unmake_above();
        window.unstick();
        window.unminimize();
    }

    destroy() {
        pad.window?.disconnectObject(this);
    }
}
