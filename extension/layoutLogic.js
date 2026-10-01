/** Pure layout calculations kept free of Shell imports for unit testing. */

export const MIN_WIDTH = 160;
export const MIN_HEIGHT = 90;
// Grid and magnet distance are live: Customize → Desktop & Widgets sets them
// through setLayoutTuning(), and importers see the new values (ES module
// exports are bindings, not copies).
export let GRID_SIZE = 8;
export const WIDGET_GAP = 8;
export let SNAP_DISTANCE = 12;

/** Apply the widget grid and magnet distance from desktop.json. */
export function setLayoutTuning({grid = 8, snap_distance: snap = 12} = {}) {
    GRID_SIZE = Number.isFinite(grid) ? Math.max(4, Math.min(32, Math.round(grid))) : 8;
    SNAP_DISTANCE = Number.isFinite(snap) ? Math.max(0, Math.min(48, Math.round(snap))) : 12;
}

function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(value, maximum));
}

/** Snap a coordinate to the nearest valid magnetic target, then to the grid. */
function snapCoordinate(value, origin, minimum, maximum, targets = []) {
    const bounded = clamp(value, minimum, maximum);
    let nearest = null;
    let distance = SNAP_DISTANCE + 1;

    for (const target of targets) {
        if (!Number.isFinite(target) || target < minimum || target > maximum)
            continue;
        const candidateDistance = Math.abs(bounded - target);
        if (candidateDistance <= SNAP_DISTANCE && candidateDistance < distance) {
            nearest = target;
            distance = candidateDistance;
        }
    }

    if (nearest !== null)
        return Math.round(nearest);
    return Math.round(clamp(
        origin + Math.round((bounded - origin) / GRID_SIZE) * GRID_SIZE,
        minimum, maximum));
}

function horizontalMoveTargets(width, peers) {
    const targets = [];
    for (const peer of peers) {
        const right = peer.x + peer.width;
        targets.push(
            peer.x,                              // aligned left edges
            right - width,                       // aligned right edges
            peer.x + (peer.width - width) / 2,   // aligned centres
            right + WIDGET_GAP,                  // immediately to its right
            peer.x - width - WIDGET_GAP);        // immediately to its left
    }
    return targets;
}

function verticalMoveTargets(height, peers) {
    const targets = [];
    for (const peer of peers) {
        const bottom = peer.y + peer.height;
        targets.push(
            peer.y,                               // aligned top edges
            bottom - height,                      // aligned bottom edges
            peer.y + (peer.height - height) / 2,  // aligned centres
            bottom + WIDGET_GAP,                  // immediately below it
            peer.y - height - WIDGET_GAP);        // immediately above it
    }
    return targets;
}

/** Keep the whole card on its monitor without otherwise changing its layout. */
export function clampPosition(monitor, x, y, width, height) {
    const maxX = Math.max(monitor.x, monitor.x + monitor.width - width);
    const maxY = Math.max(monitor.y, monitor.y + monitor.height - height);
    return [
        Math.round(clamp(x, monitor.x, maxX)),
        Math.round(clamp(y, monitor.y, maxY)),
    ];
}

/** Snap a moved card to its monitor grid, screen edges, and nearby cards. */
export function settlePosition(monitor, x, y, width, height, peers = []) {
    const maxX = Math.max(monitor.x, monitor.x + monitor.width - width);
    const maxY = Math.max(monitor.y, monitor.y + monitor.height - height);
    const sx = snapCoordinate(x, monitor.x, monitor.x, maxX, [
        monitor.x, maxX, ...horizontalMoveTargets(width, peers),
    ]);
    const sy = snapCoordinate(y, monitor.y, monitor.y, maxY, [
        monitor.y, maxY, ...verticalMoveTargets(height, peers),
    ]);
    return [sx, sy];
}

function horizontalResizeTargets(peers, horizontal, anchoredEdge) {
    const targets = [];
    for (const peer of peers) {
        const right = peer.x + peer.width;
        targets.push(peer.x, right);
        if (horizontal < 0) {
            targets.push(right + WIDGET_GAP, anchoredEdge - peer.width);
        } else {
            targets.push(peer.x - WIDGET_GAP, anchoredEdge + peer.width);
        }
    }
    return targets;
}

function verticalResizeTargets(peers, vertical, anchoredEdge) {
    const targets = [];
    for (const peer of peers) {
        const bottom = peer.y + peer.height;
        targets.push(peer.y, bottom);
        if (vertical < 0) {
            targets.push(bottom + WIDGET_GAP, anchoredEdge - peer.height);
        } else {
            targets.push(peer.y - WIDGET_GAP, anchoredEdge + peer.height);
        }
    }
    return targets;
}

/** Snap the moving edges of a resized card while keeping opposite edges fixed. */
export function settleResize(
    monitor, x, y, width, height, horizontal, vertical, peers = []) {
    let left = x;
    let right = x + width;
    let top = y;
    let bottom = y + height;
    const monitorRight = monitor.x + monitor.width;
    const monitorBottom = monitor.y + monitor.height;

    if (horizontal < 0) {
        left = snapCoordinate(left, monitor.x, monitor.x, right - MIN_WIDTH, [
            monitor.x,
            ...horizontalResizeTargets(peers, horizontal, right),
        ]);
    } else if (horizontal > 0) {
        right = snapCoordinate(right, monitor.x, left + MIN_WIDTH, monitorRight, [
            monitorRight,
            ...horizontalResizeTargets(peers, horizontal, left),
        ]);
    }

    if (vertical < 0) {
        top = snapCoordinate(top, monitor.y, monitor.y, bottom - MIN_HEIGHT, [
            monitor.y,
            ...verticalResizeTargets(peers, vertical, bottom),
        ]);
    } else if (vertical > 0) {
        bottom = snapCoordinate(bottom, monitor.y, top + MIN_HEIGHT, monitorBottom, [
            monitorBottom,
            ...verticalResizeTargets(peers, vertical, top),
        ]);
    }

    return [
        Math.round(left), Math.round(top),
        Math.round(right - left), Math.round(bottom - top),
    ];
}

/** Number of complete, evenly-spaced rows that fit in an allocated list. */
export function pageSizeForHeight(
    availableHeight, rowHeight, spacing = 0, fallback = 1) {
    const safeFallback = Math.max(1, Math.floor(fallback));
    if (!Number.isFinite(availableHeight) || availableHeight <= 0 ||
        !Number.isFinite(rowHeight) || rowHeight <= 0)
        return safeFallback;

    const gap = Number.isFinite(spacing) ? Math.max(0, spacing) : 0;
    return Math.max(1, Math.floor((availableHeight + gap) / (rowHeight + gap)));
}

/** Pixel width for a conventional left-to-right percentage bar. */
export function meterFillWidth(trackWidth, value) {
    const width = Number.isFinite(trackWidth) ? Math.max(0, trackWidth) : 0;
    const percent = Number.isFinite(value) ? clamp(value, 0, 100) : 0;
    return Math.round(width * percent / 100);
}
