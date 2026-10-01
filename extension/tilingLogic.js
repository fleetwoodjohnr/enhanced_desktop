/**
 * Tiling and snapping geometry, free of Shell imports for unit testing.
 *
 * Rectangles are {x, y, width, height} in logical pixels. Every function
 * rounds its results, so windows land on whole pixels and a layout computed
 * twice gives identical rectangles (which is how tiling recognises its own
 * moves).
 */

export const LAYOUTS = ['master', 'dwindle', 'centered', 'scrolling', 'columns', 'rows', 'grid', 'monocle'];

function rect(x, y, width, height) {
    const left = Math.round(x);
    const top = Math.round(y);
    return {x: left, y: top, width: Math.max(1, Math.round(x + width) - left),
        height: Math.max(1, Math.round(y + height) - top)};
}

/** Split `length` into `count` parts separated by `gap`. */
function split(start, length, count, gap) {
    const size = (length - gap * (count - 1)) / count;
    return Array.from({length: count}, (_, i) => [start + i * (size + gap), size]);
}

/** The work area less the outer gaps, and the gap between windows. */
function inset(area, count, options) {
    const alone = count === 1 && options.smart !== false;
    const outer = alone ? 0 : Math.max(0, options.outer ?? 0);
    return {
        x: area.x + outer, y: area.y + outer,
        width: Math.max(1, area.width - 2 * outer), height: Math.max(1, area.height - 2 * outer),
        gap: alone ? 0 : Math.max(0, options.inner ?? 0),
    };
}

function clampRatio(ratio, low = 0.2, high = 0.8) {
    return Math.max(low, Math.min(high, ratio));
}

/**
 * Where each of `count` windows goes in `area`.
 *
 * @param {string} layout - one of LAYOUTS
 * @param {number} count
 * @param {{x, y, width, height}} area - the monitor's work area
 * @param {{inner?: number, outer?: number, smart?: boolean, ratio?: number,
 *   columns?: number, first?: number}} options - `columns` and `first` are
 *   the scrolling layout's columns on screen and first column shown
 */
export function layoutRects(layout, count, area, options = {}) {
    if (count <= 0)
        return [];
    const {x, y, width, height, gap} = inset(area, count, options);
    const ratio = clampRatio(options.ratio ?? 0.55);
    if (count === 1 || layout === 'monocle')
        return Array.from({length: count}, () => rect(x, y, width, height));

    switch (layout) {
    case 'columns':
        return split(x, width, count, gap).map(([left, w]) => rect(left, y, w, height));
    case 'rows':
        return split(y, height, count, gap).map(([top, h]) => rect(x, top, width, h));
    case 'grid': {
        const columns = Math.ceil(Math.sqrt(count));
        const rows = Math.ceil(count / columns);
        const result = [];
        split(y, height, rows, gap).forEach(([top, h], row) => {
            const inRow = row === rows - 1 ? count - columns * (rows - 1) : columns;
            for (const [left, w] of split(x, width, inRow, gap))
                result.push(rect(left, top, w, h));
        });
        return result;
    }
    case 'dwindle': {
        // Each new window splits the one before it, as when every window
        // opens from the last: the chain spirals into the corner.
        const items = Array.from({length: count}, (_, i) => i);
        const placed = dwindleRects(dwindleSync(null, items), area, options);
        return items.map(i => placed.get(i));
    }
    case 'scrolling': {
        // A strip of equal columns, `columns` of them on screen. Columns
        // scrolled out of view wait under the edge column instead of off
        // the display, where Mutter would pull them back or onto a
        // neighbouring monitor.
        const columns = Math.max(1, Math.min(count, Math.round(options.columns ?? 2)));
        const slots = split(x, width, columns, gap);
        const first = Math.max(0, Math.min(count - columns, Math.round(options.first ?? 0)));
        return Array.from({length: count}, (_, i) => {
            const [left, w] = slots[Math.max(0, Math.min(columns - 1, i - first))];
            return rect(left, y, w, height);
        });
    }
    case 'centered':
        if (count > 2) {
            // The main window in the middle, the rest alternating right
            // and left of it.
            const main = (width - 2 * gap) * ratio;
            const side = (width - 2 * gap - main) / 2;
            const result = [rect(x + side + gap, y, main, height)];
            const right = [];
            const left = [];
            for (let i = 1; i < count; i++)
                (i % 2 === 1 ? right : left).push(i);
            const rightX = x + side + gap + main + gap;
            split(y, height, right.length, gap).forEach(([top, h], k) => {
                result[right[k]] = rect(rightX, top, x + width - rightX, h);
            });
            split(y, height, left.length, gap).forEach(([top, h], k) => {
                result[left[k]] = rect(x, top, side, h);
            });
            return result;
        }
        // Two windows: main and stack.
        // falls through
    default: {
        // Main and stack: the first window on the left, the rest stacked.
        const main = (width - gap) * ratio;
        const stack = split(y, height, count - 1, gap);
        return [rect(x, y, main, height),
            ...stack.map(([top, h]) => rect(x + main + gap, top, width - main - gap, h))];
    }
    }
}

/**
 * The main area ratio that makes window `index` of `count` as wide as
 * `frame`, the inverse of layoutRects: what a mouse resize sets. Null when
 * the layout has no main area to resize.
 */
export function resizedRatio(layout, count, index, frame, area, options = {}) {
    if (count < 2)
        return null;
    const {width, gap} = inset(area, count, options);
    if (layout === 'centered' && count > 2) {
        const room = Math.max(1, width - 2 * gap);
        return clampRatio(index === 0 ? frame.width / room : 1 - 2 * frame.width / room);
    }
    if (layout === 'master' || layout === 'centered') {
        const room = Math.max(1, width - gap);
        return clampRatio(index === 0 ? frame.width / room : (room - frame.width) / room);
    }
    return null;
}

// -- Dwindle: a binary tree per workspace -----------------------------------
//
// A leaf is {item}; a split is {a, b, ratio}, `a` taking `ratio` of the box.
// A new window splits the leaf of the window it opened from, and each split
// cuts its box along the longer side, recomputed every time, as Hyprland's
// dwindle does.

function isLeaf(node) {
    return 'item' in node;
}

function leaves(node, out = []) {
    if (node) {
        if (isLeaf(node))
            out.push(node);
        else
            leaves(node.b, leaves(node.a, out));
    }
    return out;
}

function prune(node, keep) {
    if (!node)
        return null;
    if (isLeaf(node))
        return keep.has(node.item) ? node : null;
    node.a = prune(node.a, keep);
    node.b = prune(node.b, keep);
    if (!node.a)
        return node.b;
    return node.b ? node : node.a;
}

/** The leaf holding `item`, and the split above it. */
function find(node, item, parent = null) {
    if (!node)
        return null;
    if (isLeaf(node))
        return node.item === item ? {leaf: node, parent} : null;
    return find(node.a, item, node) ?? find(node.b, item, node);
}

/**
 * Bring a tree up to date with `items`: leaves of items that are gone are
 * removed (their sibling takes their room) and new items split the leaf of
 * the first of `anchors` in the tree, or else the last leaf. New items after
 * the first split the one before, so several at once spiral.
 */
export function dwindleSync(tree, items, anchors = []) {
    let root = prune(tree, new Set(items));
    const present = new Set(leaves(root).map(leaf => leaf.item));
    let target = anchors.find(anchor => present.has(anchor));
    for (const item of items) {
        if (present.has(item))
            continue;
        if (!root) {
            root = {item};
        } else {
            const all = leaves(root);
            const leaf = all.find(l => l.item === target) ?? all[all.length - 1];
            const old = leaf.item;
            delete leaf.item;
            Object.assign(leaf, {a: {item: old}, b: {item}, ratio: 0.5});
        }
        present.add(item);
        target = item;
    }
    return root;
}

/**
 * Where each item of the tree goes, as a Map. Items not in `visible` (when
 * given) take no room: a minimized window keeps its place in the tree while
 * its sibling fills the space. Each node remembers its box for resizing.
 */
export function dwindleRects(tree, area, options = {}, visible = null) {
    const result = new Map();
    const shows = node => isLeaf(node) ? !visible || visible.has(node.item) : shows(node.a) || shows(node.b);
    const count = leaves(tree).filter(leaf => !visible || visible.has(leaf.item)).length;
    if (!count)
        return result;
    const {x, y, width, height, gap} = inset(area, count, options);
    const place = (node, box) => {
        node.box = box;
        if (isLeaf(node)) {
            result.set(node.item, rect(box.x, box.y, box.width, box.height));
            return;
        }
        const showA = shows(node.a);
        if (!showA || !shows(node.b)) {
            place(showA ? node.a : node.b, box);
            return;
        }
        node.vertical = box.width >= box.height;
        const length = node.vertical ? box.width : box.height;
        const first = (length - gap) * node.ratio;
        const rest = length - first - gap;
        if (node.vertical) {
            place(node.a, {x: box.x, y: box.y, width: first, height: box.height});
            place(node.b, {x: box.x + first + gap, y: box.y, width: rest, height: box.height});
        } else {
            place(node.a, {x: box.x, y: box.y, width: box.width, height: first});
            place(node.b, {x: box.x, y: box.y + first + gap, width: box.width, height: rest});
        }
    };
    place(tree, {x, y, width, height});
    return result;
}

/** Trade the places of two items. */
export function dwindleSwap(tree, a, b) {
    const one = find(tree, a)?.leaf;
    const two = find(tree, b)?.leaf;
    if (one && two)
        [one.item, two.item] = [two.item, one.item];
}

/** Grow (step > 0) or shrink an item's share of the split above it. */
// ponytail: only the split directly above; growing along the other axis needs the nearest ancestor split on that axis.
export function dwindleNudge(tree, item, step) {
    const found = find(tree, item);
    if (!found?.parent)
        return false;
    const {leaf, parent} = found;
    parent.ratio = clampRatio(parent.ratio + (parent.a === leaf ? step : -step), 0.1, 0.9);
    return true;
}

/** Set the split above an item so it is as big as `frame` (a mouse resize). */
export function dwindleRatio(tree, item, frame, gap = 0) {
    const found = find(tree, item);
    const parent = found?.parent;
    if (!parent?.box)
        return false;
    const length = (parent.vertical ? parent.box.width : parent.box.height) - gap;
    const size = parent.vertical ? frame.width : frame.height;
    const share = size / Math.max(1, length);
    parent.ratio = clampRatio(parent.a === found.leaf ? share : 1 - share, 0.1, 0.9);
    return true;
}

/** The first column the scrolling layout shows, so the focused one is in view. */
export function scrollFirst(first, focus, count, columns) {
    const shown = Math.max(1, Math.min(count, columns));
    const wanted = focus >= 0 ? Math.max(focus - shown + 1, Math.min(first, focus)) : first;
    return Math.max(0, Math.min(count - shown, wanted));
}

/**
 * The window a focus or swap shortcut moves to. Monocle and scrolling step
 * through the order (their windows share rectangles); the rest go by
 * direction on screen.
 */
export function stepIndex(layout, rects, from, direction) {
    if (layout === 'monocle' || layout === 'scrolling') {
        const delta = {left: -1, right: 1, up: layout === 'monocle' ? -1 : 0, down: layout === 'monocle' ? 1 : 0}[direction];
        const to = from + (delta ?? 0);
        return delta && from >= 0 && to >= 0 && to < rects.length ? to : -1;
    }
    return neighbour(rects, from, direction);
}

export const SNAP_ZONES = ['left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right', 'maximize'];

/** The rectangle a snap zone fills, `gap` pixels from the edges and each other. */
export function snapRect(zone, area, gap = 0) {
    const g = Math.max(0, gap);
    const halfW = (area.width - 3 * g) / 2;
    const halfH = (area.height - 3 * g) / 2;
    const left = area.x + g;
    const right = area.x + 2 * g + halfW;
    const top = area.y + g;
    const bottom = area.y + 2 * g + halfH;
    switch (zone) {
    case 'left': return rect(left, top, halfW, area.height - 2 * g);
    case 'right': return rect(right, top, halfW, area.height - 2 * g);
    case 'top-left': return rect(left, top, halfW, halfH);
    case 'top-right': return rect(right, top, halfW, halfH);
    case 'bottom-left': return rect(left, bottom, halfW, halfH);
    case 'bottom-right': return rect(right, bottom, halfW, halfH);
    case 'maximize': return rect(left, top, area.width - 2 * g, area.height - 2 * g);
    default: return null;
    }
}

/**
 * Which zone a window dragged to (px, py) snaps into, or null.
 *
 * `monitor` is the whole display: the edges that count are the screen's,
 * so a hidden top bar does not move the trigger. Corners need `quarters`.
 */
export function zoneAt(px, py, monitor, {threshold = 8, corner = 64, quarters = true} = {}) {
    if (!monitor || px < monitor.x || py < monitor.y ||
        px >= monitor.x + monitor.width || py >= monitor.y + monitor.height)
        return null;
    const atLeft = px < monitor.x + threshold;
    const atRight = px >= monitor.x + monitor.width - threshold;
    const atTop = py < monitor.y + threshold;
    const nearTop = py < monitor.y + corner;
    const nearBottom = py >= monitor.y + monitor.height - corner;
    if (atLeft || atRight) {
        const side = atLeft ? 'left' : 'right';
        if (quarters && nearTop)
            return `top-${side}`;
        if (quarters && nearBottom)
            return `bottom-${side}`;
        return side;
    }
    if (atTop) {
        if (quarters && px < monitor.x + corner)
            return 'top-left';
        if (quarters && px >= monitor.x + monitor.width - corner)
            return 'top-right';
        return 'maximize';
    }
    return null;
}

/**
 * The index of the rectangle nearest `from` in a direction, or -1.
 * Only rectangles lying that way count; among them the closest centre wins,
 * preferring ones that overlap on the other axis.
 */
export function neighbour(rects, from, direction) {
    const origin = rects[from];
    if (!origin)
        return -1;
    const cx = r => r.x + r.width / 2;
    const cy = r => r.y + r.height / 2;
    let best = -1;
    let bestScore = Infinity;
    rects.forEach((candidate, index) => {
        if (index === from)
            return;
        const dx = cx(candidate) - cx(origin);
        const dy = cy(candidate) - cy(origin);
        const ahead = {left: dx < -1, right: dx > 1, up: dy < -1, down: dy > 1}[direction];
        if (!ahead)
            return;
        const horizontal = direction === 'left' || direction === 'right';
        const overlap = horizontal
            ? candidate.y < origin.y + origin.height && origin.y < candidate.y + candidate.height
            : candidate.x < origin.x + origin.width && origin.x < candidate.x + candidate.width;
        const score = (horizontal ? Math.abs(dx) + Math.abs(dy) * 2 : Math.abs(dy) + Math.abs(dx) * 2) +
            (overlap ? 0 : 100000);
        if (score < bestScore) {
            bestScore = score;
            best = index;
        }
    });
    return best;
}

/** Where a new window joins an ordered group, `anchorIndex` being the window it opened from. */
export function insertionIndex(length, anchorIndex, placement) {
    if (placement === 'master')
        return 0;
    if (placement === 'after_focus' && anchorIndex >= 0 && anchorIndex < length)
        return anchorIndex + 1;
    return length;
}

/** The index of the rectangle containing a point, or -1. */
export function rectAt(rects, px, py, skip = -1) {
    return rects.findIndex((r, index) => index !== skip &&
        px >= r.x && px < r.x + r.width && py >= r.y && py < r.y + r.height);
}

/**
 * Where a window of `size` goes for the tile `target`: the tile's corner,
 * moved back just enough to keep the window inside `area`. Only a window
 * bigger than its tile (an app with a large minimum size) moves back.
 */
export function keptInside(target, size, area) {
    return {
        x: Math.max(area.x, Math.min(target.x, area.x + area.width - size.width)),
        y: Math.max(area.y, Math.min(target.y, area.y + area.height - size.height)),
    };
}

export function sameRect(a, b, tolerance = 0) {
    return !!a && !!b && Math.abs(a.x - b.x) <= tolerance && Math.abs(a.y - b.y) <= tolerance &&
        Math.abs(a.width - b.width) <= tolerance && Math.abs(a.height - b.height) <= tolerance;
}
