import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {WindowMenu} from 'resource:///org/gnome/shell/ui/windowMenu.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

import {actionsFor} from '../rulesLogic.js';
import {
    LAYOUTS, dwindleNudge, dwindleRatio, dwindleRects, dwindleSwap, dwindleSync, insertionIndex,
    keptInside, layoutRects, rectAt, resizedRatio, sameRect, scrollFirst, stepIndex,
} from '../tilingLogic.js';
import {afterFirstFrame, describeWindow, isAppWindow, maximizeFlags, unmaximizeAny} from '../windowInfo.js';

const GLIDE_MS = 200;
// Focus View: the share of the work area a floated-up window takes.
const FOCUS_WIDTH = 0.7;
const FOCUS_HEIGHT = 0.8;
const MOVE_OPS = new Set([Meta.GrabOp.MOVING, Meta.GrabOp.MOVING_UNCONSTRAINED, Meta.GrabOp.KEYBOARD_MOVING]);

export const LAYOUT_NAMES = {
    master: 'Main and stack', dwindle: 'Dwindle', centered: 'Centered main', scrolling: 'Scrolling columns',
    columns: 'Columns', rows: 'Rows', grid: 'Grid', monocle: 'Monocle',
};

/**
 * Tiling state that outlives the module: window order, windows floated by
 * hand, per-workspace layouts, dwindle trees and scroll positions. The
 * module is torn down at the lock screen and rebuilt after it; without
 * this, every unlock would reshuffle the windows.
 */
const memory = {
    order: new Map(),     // group key -> [Meta.Window], including ones sitting out
    floating: new Set(),  // windows the user floated
    loose: new Set(),     // a lone window the user moved or resized; it tiles again when another joins
    layouts: new Map(),   // group key -> layout chosen with the shortcut or top bar
    ratios: new Map(),    // group key -> main area ratio
    trees: new Map(),     // group key -> dwindle tree
    scroll: new Map(),    // group key -> first column shown by the scrolling layout
    suspended: false,
    enabled: false,
};

// Groups are keyed by the workspace itself, never its number: GNOME removes
// empty workspaces and inserts new ones at the start, which renumbers the
// rest, and a window must stay in its group when that happens.
const workspaceIds = new WeakMap();
const workspaces = new Map();  // id -> Meta.Workspace
let nextWorkspaceId = 1;

function workspaceId(workspace) {
    let id = workspaceIds.get(workspace);
    if (!id) {
        id = nextWorkspaceId++;
        workspaceIds.set(workspace, id);
        workspaces.set(id, workspace);
    }
    return id;
}

/**
 * The group a window tiles in: its workspace and display. With workspaces
 * on the primary display only, windows on the others are on every
 * workspace, and share one group per display. Windows the user put on every
 * workspace do not tile.
 */
function groupKey(window) {
    if (window.is_on_all_workspaces()) {
        return Meta.prefs_get_workspaces_only_on_primary() && !window.is_on_primary_monitor()
            ? `all:${window.get_monitor()}` : null;
    }
    const workspace = window.get_workspace();
    return workspace ? `${workspaceId(workspace)}:${window.get_monitor()}` : null;
}

/** The workspace a group belongs to, if GNOME still has it. */
function groupWorkspace(key) {
    const workspace = workspaces.get(Number(key.split(':')[0]));
    const manager = global.workspace_manager;
    for (let i = 0; i < manager.n_workspaces; i++) {
        if (manager.get_workspace_by_index(i) === workspace)
            return workspace;
    }
    return null;
}

/**
 * Automatic tiling. Windows on each workspace and display are arranged by the
 * chosen layout, with gaps; dialogs, fixed-size windows and windows a rule
 * floats are left free. New windows join once they have drawn their first
 * frame (Mutter's own placement would undo an earlier move). Minimized,
 * fullscreen and floated windows sit out but keep their place; a maximized
 * window covers its tile without moving the others. Dropping a window onto
 * another swaps them, and windows glide to their new places. A lone window
 * fills the work area until the user drags or resizes it; then it stays put.
 * Focus View, in the window menu, floats a window above the others.
 *
 * Arranging runs once per frame, just before it is drawn, and never while a
 * window is being dragged or resized.
 */
export class Tiling {
    constructor() {
        this._values = {};
        this._rules = [];
        this._ruleCache = new WeakMap();
        this._own = new Map();       // window -> the rectangle tiling last asked for
        this._glides = new Map();    // window -> where it was on screen before a move
        this._gliding = new Set();
        this._hidden = new Set();    // windows scrolled out of view
        this._anchors = new Map();   // group key -> window a new one opened from
        this._dirty = new Set();
        this._laterId = 0;
        this._grabbing = false;
        this._grabbed = null;
        this._watched = new Set();
        this._waiting = new Set();
        this._indicator = null;
        global.display.connectObject(
            'window-created', (_display, window) => this._created(window),
            'grab-op-begin', (_display, window) => {
                this._grabbing = true;
                this._grabbed = window;
            },
            'grab-op-end', (_display, window, op) => {
                this._grabbing = false;
                this._grabbed = null;
                this._grabEnded(window, op);
                this._schedule();
            },
            'workareas-changed', () => this._queueAll(),
            'window-entered-monitor', (_display, _index, window) => this._moved(window),
            'notify::focus-window', () => this._focusChanged(), this);
        global.workspace_manager.connectObject(
            'workspace-removed', () => this._forgetWorkspaces(),
            'active-workspace-changed', () => this._indicator?.sync(), this);
        // Focus View in the window menu (right-click a title bar, or
        // Super+right-click a window). GNOME builds a new menu each time.
        const tiling = this;
        this._injections = new InjectionManager();
        this._injections.overrideMethod(WindowMenu.prototype, '_buildMenu', original => function (window) {
            original.call(this, window);
            if (!tiling._on || !isAppWindow(window) || window.get_window_type() !== Meta.WindowType.NORMAL)
                return;
            const item = this.addAction('Focus View', () => tiling.focusView(window));
            if (window.is_above())
                item.setOrnament(PopupMenu.Ornament.CHECK);
            this.moveMenuItem(item, 0);
        });
    }

    update({tiling = {}, rules = {}}) {
        const enabled = tiling.enabled === true;
        // A shortcut can pause tiling; switching it on in settings resumes it.
        if (enabled !== memory.enabled)
            memory.suspended = false;
        memory.enabled = enabled;
        this._values = tiling;
        this._rules = Array.isArray(rules.list) ? rules.list : [];
        this._ruleCache = new WeakMap();
        this._sync();
    }

    get _on() {
        return this._values.enabled === true && !memory.suspended;
    }

    /** Watch and adopt every window while tiling is on; nothing while off. */
    _sync() {
        if (this._on) {
            for (const actor of global.get_window_actors()) {
                this._watch(actor.meta_window);
                this._adopt(actor.meta_window);
            }
            this._queueAll();
        } else {
            this._unwatchAll();
        }
        const wanted = this._on && this._values.indicator !== false;
        if (wanted && !this._indicator) {
            this._indicator = new LayoutIndicator(this);
            Main.panel.addToStatusArea('desktop-forge-layout', this._indicator, 1, 'left');
        } else if (!wanted && this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }
        this._indicator?.sync();
    }

    // -- which windows tile -------------------------------------------------

    _rule(window) {
        let rule = this._ruleCache.get(window);
        if (!rule) {
            const described = describeWindow(window);
            rule = actionsFor(this._rules, described);
            // The app is sometimes not known yet for a brand new window.
            if (described.desktop_id)
                this._ruleCache.set(window, rule);
        }
        return rule;
    }

    tiles(window) {
        if (!this._on || !window || !isAppWindow(window) || memory.floating.has(window))
            return false;
        if (window.minimized || window.is_fullscreen() || window.is_above() || groupKey(window) === null)
            return false;
        const rule = this._rule(window);
        if (rule.mode === 'float')
            return false;
        // Dialogs float even when a rule tiles their app.
        if (window.get_window_type() !== Meta.WindowType.NORMAL)
            return false;
        if (this._values.float_dialogs !== false &&
            (window.get_transient_for() || window.is_attached_dialog()))
            return false;
        if (rule.mode === 'tile')
            return true;
        // Mutter reports maximized windows as not resizable.
        return this._values.float_fixed === false || window.allows_resize() || maximizeFlags(window) !== 0;
    }

    /** A window with no other tiled window in its group: snapping may take it. */
    alone(window) {
        const key = groupKey(window);
        return key !== null && this._members(key).length <= 1;
    }

    // -- tracking -------------------------------------------------------------

    _created(window) {
        if (!this._on || !isAppWindow(window))
            return;
        // The window it opened from: where "after focus" and dwindle put it.
        const anchor = global.display.focus_window;
        const cancel = afterFirstFrame(window, () => {
            this._waiting.delete(cancel);
            if (!this._on || !window.get_compositor_private())
                return;
            this._watch(window);
            this._adopt(window, anchor);
        });
        this._waiting.add(cancel);
    }

    _watch(window) {
        if (!window || this._watched.has(window) || !isAppWindow(window))
            return;
        this._watched.add(window);
        window.connectObject(
            'unmanaged', () => this._forget(window),
            'workspace-changed', () => this._moved(window),
            'notify::on-all-workspaces', () => this._moved(window),
            'notify::minimized', () => this._changed(window),
            'notify::fullscreen', () => this._changed(window),
            'notify::above', () => this._changed(window),
            'notify::maximized-horizontally', () => this._changed(window),
            'notify::maximized-vertically', () => this._changed(window),
            'notify::title', () => {
                this._ruleCache.delete(window);
                if (this._rules.length)
                    this._changed(window);
            },
            'size-changed', () => this._resized(window),
            'position-changed', () => this._resized(window), this);
    }

    _unwatchAll() {
        for (const window of this._watched)
            window.disconnectObject(this);
        this._watched.clear();
        this._own.clear();
        this._glides.clear();
        for (const window of [...this._gliding])
            this._stopGlide(window);
        for (const window of [...this._hidden])
            this._setHidden(window, false);
    }

    _forget(window) {
        window.disconnectObject(this);
        this._watched.delete(window);
        memory.floating.delete(window);
        memory.loose.delete(window);
        this._own.delete(window);
        this._glides.delete(window);
        this._gliding.delete(window);
        this._hidden.delete(window);
        for (const [key, order] of memory.order) {
            const index = order.indexOf(window);
            if (index >= 0) {
                order.splice(index, 1);
                this._queue(key);
            }
        }
    }

    /**
     * Keep a window in its own group only, and give it a place there if it
     * tiles. `anchor` is set for new windows: the window they opened from.
     */
    _adopt(window, anchor) {
        const key = groupKey(window);
        for (const [other, order] of memory.order) {
            if (other !== key && order.includes(window)) {
                order.splice(order.indexOf(window), 1);
                this._setHidden(window, false);
                this._queue(other);
            }
        }
        if (!key || !this.tiles(window))
            return;
        const order = memory.order.get(key) ?? [];
        if (!order.includes(window)) {
            const isNew = anchor !== undefined;
            // A new window Mutter maximized because it was nearly screen
            // sized: it was not the user's choice.
            if (isNew && maximizeFlags(window))
                window.unmaximize();
            const at = isNew ? insertionIndex(order.length, order.indexOf(anchor), this._values.new_window)
                : order.length;
            order.splice(at, 0, window);
            memory.order.set(key, order);
            if (isNew && anchor)
                this._anchors.set(key, anchor);
        }
        this._queue(key);
    }

    _moved(window) {
        if (this._watched.has(window))
            this._adopt(window);
    }

    /**
     * Minimized, fullscreen, always-on-top and floated windows sit out of
     * the layout but keep their place in it; the rest close up.
     */
    _changed(window) {
        if (!this._watched.has(window))
            return;
        this._adopt(window);
        this._queue(groupKey(window));
    }

    _resized(window) {
        if (this._grabbing || !this._watched.has(window))
            return;
        const glide = this._glides.get(window);
        if (glide)
            this._glide(window, glide);
        // Our own move landing. A size the app insists on (terminals round
        // to whole characters, some apps have a minimum wider than their
        // tile) is accepted: only the position is ours.
        const own = this._own.get(window);
        const frame = window.get_frame_rect();
        const key = groupKey(window);
        const area = own && key ? this._area(key) : null;
        if (area) {
            const at = keptInside(own, frame, area);
            if (frame.x === at.x && frame.y === at.y)
                return;
        }
        if (key && memory.order.get(key)?.includes(window))
            this._queue(key);
    }

    _grabEnded(window, op) {
        if (!window || !this._watched.has(window) || !this.tiles(window))
            return;
        const key = groupKey(window);
        const members = this._members(key);
        const from = members.indexOf(window);
        if (from < 0)
            return;
        // A lone window moved or resized by hand stays where it was left.
        if (members.length === 1) {
            memory.loose.add(window);
            return;
        }
        const layout = this._layout(key);
        // Resized by hand: it is asked for its tile's size again.
        if (!MOVE_OPS.has(op))
            this._own.delete(window);
        if (MOVE_OPS.has(op)) {
            // Dropped on another tiled window: trade places with it. A
            // keyboard move goes by where the window is, not the pointer.
            const frame = window.get_frame_rect();
            const [px, py] = op === Meta.GrabOp.KEYBOARD_MOVING
                ? [frame.x + frame.width / 2, frame.y + frame.height / 2] : global.get_pointer();
            const to = rectAt(this._rects(key, members), px, py, from);
            if (to >= 0)
                this._swap(key, window, members[to]);
        } else if (layout === 'dwindle') {
            const tree = memory.trees.get(key);
            if (tree)
                dwindleRatio(tree, window, window.get_frame_rect(), this._options(key).inner);
        } else {
            const area = this._area(key);
            const ratio = area && resizedRatio(layout, members.length, from, window.get_frame_rect(), area,
                this._options(key));
            if (ratio)
                memory.ratios.set(key, ratio);
        }
        this._queue(key);
    }

    _focusChanged() {
        const window = global.display.focus_window;
        const key = window && this._watched.has(window) ? groupKey(window) : null;
        // Scrolling brings the focused column into view.
        if (key && this._layout(key) === 'scrolling')
            this._queue(key);
        this._indicator?.sync();
    }

    // -- layout -------------------------------------------------------------------

    _layout(key) {
        const chosen = memory.layouts.get(key) ?? this._values.layout;
        return LAYOUTS.includes(chosen) ? chosen : 'master';
    }

    _area(key) {
        const [workspace, monitor] = key.split(':');
        if (workspace === 'all')
            return Main.layoutManager.getWorkAreaForMonitor(Number(monitor));
        return groupWorkspace(key)?.get_work_area_for_monitor(Number(monitor)) ?? null;
    }

    _options(key) {
        return {
            inner: this._values.gaps_inner ?? 8,
            outer: this._values.gaps_outer ?? 8,
            smart: this._values.smart_gaps !== false,
            ratio: memory.ratios.get(key) ?? this._values.master_ratio ?? 0.55,
        };
    }

    /** The windows of a group that tile right now, in order. */
    _members(key) {
        return (memory.order.get(key) ?? []).filter(window =>
            this._watched.has(window) && this.tiles(window) && groupKey(window) === key);
    }

    _rects(key, members) {
        const area = members.length ? this._area(key) : null;
        if (!area)
            return [];
        const layout = this._layout(key);
        const options = this._options(key);
        if (layout === 'dwindle') {
            const tree = dwindleSync(memory.trees.get(key) ?? null, memory.order.get(key) ?? members,
                [this._anchors.get(key), global.display.focus_window]);
            this._anchors.delete(key);
            memory.trees.set(key, tree);
            const placed = dwindleRects(tree, area, options, new Set(members));
            return members.map(window => placed.get(window));
        }
        if (layout === 'scrolling') {
            options.columns = Math.max(1, Math.min(4, this._values.scroll_columns ?? 2));
            options.first = scrollFirst(memory.scroll.get(key) ?? 0,
                members.indexOf(global.display.focus_window), members.length, options.columns);
            memory.scroll.set(key, options.first);
        }
        return layoutRects(layout, members.length, area, options);
    }

    _queue(key) {
        if (!key)
            return;
        this._dirty.add(key);
        if (!this._grabbing)
            this._schedule();
    }

    /** Arrange the waiting groups just before the next frame is drawn. */
    _schedule() {
        if (this._laterId || !this._dirty.size)
            return;
        this._laterId = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._laterId = 0;
            if (this._grabbing)
                return GLib.SOURCE_REMOVE;
            const keys = [...this._dirty];
            this._dirty.clear();
            for (const key of keys) {
                try {
                    this._arrange(key);
                } catch (error) {
                    logError(error, 'desktop-forge: could not tile a workspace');
                }
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    /** Drop groups whose workspace GNOME has removed; the rest re-arrange. */
    _forgetWorkspaces() {
        for (const key of [...memory.order.keys()]) {
            if (!key.startsWith('all:') && !groupWorkspace(key)) {
                for (const map of [memory.order, memory.layouts, memory.ratios, memory.trees, memory.scroll])
                    map.delete(key);
            }
        }
        for (const [id, workspace] of [...workspaces]) {
            if (!groupWorkspace(`${id}:0`)) {
                workspaces.delete(id);
                workspaceIds.delete(workspace);
            }
        }
        this._queueAll();
    }

    _queueAll() {
        for (const key of memory.order.keys())
            this._queue(key);
    }

    _arrange(key) {
        if (!this._on || !memory.order.has(key))
            return;
        const all = memory.order.get(key).filter(window =>
            this._watched.has(window) && groupKey(window) === key);
        memory.order.set(key, all);
        const members = this._members(key);
        if (members.length > 1)
            members.forEach(window => memory.loose.delete(window));
        // A window sitting out is asked for its whole tile again on return.
        for (const window of all) {
            if (!members.includes(window))
                this._own.delete(window);
        }
        const rects = this._rects(key, members);
        const area = members.length ? this._area(key) : null;
        const glide = this._values.animate !== false;
        members.forEach((window, index) => {
            const target = rects[index];
            const flags = maximizeFlags(window);
            // A maximized window covers its tile; the others stay put.
            if (!target || flags === Meta.MaximizeFlags.BOTH || memory.loose.has(window)) {
                this._own.delete(window);
                return;
            }
            const frame = window.get_frame_rect();
            const own = this._own.get(window);
            if (sameRect(frame, target))
                return;
            // Asked for this tile already and the app kept a size of its
            // own: move it only. A window bigger than its tile would go
            // off screen, where Mutter does not put it, so it stays inside.
            // ponytail: it overlaps its neighbour; reserving room for minimum sizes needs them from Mutter.
            const moveOnly = own && sameRect(own, target) && !flags && area;
            const to = moveOnly ? keptInside(target, frame, area) : target;
            if (moveOnly && frame.x === to.x && frame.y === to.y)
                return;
            const actor = window.get_compositor_private();
            if (glide && own && actor && window !== this._grabbed &&
                (frame.x !== to.x || frame.y !== to.y)) {
                this._glides.set(window, {
                    x: frame.x + actor.translation_x, y: frame.y + actor.translation_y,
                    to: {x: to.x, y: to.y},
                });
            }
            if (moveOnly) {
                window.move_frame(true, to.x, to.y);
                return;
            }
            this._own.set(window, target);
            // Half-screen tiling from Mutter would hold the window in place.
            if (flags)
                window.unmaximize();
            window.move_resize_frame(true, target.x, target.y, target.width, target.height);
        });
        // Scrolling: columns out of view wait, invisible, under the edges.
        const hidden = new Set();
        const first = memory.scroll.get(key) ?? 0;
        const shown = Math.max(1, Math.min(4, this._values.scroll_columns ?? 2));
        if (this._layout(key) === 'scrolling') {
            members.forEach((window, index) => {
                if (index < first || index >= first + shown)
                    hidden.add(window);
            });
        }
        for (const window of all)
            this._setHidden(window, hidden.has(window));
    }

    _setHidden(window, hidden) {
        if (hidden === this._hidden.has(window))
            return;
        const actor = window.get_compositor_private();
        if (hidden) {
            this._hidden.add(window);
            window.lower();
        } else {
            this._hidden.delete(window);
        }
        if (actor)
            actor.opacity = hidden ? 0 : 255;
    }

    /** Ease a window from where it was drawn to where it landed. */
    _glide(window, from) {
        this._glides.delete(window);
        const frame = window.get_frame_rect();
        const actor = window.get_compositor_private();
        if (!actor || frame.x !== from.to.x || frame.y !== from.to.y)
            return;
        actor.remove_transition('translation-x');
        actor.remove_transition('translation-y');
        actor.translation_x = from.x - frame.x;
        actor.translation_y = from.y - frame.y;
        this._gliding.add(window);
        actor.ease({
            translation_x: 0, translation_y: 0, duration: GLIDE_MS,
            mode: Clutter.AnimationMode.EASE_OUT_CUBIC,
            onStopped: () => this._gliding.delete(window),
        });
    }

    _stopGlide(window) {
        this._gliding.delete(window);
        const actor = window.get_compositor_private();
        if (!actor)
            return;
        actor.remove_transition('translation-x');
        actor.remove_transition('translation-y');
        actor.translation_x = 0;
        actor.translation_y = 0;
    }

    _swap(key, a, b) {
        const order = memory.order.get(key);
        const i = order?.indexOf(a) ?? -1;
        const j = order?.indexOf(b) ?? -1;
        if (i < 0 || j < 0)
            return;
        [order[i], order[j]] = [order[j], order[i]];
        const tree = memory.trees.get(key);
        if (tree)
            dwindleSwap(tree, a, b);
        this._queue(key);
    }

    // -- shortcuts, gestures and the top bar ------------------------------------

    toggle() {
        memory.suspended = !memory.suspended;
        this._sync();
        return this._on;
    }

    /** The focused window's group, or the active workspace's on the primary display. */
    currentKey() {
        const window = global.display.focus_window;
        const active = global.workspace_manager.get_active_workspace();
        if (window && this._watched.has(window) && window.located_on_workspace(active)) {
            const key = groupKey(window);
            if (key)
                return key;
        }
        return `${workspaceId(active)}:${Main.layoutManager.primaryIndex}`;
    }

    layoutOf(key) {
        return this._layout(key);
    }

    setLayout(key, layout) {
        if (!key || !LAYOUTS.includes(layout))
            return;
        memory.layouts.set(key, layout);
        this._queue(key);
        this._indicator?.sync();
    }

    nextLayout() {
        const window = global.display.focus_window;
        const key = window ? groupKey(window) : null;
        if (!key)
            return null;
        const next = LAYOUTS[(LAYOUTS.indexOf(this._layout(key)) + 1) % LAYOUTS.length];
        this.setLayout(key, next);
        return next;
    }

    toggleFloating() {
        const window = global.display.focus_window;
        if (!window)
            return;
        if (memory.floating.has(window))
            memory.floating.delete(window);
        else
            memory.floating.add(window);
        this._changed(window);
    }

    /**
     * Float a window above the others, centered and enlarged, or put it back
     * in its tile. Always-on-top windows sit out of the layout, so the rest
     * close up underneath.
     */
    focusView(window) {
        if (window.is_above()) {
            window.unmake_above();
            return;
        }
        unmaximizeAny(window);
        window.make_above();
        const area = window.get_work_area_current_monitor();
        const width = Math.round(area.width * FOCUS_WIDTH);
        const height = Math.round(area.height * FOCUS_HEIGHT);
        window.move_resize_frame(true, area.x + Math.round((area.width - width) / 2),
            area.y + Math.round((area.height - height) / 2), width, height);
        window.activate(global.get_current_time());
    }

    focus(direction) {
        this._step(direction, (_key, _window, other) => other.activate(global.get_current_time()));
    }

    swap(direction) {
        this._step(direction, (key, window, other) => this._swap(key, window, other));
    }

    /** Grow (step > 0) or shrink the main area, or in dwindle the focused window. */
    resizeMain(step) {
        const window = global.display.focus_window;
        const key = window ? groupKey(window) : null;
        if (!key)
            return;
        if (this._layout(key) === 'dwindle') {
            const tree = memory.trees.get(key);
            if (tree)
                dwindleNudge(tree, window, step);
        } else {
            const ratio = memory.ratios.get(key) ?? this._values.master_ratio ?? 0.55;
            memory.ratios.set(key, Math.max(0.2, Math.min(0.8, ratio + step)));
        }
        this._queue(key);
    }

    _step(direction, act) {
        const window = global.display.focus_window;
        const key = window && this._watched.has(window) ? groupKey(window) : null;
        if (!key)
            return;
        const members = this._members(key);
        const from = members.indexOf(window);
        const to = stepIndex(this._layout(key), this._rects(key, members), from, direction);
        if (from >= 0 && to >= 0)
            act(key, window, members[to]);
    }

    /** Group sizes, for the smoke test. */
    get groups() {
        return Object.fromEntries([...memory.order].map(([key, order]) => [key, order.length]));
    }

    destroy() {
        for (const cancel of this._waiting)
            cancel();
        this._waiting.clear();
        if (this._laterId) {
            global.compositor.get_laters().remove(this._laterId);
            this._laterId = 0;
        }
        global.display.disconnectObject(this);
        global.workspace_manager.disconnectObject(this);
        this._injections.clear();
        this._unwatchAll();
        this._indicator?.destroy();
        this._indicator = null;
        // memory is kept on purpose; see its comment.
    }
}

/**
 * The current workspace's layout in the top bar, drawn as a tiny sketch of
 * it; its menu switches layout for that workspace.
 */
const LayoutIndicator = GObject.registerClass(
class LayoutIndicator extends PanelMenu.Button {
    _init(tiling) {
        super._init(0.5, 'Tiling layout');
        this._tiling = tiling;
        this._key = null;
        this._layout = 'master';
        this._icon = new St.DrawingArea({
            style_class: 'system-status-icon', width: 16, height: 16, y_align: Clutter.ActorAlign.CENTER,
        });
        this._icon.connect('repaint', area => this._draw(area));
        this.add_child(this._icon);
        this._items = new Map();
        for (const layout of LAYOUTS) {
            const item = new PopupMenu.PopupMenuItem(LAYOUT_NAMES[layout]);
            item.connect('activate', () => this._tiling.setLayout(this._key, layout));
            this.menu.addMenuItem(item);
            this._items.set(layout, item);
        }
    }

    get layout() {
        return this._layout;
    }

    sync() {
        this._key = this._tiling.currentKey();
        this._layout = this._tiling.layoutOf(this._key);
        for (const [layout, item] of this._items)
            item.setOrnament(layout === this._layout ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
        this._icon.queue_repaint();
    }

    _draw(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        const colour = area.get_theme_node().get_foreground_color();
        cr.setSourceRGBA(colour.red / 255, colour.green / 255, colour.blue / 255, colour.alpha / 255);
        const count = this._layout === 'monocle' ? 1 : 3;
        for (const r of layoutRects(this._layout, count, {x: 1, y: 2, width: width - 2, height: height - 4},
            {inner: 2, outer: 0, smart: false, columns: 2}))
            cr.rectangle(r.x, r.y, r.width, r.height);
        cr.fill();
        cr.$dispose();
    }
});
