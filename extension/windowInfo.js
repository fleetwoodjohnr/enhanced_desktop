import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

/** A window as rules see it: the same fields desktopBridge.js reports. */
export function describeWindow(window) {
    const app = Shell.WindowTracker.get_default().get_window_app(window);
    return {
        desktop_id: app?.get_id?.() ?? '',
        title: window.get_title?.() ?? '',
        wm_class: window.get_wm_class?.() ?? '',
        wm_class_instance: window.get_wm_class_instance?.() ?? '',
        type: window.get_window_type() === Meta.WindowType.NORMAL ? 'normal' : 'dialog',
    };
}

/** Ordinary app windows: what effects, rules and tiling act on. */
export function isAppWindow(window) {
    if (!window || window.is_skip_taskbar?.() || window.is_override_redirect?.())
        return false;
    const type = window.get_window_type();
    return type === Meta.WindowType.NORMAL || type === Meta.WindowType.DIALOG ||
        type === Meta.WindowType.MODAL_DIALOG;
}

/**
 * Which ways a window is maximized. Mutter 18's is_maximized() is true only
 * for both; a window it tiled to half the screen is maximized vertically.
 */
export function maximizeFlags(window) {
    return window.get_maximize_flags?.() ?? (window.is_maximized() ? Meta.MaximizeFlags.BOTH : 0);
}

/** Unmaximize a window maximized in either direction, so it can be moved. */
export function unmaximizeAny(window) {
    if (maximizeFlags(window))
        window.unmaximize();
}

/**
 * Call `done(window)` once a new window has drawn its first frame, and one
 * more turn of the main loop after (Mutter is still placing it at
 * first-frame and would undo a move). Returns a function that cancels the
 * wait; modules call it when destroyed, so nothing runs after they are.
 */
export function afterFirstFrame(window, done, tries = 10) {
    const token = {};
    let idle = 0;
    let actor = null;
    const soon = callback => {
        idle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            idle = 0;
            callback();
            return GLib.SOURCE_REMOVE;
        });
    };
    const wait = left => {
        actor = window.get_compositor_private();
        if (!actor) {
            // Not composited yet: a window closed that early never will be.
            if (left > 0)
                soon(() => wait(left - 1));
            return;
        }
        actor.connectObject(
            'first-frame', () => {
                actor.disconnectObject(token);
                actor = null;
                soon(() => done(window));
            },
            'destroy', () => {
                actor = null;
            }, token);
    };
    wait(tries);
    return () => {
        if (idle)
            GLib.source_remove(idle);
        idle = 0;
        actor?.disconnectObject(token);
        actor = null;
    };
}
