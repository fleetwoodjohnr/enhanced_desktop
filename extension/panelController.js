import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Layout from 'resource:///org/gnome/shell/ui/layout.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PointerWatcher from 'resource:///org/gnome/shell/ui/pointerWatcher.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

import {
    SettleTimer, barOverlap, barShouldShow, chromeStylesheet, edgeActivation,
    panelTranslation, pointerAtEdge, pointerInPanelCorridor, resolveChrome,
    revealAllowed,
} from './chromeLogic.js';
import {DesktopIconsIntegration} from './desktopIconsIntegration.js';

// A burst of window changes (a resize, an app laying itself out) is decided
// once it has been quiet this long, and never later than OVERLAP_MAX_WAIT_MS
// after it began.
const OVERLAP_SETTLE_MS = 150;
const OVERLAP_MAX_WAIT_MS = 600;
const EDGE_POLL_MS = 50;
const CORRIDOR_CHECK_MS = 200;
const PRESSURE_TIMEOUT_MS = 1000;

export const PANEL_STATES = Object.freeze({
    HIDDEN: 0,
    SHOWING: 1,
    SHOWN: 2,
    HIDING: 3,
});

// Only windows a person works in can cover the bar. Docks, menus, tooltips
// and notifications are transient chrome of their own, and counting them made
// the bar flap whenever one opened near it.
const COUNTED_TYPES = new Set([
    Meta.WindowType.NORMAL,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
    Meta.WindowType.UTILITY,
    Meta.WindowType.TOOLBAR,
]);
const IGNORED_APPLICATIONS = new Set(['com.rastersoft.ding', 'com.desktop.ding']);

function toMs(seconds) {
    return Math.max(0, Math.round(seconds * 1000));
}

/**
 * Owns top-bar geometry, visibility and the generated chrome stylesheet.
 *
 * Two tracking modes. "Always visible" is Shell's own arrangement: the bar
 * reserves a strut and Shell hides it over fullscreen windows. Every hiding
 * mode is an overlay: no strut, so revealing it never resizes a maximized
 * window, and this controller owns its visibility outright -- including over
 * fullscreen windows, where Shell's own rule would contradict the reveal
 * setting.
 *
 * A settled hidden bar is translated fully off its monitor *and* hidden, so
 * it can neither draw onto an adjacent display nor take clicks from the top
 * row of a maximized application. The edge is watched by pointer position or
 * a pressure barrier instead, neither of which puts an actor over that row.
 */
export class PanelController {
    constructor(extensionUuid) {
        this._panelBox = Main.layoutManager.panelBox;
        this._panel = Main.panel;
        this._desktopIcons = new DesktopIconsIntegration(extensionUuid);
        this._injections = new InjectionManager();
        this._chrome = null;
        this._css = null;
        this._geometryKey = null;
        this._behaviorKey = null;
        // The floating ("island") bar, from desktop.json. It lives here, not in
        // a session module, because it changes the strut: tearing it down at
        // the lock screen would resize every window behind the shield.
        this._extras = {floating: false, margin: 0, radius: 0};
        this._extrasCss = '';
        this._tracking = null;
        this._state = PANEL_STATES.SHOWN;
        this._shown = true;
        this._held = false;
        this._overlap = false;
        this._grabbing = false;
        this._locked = false;
        this._destroyed = false;
        this._stylesheetLoaded = false;
        this._watchedWindows = new Set();
        this._watchedMenus = new Set();
        this._settle = new SettleTimer(OVERLAP_SETTLE_MS, OVERLAP_MAX_WAIT_MS);
        this._overlapId = 0;
        this._revealId = 0;
        this._hideId = 0;
        this._corridorId = 0;
        this._monitorsId = 0;
        this._edgeWatch = null;
        this._pressure = null;
        this._pressureBarrier = null;
        this._pressureTriggerId = 0;
        // Counted for the smoke test, which asserts a window dragged across the
        // strip produces no show/hide churn.
        this.transitions = 0;
        this._originalTrackHover = this._panelBox.track_hover;

        const cacheDir = GLib.build_filenamev([GLib.get_user_cache_dir(), 'desktop-forge']);
        GLib.mkdir_with_parents(cacheDir, 0o700);
        this._stylesheetFile = Gio.File.new_for_path(
            GLib.build_filenamev([cacheDir, 'shell-chrome.css']));
        this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();

        this._panelBox.track_hover = true;
        this._panelBox.connectObject(
            'notify::hover', () => this._panelHoverChanged(),
            // Connected after Shell's own handler, which recreates the
            // right-edge barrier on every allocation.
            'notify::allocation', () => this._syncPanelBarrier(), this);
        Main.overview.connectObject(
            'showing', () => this._evaluate(),
            'hiding', () => this._evaluate(),
            'hidden', () => this._evaluate(), this);
        Main.layoutManager.connectObject(
            'monitors-changed', () => this._monitorsChanged(), this);
        global.display.connectObject(
            'notify::focus-window', () => this._scheduleOverlap(),
            'restacked', () => this._scheduleOverlap(),
            'workareas-changed', () => this._scheduleOverlap(),
            'in-fullscreen-changed', () => this._evaluate(false),
            'grab-op-begin', () => this._grabChanged(true),
            'grab-op-end', () => this._grabChanged(false),
            'window-created', (_display, window) => {
                this._watchWindow(window);
                this._scheduleOverlap();
            }, this);
        global.workspace_manager.connectObject(
            'active-workspace-changed', () => this._scheduleOverlap(0), this);
        global.stage.connectObject(
            'notify::key-focus', () => this._keyFocusChanged(), this);
        for (const actor of global.get_window_actors())
            this._watchWindow(actor.meta_window);
        this._watchMenus();
    }

    // -- configuration ------------------------------------------------------

    /**
     * Apply chrome settings, touching only what actually changed.
     *
     * This runs on every widget rebuild and colour-scheme change, so an
     * unchanged configuration must be a no-op: resetting the reveal hold and
     * reloading the theme each time is what made the bar appear and vanish
     * "randomly" whenever an unrelated setting was saved.
     */
    apply(configured, dark) {
        const chrome = resolveChrome(configured, dark);
        const top = chrome.top_bar;
        this._chrome = chrome;
        this._configured = configured;
        this._dark = dark;

        const css = chromeStylesheet(chrome) + this._extrasCss;
        if (css !== this._css) {
            this._css = css;
            this._writeStylesheet(css);
        }

        const geometryKey = `${top.position}:${top.height}:${this._margin}`;
        const behaviorKey = JSON.stringify([
            top.visibility, top.reveal_method, top.sensitivity, top.hide_when,
            top.reveal_in_fullscreen,
        ]);
        const geometryChanged = geometryKey !== this._geometryKey;
        const behaviorChanged = behaviorKey !== this._behaviorKey;
        this._geometryKey = geometryKey;
        this._behaviorKey = behaviorKey;
        if (!geometryChanged && !behaviorChanged)
            return;

        if (geometryChanged) {
            // A hold belongs to the old geometry: a bar moved to the other
            // edge must not stay exposed because the pointer crossed its
            // former position.
            this._held = false;
            this._applyGeometry();
        }
        this._syncTracking();
        this._removeRevealTrigger();
        this._overlap = this._computeOverlap();
        this._evaluate(false, true);
    }

    /** Floating bar margin and corner radius; a no-op when unchanged. */
    setExtras({floating = false, margin = 0, radius = 0} = {}) {
        const extras = {
            floating: floating === true,
            margin: Number.isFinite(margin) ? Math.max(0, Math.min(24, Math.round(margin))) : 0,
            radius: Number.isFinite(radius) ? Math.max(0, Math.min(24, Math.round(radius))) : 0,
        };
        if (JSON.stringify(extras) === JSON.stringify(this._extras))
            return;
        this._extras = extras;
        this._extrasCss = extras.floating
            ? `\n#panel { border-radius: ${extras.radius}px; }\n` : '';
        if (this._chrome)
            this.apply(this._configured ?? {}, this._dark ?? false);
    }

    get extras() {
        return {...this._extras};
    }

    /** Space between the bar and the screen edge, while floating. */
    get _margin() {
        return this._extras.floating ? this._extras.margin : 0;
    }

    /** The box's height: the bar plus its floating margin. */
    get _boxHeight() {
        return this._top.height + this._margin;
    }

    setLocked(locked) {
        if (this._destroyed || locked === this._locked)
            return;
        this._locked = locked;
        if (locked) {
            // The lock screen shows this same panel with its own battery and
            // network indicators. Leave struts exactly as they are -- changing
            // them now would resize every window behind the lock.
            this._cancelOverlap();
            this._cancelHide();
            this._stopCorridorWatch();
            this._removeRevealTrigger();
            this._held = false;
            this._panelBox.remove_all_transitions();
            this._panelBox.translation_y = 0;
            this._panelBox.show();
            this._state = PANEL_STATES.SHOWN;
            this._shown = true;
            this._syncPanelBarrier();
            return;
        }
        this._overlap = this._computeOverlap();
        this._evaluate(false, true);
    }

    get _top() {
        return this._chrome?.top_bar ?? resolveChrome({}, false).top_bar;
    }

    _writeStylesheet(css) {
        try {
            if (this._stylesheetLoaded) {
                this._theme.unload_stylesheet(this._stylesheetFile);
                this._stylesheetLoaded = false;
            }
            GLib.file_set_contents(this._stylesheetFile.get_path(), css);
            this._theme.load_stylesheet(this._stylesheetFile);
            this._stylesheetLoaded = true;
        } catch (error) {
            console.error(`desktop-forge: could not apply shell chrome style: ${error}`);
        }
    }

    _monitor() {
        return Main.layoutManager.primaryMonitor;
    }

    _applyGeometry() {
        const monitor = this._monitor();
        if (!monitor)
            return;
        const {position, height} = this._top;
        const margin = this._margin;
        const box = height + margin;
        const y = position === 'bottom' ? monitor.y + monitor.height - box : monitor.y;
        this._panelBox.set_position(monitor.x, y);
        this._panelBox.set_size(monitor.width, box);
        this._panel.set_size(monitor.width - 2 * margin, height);
        this._panel.margin_left = margin;
        this._panel.margin_right = margin;
        this._panel.margin_top = position === 'bottom' ? 0 : margin;
        this._panel.margin_bottom = position === 'bottom' ? margin : 0;
        this._desktopIcons.setPanelMargin(position, box);
    }

    _monitorsChanged() {
        // Shell's own handler has just reset the box to the stock top-edge
        // geometry. Put ours back once layout settles, without animating the
        // correction, so a hotplug does not flash the bar.
        if (this._monitorsId)
            return;
        this._monitorsId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._monitorsId = 0;
            if (this._destroyed)
                return GLib.SOURCE_REMOVE;
            this._applyGeometry();
            this._removeRevealTrigger();
            this._overlap = this._computeOverlap();
            this._evaluate(false, true);
            return GLib.SOURCE_REMOVE;
        });
    }

    /** Stock chrome in always-visible mode; an owned overlay otherwise. */
    _syncTracking() {
        const tracking = this._top.visibility === 'always' ? 'stock' : 'overlay';
        if (tracking === this._tracking)
            return;
        this._tracking = tracking;
        try {
            Main.layoutManager.untrackChrome(this._panelBox);
            Main.layoutManager.trackChrome(this._panelBox, tracking === 'stock'
                ? {affectsStruts: true, trackFullscreen: true}
                : {affectsStruts: false, trackFullscreen: false});
        } catch (error) {
            console.error(`desktop-forge: could not update top-bar work area: ${error}`);
        }
        if (tracking === 'overlay')
            this._panelBox.visible = this._state !== PANEL_STATES.HIDDEN;
    }

    // -- the decision -------------------------------------------------------

    _menuOpen() {
        return !!this._panel.menuManager?.activeMenu;
    }

    _panelHasKeyFocus() {
        const focus = global.stage.get_key_focus();
        return !!focus && focus !== global.stage && this._panel.contains(focus);
    }

    /**
     * Keep a hiding bar shown (the "Show or hide the top bar" shortcut) or
     * let it hide again. Not saved: it lasts until toggled or logout.
     */
    toggleForced() {
        this._forced = !this._forced;
        this._evaluate();
        return this._forced;
    }

    get forced() {
        return !!this._forced;
    }

    _situation() {
        const top = this._top;
        return {
            mode: this._forced ? 'always' : top.visibility,
            locked: this._locked,
            overview: Main.overview.visible || Main.overview.animationInProgress,
            fullscreen: !!this._monitor()?.inFullscreen,
            revealInFullscreen: top.reveal_in_fullscreen,
            held: this._held,
            menuOpen: this._menuOpen(),
            keyFocus: this._panelHasKeyFocus(),
            overlap: this._overlap,
        };
    }

    _evaluate(animate = true, force = false) {
        if (this._destroyed || this._locked || !this._chrome)
            return;
        const show = barShouldShow(this._situation());
        const settled = show
            ? this._state === PANEL_STATES.SHOWN || this._state === PANEL_STATES.SHOWING
            : this._state === PANEL_STATES.HIDDEN || this._state === PANEL_STATES.HIDING;
        if (force || show !== this._shown || !settled) {
            if (show !== this._shown)
                this.transitions++;
            this._shown = show;
            this._animate(show, animate);
        }
        this._syncWatchers();
    }

    _animate(show, animate) {
        const top = this._top;
        const target = panelTranslation(top.position, this._boxHeight, !show);
        this._panelBox.remove_all_transitions();
        if (show && this._tracking === 'overlay')
            this._panelBox.show();
        const duration = animate && St.Settings.get().enable_animations
            ? toMs(top.animation_time) : 0;
        if (!duration || this._panelBox.translation_y === target) {
            this._panelBox.translation_y = target;
            this._arrive(show);
            return;
        }
        this._state = show ? PANEL_STATES.SHOWING : PANEL_STATES.HIDING;
        this._panelBox.ease({
            translation_y: target,
            duration,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            // onComplete only runs for a transition that finished; one
            // replaced by a newer decision never lands here.
            onComplete: () => {
                if (!this._destroyed)
                    this._arrive(show);
            },
        });
    }

    _arrive(show) {
        this._state = show ? PANEL_STATES.SHOWN : PANEL_STATES.HIDDEN;
        if (!show) {
            // A hold cannot outlive the bar it held; a stale one would
            // reveal the bar later for no reason, say on leaving fullscreen.
            this._held = false;
            if (this._tracking === 'overlay')
                this._panelBox.hide();
        }
        this._syncPanelBarrier();
        this._syncWatchers();
    }

    /**
     * Shell keeps a barrier at the right end of the bar's rows so the pointer
     * catches on the top-right menu. With the bar hidden or at the bottom it
     * only stops the pointer crossing to the next display for no reason.
     */
    _syncPanelBarrier() {
        const layout = Main.layoutManager;
        const wanted = this._locked || (this._state !== PANEL_STATES.HIDDEN &&
            this._top.position === 'top');
        try {
            if (!wanted)
                layout._destroyPanelBarrier?.();
            else if (!layout._rightPanelBarrier)
                layout._updatePanelBarrier?.();
        } catch (error) {
            console.warn(`desktop-forge: could not update the panel barrier: ${error}`);
        }
    }

    // -- overlap --------------------------------------------------------------

    _watchWindow(window) {
        if (!window || this._watchedWindows.has(window))
            return;
        this._watchedWindows.add(window);
        const changed = () => this._scheduleOverlap();
        window.connectObject(
            'size-changed', changed,
            'position-changed', changed,
            'workspace-changed', changed,
            'notify::minimized', changed,
            'notify::maximized-horizontally', changed,
            'notify::maximized-vertically', changed,
            'notify::fullscreen', changed,
            'notify::window-type', changed,
            'unmanaged', () => this._unwatchWindow(window), this);
    }

    _unwatchWindow(window) {
        window.disconnectObject(this);
        this._watchedWindows.delete(window);
        this._scheduleOverlap();
    }

    _counts(window, workspace) {
        if (!window || window.minimized || !COUNTED_TYPES.has(window.get_window_type()))
            return false;
        const appId = window.get_gtk_application_id?.() ?? '';
        if (IGNORED_APPLICATIONS.has(appId))
            return false;
        return window.located_on_workspace(workspace) && window.showing_on_its_workspace();
    }

    _shownBarRect() {
        const monitor = this._monitor();
        const {position} = this._top;
        const height = this._boxHeight;
        return {
            x: monitor?.x ?? 0,
            y: position === 'bottom' && monitor
                ? monitor.y + monitor.height - height : monitor?.y ?? 0,
            width: monitor?.width ?? 0,
            height,
        };
    }

    _computeOverlap() {
        if (this._top.visibility !== 'intelligent')
            return false;
        const workspace = global.workspace_manager.get_active_workspace();
        const tracker = Shell.WindowTracker.get_default();
        const focus = global.display.focus_window;
        const focusApp = focus ? tracker.get_window_app(focus) : null;
        const windows = global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(window => this._counts(window, workspace))
            .map(window => ({
                rect: window.get_frame_rect(),
                focusedApp: window === focus ||
                    (!!focusApp && tracker.get_window_app(window) === focusApp),
                // Mutter reports edge-tiled windows as vertically maximized.
                maximized: window.maximized_vertically || window.maximized_horizontally,
            }));
        return barOverlap(windows, this._shownBarRect(), this._top.hide_when);
    }

    _grabChanged(grabbing) {
        this._grabbing = grabbing;
        if (grabbing) {
            // A window being dragged or resized passes over the strip many
            // times a second. Keep the last decision until it is dropped.
            this._cancelOverlap();
            return;
        }
        this._scheduleOverlap(0);
    }

    _scheduleOverlap(delay = null) {
        if (this._destroyed || this._locked || this._grabbing)
            return;
        const wait = delay ?? this._settle.delayFor(GLib.get_monotonic_time() / 1000);
        if (this._overlapId)
            GLib.source_remove(this._overlapId);
        this._overlapId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, wait, () => {
            this._overlapId = 0;
            this._settle.settled();
            if (this._destroyed)
                return GLib.SOURCE_REMOVE;
            const overlap = this._computeOverlap();
            if (overlap !== this._overlap) {
                this._overlap = overlap;
                this._evaluate();
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _cancelOverlap() {
        if (this._overlapId) {
            GLib.source_remove(this._overlapId);
            this._overlapId = 0;
        }
        this._settle.settled();
    }

    // -- menus and keyboard -------------------------------------------------

    _watchMenus() {
        const manager = this._panel.menuManager;
        if (!manager)
            return;
        for (const menu of manager._menus ?? [])
            this._watchMenu(menu);
        const controller = this;
        // Status items added later (other extensions, CLIVE) bring menus too.
        this._injections.overrideMethod(manager, 'addMenu', original => function (menu, position) {
            const result = original.call(this, menu, position);
            controller._watchMenu(menu);
            return result;
        });
        // Super+V and Super+S open a menu from the keyboard. Shell refuses to
        // toggle an unmapped indicator, so a hidden bar must arrive first.
        this._injections.overrideMethod(this._panel, '_toggleMenu', original => function (indicator) {
            controller._revealForMenu();
            return original.call(this, indicator);
        });
    }

    _watchMenu(menu) {
        if (!menu || this._watchedMenus.has(menu))
            return;
        this._watchedMenus.add(menu);
        menu.connectObject(
            'open-state-changed', (_menu, open) => this._menuStateChanged(menu, open),
            'destroy', () => this._watchedMenus.delete(menu), this);
    }

    _revealForMenu() {
        if (this._locked || this._top.visibility === 'always' ||
            this._state === PANEL_STATES.SHOWN)
            return;
        this._held = true;
        this._shown = true;
        this.transitions++;
        this._animate(true, false);
    }

    _menuStateChanged(menu, open) {
        if (this._destroyed || this._locked)
            return;
        if (open) {
            if (this._state !== PANEL_STATES.SHOWN) {
                // Opened some other way than the keyboard shortcuts above.
                // Arrive now and re-anchor the menu to where the bar landed.
                this._revealForMenu();
                menu._boxPointer?.queue_relayout?.();
            }
            this._held = this._top.visibility !== 'always';
        }
        this._evaluate();
    }

    _keyFocusChanged() {
        if (this._destroyed || this._locked || this._top.visibility === 'always')
            return;
        if (this._panelHasKeyFocus())
            this._held = true;
        this._evaluate();
    }

    _panelHoverChanged() {
        if (this._destroyed || this._locked || this._top.visibility === 'always')
            return;
        if (this._panelBox.hover && this._state !== PANEL_STATES.HIDDEN) {
            // Resting on a visible bar holds it, so a window arriving
            // underneath does not whisk it away from under the pointer.
            this._held = true;
            this._cancelHide();
            this._evaluate();
        }
    }

    // -- reveal and retention -------------------------------------------------

    _syncWatchers() {
        if (this._destroyed || this._locked) {
            this._removeRevealTrigger();
            this._stopCorridorWatch();
            return;
        }
        const situation = this._situation();
        if (this._state === PANEL_STATES.HIDDEN && revealAllowed(situation))
            this._ensureRevealTrigger();
        else
            this._removeRevealTrigger();
        if (this._held && this._state !== PANEL_STATES.HIDDEN)
            this._startCorridorWatch();
        else
            this._stopCorridorWatch();
    }

    _reveal() {
        if (this._destroyed || !revealAllowed(this._situation()))
            return;
        this._removeRevealTrigger();
        this._cancelHide();
        this._held = true;
        this._evaluate();
    }

    _ensureRevealTrigger() {
        const top = this._top;
        const activation = edgeActivation(top.sensitivity);
        if (top.reveal_method === 'pressure') {
            if (!this._pressure)
                this._createPressureBarrier(activation.pressure);
            return;
        }
        if (this._edgeWatch)
            return;
        this._edgeWatch = PointerWatcher.getPointerWatcher().addWatch(
            EDGE_POLL_MS, (x, y) => this._edgeMoved(x, y, activation.band));
        // The pointer may already rest on the edge the bar just left.
        const [x, y] = global.get_pointer();
        this._edgeMoved(x, y, activation.band);
    }

    _createPressureBarrier(threshold) {
        const monitor = this._monitor();
        if (!monitor)
            return;
        const bottom = this._top.position === 'bottom';
        const y = bottom ? monitor.y + monitor.height : monitor.y;
        try {
            this._pressureBarrier = new Meta.Barrier({
                backend: global.backend,
                x1: monitor.x, x2: monitor.x + monitor.width, y1: y, y2: y,
                directions: bottom ? Meta.BarrierDirection.NEGATIVE_Y
                    : Meta.BarrierDirection.POSITIVE_Y,
            });
            this._pressure = new Layout.PressureBarrier(
                threshold, PRESSURE_TIMEOUT_MS, Shell.ActionMode.NORMAL);
            this._pressure.addBarrier(this._pressureBarrier);
            this._pressureTriggerId = this._pressure.connect('trigger', () => this._reveal());
        } catch (error) {
            console.warn(`desktop-forge: pressure reveal is unavailable: ${error}`);
            this._destroyPressureBarrier();
        }
    }

    _destroyPressureBarrier() {
        if (this._pressure) {
            if (this._pressureTriggerId)
                this._pressure.disconnect(this._pressureTriggerId);
            this._pressure.destroy();
        }
        this._pressureTriggerId = 0;
        this._pressure = null;
        this._pressureBarrier?.destroy();
        this._pressureBarrier = null;
    }

    _edgeMoved(x, y, band) {
        if (!pointerAtEdge(this._top.position, this._monitor(), band, x, y)) {
            this._cancelRevealDelay();
            return;
        }
        if (this._revealId)
            return;
        const delay = toMs(this._top.reveal_delay);
        if (!delay) {
            this._reveal();
            return;
        }
        this._revealId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._revealId = 0;
            if (this._destroyed)
                return GLib.SOURCE_REMOVE;
            const [px, py] = global.get_pointer();
            if (pointerAtEdge(this._top.position, this._monitor(), band, px, py))
                this._reveal();
            return GLib.SOURCE_REMOVE;
        });
    }

    _cancelRevealDelay() {
        if (this._revealId) {
            GLib.source_remove(this._revealId);
            this._revealId = 0;
        }
    }

    _removeRevealTrigger() {
        this._cancelRevealDelay();
        if (this._edgeWatch) {
            this._edgeWatch.remove();
            this._edgeWatch = null;
        }
        this._destroyPressureBarrier();
    }

    _pointerInCorridor() {
        const [x, y] = global.get_pointer();
        return pointerInPanelCorridor(
            this._top.position, this._monitor(), this._shownBarRect(), x, y);
    }

    _retained() {
        return this._pointerInCorridor() || this._menuOpen() ||
            this._panelHasKeyFocus() || this._panelBox.hover;
    }

    /**
     * Keeps a revealed bar while the pointer stays with it.
     *
     * Polled rather than hover-driven: the bar slides in under a pointer that
     * has not moved, and Clutter does not report hover for that.
     */
    _startCorridorWatch() {
        if (this._corridorId)
            return;
        this._corridorId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, CORRIDOR_CHECK_MS, () => {
            if (this._destroyed) {
                this._corridorId = 0;
                return GLib.SOURCE_REMOVE;
            }
            if (this._retained())
                this._cancelHide();
            else
                this._queueHide();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopCorridorWatch() {
        this._cancelHide();
        if (this._corridorId) {
            GLib.source_remove(this._corridorId);
            this._corridorId = 0;
        }
    }

    _queueHide() {
        if (this._hideId)
            return;
        this._hideId = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            toMs(this._top.hide_delay), () => {
                this._hideId = 0;
                if (this._destroyed || this._retained())
                    return GLib.SOURCE_REMOVE;
                this._held = false;
                this._evaluate();
                return GLib.SOURCE_REMOVE;
            });
    }

    _cancelHide() {
        if (this._hideId) {
            GLib.source_remove(this._hideId);
            this._hideId = 0;
        }
    }

    // Diagnostics read by the smoke test.
    get state() {
        return this._state;
    }

    get held() {
        return this._held;
    }

    get overlapping() {
        return this._overlap;
    }

    get revealArmed() {
        return !!(this._edgeWatch || this._pressure);
    }

    get stylesheetLoaded() {
        return this._stylesheetLoaded;
    }

    get tracking() {
        return this._tracking;
    }

    destroy() {
        this._destroyed = true;
        this._cancelOverlap();
        this._removeRevealTrigger();
        this._stopCorridorWatch();
        if (this._monitorsId) {
            GLib.source_remove(this._monitorsId);
            this._monitorsId = 0;
        }
        this._injections.clear();
        this._panelBox.disconnectObject(this);
        Main.overview.disconnectObject(this);
        Main.layoutManager.disconnectObject(this);
        global.display.disconnectObject(this);
        global.workspace_manager.disconnectObject(this);
        global.stage.disconnectObject(this);
        for (const window of this._watchedWindows)
            window.disconnectObject(this);
        this._watchedWindows.clear();
        for (const menu of this._watchedMenus)
            menu.disconnectObject(this);
        this._watchedMenus.clear();

        // Restore Shell's own arrangement from the monitors as they are now,
        // not as they were at enable time: a display unplugged in between
        // would otherwise strand the bar where the old primary used to be.
        this._panelBox.remove_all_transitions();
        this._panelBox.translation_y = 0;
        this._panelBox.show();
        const monitor = this._monitor();
        if (monitor) {
            this._panelBox.set_position(monitor.x, monitor.y);
            this._panelBox.set_size(monitor.width, -1);
        }
        this._panel.set_size(-1, -1);
        this._panel.margin_left = 0;
        this._panel.margin_right = 0;
        this._panel.margin_top = 0;
        this._panel.margin_bottom = 0;
        this._panelBox.track_hover = this._originalTrackHover;
        try {
            Main.layoutManager.untrackChrome(this._panelBox);
            Main.layoutManager.trackChrome(this._panelBox,
                {affectsStruts: true, trackFullscreen: true});
        } catch (error) {
            console.error(`desktop-forge: could not restore the top-bar work area: ${error}`);
        }
        this._tracking = null;
        try {
            Main.layoutManager._updatePanelBarrier?.();
        } catch {
            // The stock barrier is a convenience; never fail teardown over it.
        }
        this._desktopIcons?.destroy();
        this._desktopIcons = null;
        if (this._stylesheetLoaded) {
            this._theme.unload_stylesheet(this._stylesheetFile);
            this._stylesheetLoaded = false;
        }
    }
}
