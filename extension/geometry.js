/**
 * Where a widget is allowed to sit and how big it is allowed to be.
 *
 * Kept apart from both the widgets and the edit overlay because the extension
 * needs the same monitor lookup when it restores a saved layout, and the
 * overlay needs it again on every drag.
 */
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export {
    GRID_SIZE, MIN_HEIGHT, MIN_WIDTH, SNAP_DISTANCE, WIDGET_GAP,
    clampPosition, settlePosition, settleResize,
} from './layoutLogic.js';

export function monitorForEntry(entry) {
    return Main.layoutManager.monitors[entry.monitor] ?? Main.layoutManager.primaryMonitor;
}

/**
 * The part of an entry's monitor that desktop content may occupy.
 *
 * Unlike the raw monitor rectangle this excludes the GNOME top panel and any
 * dock or panel that reserves screen space. Work areas belong to workspaces,
 * so always resolve this against the active workspace instead of caching it.
 */
export function workAreaForEntry(entry) {
    const monitor = monitorForEntry(entry);
    if (!monitor)
        return null;
    const workspace = global.workspace_manager.get_active_workspace();
    return workspace?.get_work_area_for_monitor(monitor.index) ?? monitor;
}
