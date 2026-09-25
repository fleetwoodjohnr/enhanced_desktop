import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PointerWatcher from 'resource:///org/gnome/shell/ui/pointerWatcher.js';

import {
    chromeStylesheet, panelTranslation, pointerInPanelCorridor,
    resolveChrome, resolveDockBehavior, topBarShouldHide,
} from './chromeLogic.js';
import {DesktopIconsIntegration} from './desktopIconsIntegration.js';

const DOCK_SCHEMA = 'org.gnome.shell.extensions.dash-to-dock';
const DWELL_CHECK_MS = 100;
const CORRIDOR_CHECK_MS = 250;
// A hidden bar still shows one pixel at the screen edge; the extra pixel is
// slack for the pointer landing a hair inside it.
const REVEAL_EDGE = 2;
const PANEL_STATES = Object.freeze({
    HIDDEN: 0,
    SHOWING: 1,
    SHOWN: 2,
    HIDING: 3,
});
const APPLICATION_TYPES = new Set([
    Meta.WindowType.NORMAL,
    Meta.WindowType.DOCK,
    Meta.WindowType.DIALOG,
    Meta.WindowType.MODAL_DIALOG,
    Meta.WindowType.TOOLBAR,
    Meta.WindowType.MENU,
    Meta.WindowType.UTILITY,
    Meta.WindowType.SPLASHSCREEN,
    Meta.WindowType.DROPDOWN_MENU,
]);
const IGNORED_APPLICATIONS = new Set(['com.rastersoft.ding', 'com.desktop.ding']);

/** Dash to Dock stores its delays in seconds; GLib timeouts want whole ms. */
function toMs(seconds) {
    return Math.max(0, Math.round(seconds * 1000));
}

/** Owns top-bar geometry/visibility and the generated chrome stylesheet. */
export class PanelController {
    constructor(extensionUuid) {
        this._panelBox = Main.layoutManager.panelBox;
        this._panel = Main.panel;
        this._desktopIcons = new DesktopIconsIntegration(extensionUuid);
        this._chrome = resolveChrome({}, false);
        this._behavior = resolveDockBehavior();
        this._heldOpen = false;
        this._hideId = 0;
        this._visibilityId = 0;
        this._showDelayId = 0;
        this._corridorId = 0;
        this._dwellWatch = null;
        this._edgeDwelling = false;
        this._hidden = null;
        this._panelState = PANEL_STATES.SHOWN;
        this._strut = null;
        this._stylesheetLoaded = false;
        this._watchedWindows = new Set();
        this._destroyed = false;
        this._original = {
            boxPosition: this._panelBox.get_position(),
            boxSize: this._panelBox.get_size(),
            boxTranslation: this._panelBox.translation_y,
            panelSize: this._panel.get_size(),
            trackHover: this._panelBox.track_hover,
        };

        const cacheDir = GLib.build_filenamev([GLib.get_user_cache_dir(), 'desktop-forge']);
        GLib.mkdir_with_parents(cacheDir, 0o700);
        this._stylesheetFile = Gio.File.new_for_path(
            GLib.build_filenamev([cacheDir, 'shell-chrome.css']));
        this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();

        this._dockSettings = this._findDockSettings();
        this._dockChangedId = this._dockSettings?.connect('changed', () => {
            this._syncDockBehavior();
            this._scheduleVisibility();
        }) ?? 0;
        this._syncDockBehavior(false);

        this._panelBox.track_hover = true;
        this._panelBox.connectObject(
            'notify::hover', () => this._panelHoverChanged(), this);
        Main.overview.connectObject(
            'showing', () => this._scheduleVisibility(),
            'hidden', () => this._scheduleVisibility(), this);
        Main.layoutManager.connectObject(
            'monitors-changed', () => this._applyGeometry(), this);
        global.display.connectObject(
            'notify::focus-window', () => this._scheduleVisibility(),
            'restacked', () => this._scheduleVisibility(),
            'in-fullscreen-changed', () => {
                this._scheduleVisibility();
                this._rebuildRevealTrigger();
            },
            'window-created', (_display, window) => this._watchWindow(window), this);
        global.workspace_manager.connectObject(
            'active-workspace-changed', () => this._scheduleVisibility(), this);
        for (const actor of global.get_window_actors())
            this._watchWindow(actor.meta_window);
    }

    _findDockSettings() {
        try {
            const source = Gio.SettingsSchemaSource.get_default();
            const schema = source?.lookup(DOCK_SCHEMA, true);
            return schema ? new Gio.Settings({settings_schema: schema}) : null;
        } catch (error) {
            console.warn(`desktop-forge: could not read Dash-to-Dock behavior: ${error}`);
            return null;
        }
    }

    _syncDockBehavior(rebuild = true) {
        let configured = {};
        if (this._dockSettings) {
            try {
                configured = {
                    animation_time: this._dockSettings.get_double('animation-time'),
                    show_delay: this._dockSettings.get_double('show-delay'),
                    hide_delay: this._dockSettings.get_double('hide-delay'),
                    require_pressure: this._dockSettings.get_boolean('require-pressure-to-show'),
                    pressure_threshold: this._dockSettings.get_double('pressure-threshold'),
                    autohide_in_fullscreen: this._dockSettings.get_boolean('autohide-in-fullscreen'),
                    intellihide_mode: this._dockSettings.get_string('intellihide-mode'),
                };
            } catch (error) {
                console.warn(`desktop-forge: could not refresh Dash-to-Dock behavior: ${error}`);
            }
        }
        // Timings and the fullscreen policy follow the dock. Its
        // require_pressure/pressure_threshold and intellihide_mode do not: the
        // top bar reveals on a plain edge hover and hides for any window that
        // really covers it, so those two are resolved but never read.
        this._behavior = resolveDockBehavior(configured);
        if (rebuild)
            this._rebuildRevealTrigger();
    }

    apply(configured, dark) {
        this._chrome = resolveChrome(configured, dark);
        // A reveal hold belongs to the previous geometry. In particular, a
        // bar moved to the opposite edge must not stay exposed because the
        // pointer crossed its former position before the config edit.
        this._heldOpen = false;
        this._cancelHide();
        this._stopCorridorWatch();
        this._writeStylesheet(chromeStylesheet(this._chrome));
        this._applyGeometry();
        this._updateVisibility(false, true);
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

    _applyGeometry() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const {position, height} = this._chrome.top_bar;
        const y = position === 'bottom'
            ? monitor.y + monitor.height - height : monitor.y;
        this._panelBox.set_position(monitor.x, y);
        this._panelBox.set_size(monitor.width, height);
        this._panel.set_size(monitor.width, height);
        this._desktopIcons.setPanelMargin(position, height);
        this._updateVisibility(false, true);
        this._rebuildRevealTrigger();
    }

    _watchWindow(window) {
        if (!window || this._watchedWindows.has(window))
            return;
        this._watchedWindows.add(window);
        window.connectObject(
            'size-changed', () => this._scheduleVisibility(),
            'position-changed', () => this._scheduleVisibility(),
            'workspace-changed', () => this._scheduleVisibility(),
            'notify::minimized', () => this._scheduleVisibility(),
            'notify::maximized-horizontally', () => this._scheduleVisibility(),
            'notify::maximized-vertically', () => this._scheduleVisibility(),
            'notify::fullscreen', () => this._scheduleVisibility(),
            'unmanaged', () => this._unwatchWindow(window), this);
    }

    _unwatchWindow(window) {
        window.disconnectObject(this);
        this._watchedWindows.delete(window);
        this._scheduleVisibility();
    }

    _handledWindow(window) {
        if (!window || !APPLICATION_TYPES.has(window.get_window_type()) || window.minimized)
            return false;
        const appId = window.get_gtk_application_id?.() ?? '';
        if (IGNORED_APPLICATIONS.has(appId) && window.is_skip_taskbar())
            return false;
        return window.get_monitor() === Main.layoutManager.primaryIndex &&
            window.get_workspace() === global.workspace_manager.get_active_workspace() &&
            window.showing_on_its_workspace();
    }

    /**
     * Does any visible window actually cover the bar's strip?
     *
     * Deliberately independent of which window has focus: a focus-sensitive
     * rule makes the bar flap on and off as you click between two windows that
     * both sit over it. Edges are exclusive, so a window resting flush against
     * the bar is not an overlap.
     */
    _primaryHasOverlap() {
        const target = this._shownPanelBox();
        return global.get_window_actors().some(actor => {
            const window = actor.meta_window;
            if (!this._handledWindow(window))
                return false;
            const rect = window.get_frame_rect();
            return rect.x < target.x + target.width && rect.x + rect.width > target.x &&
                rect.y < target.y + target.height && rect.y + rect.height > target.y;
        });
    }

    // Retained as a useful smoke-test diagnostic.
    _primaryHasMaximizedWindow() {
        return global.get_window_actors().some(actor => {
            const window = actor.meta_window;
            return this._handledWindow(window) &&
                (window.maximized_vertically || window.maximized_horizontally || window.fullscreen);
        });
    }

    _menuIsOpen() {
        return !!this._panel.menuManager?.activeMenu;
    }

    _scheduleVisibility() {
        if (this._visibilityId)
            return;
        const laters = global.compositor.get_laters();
        this._visibilityId = laters.add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._visibilityId = 0;
            this._updateVisibility();
            return GLib.SOURCE_REMOVE;
        });
    }

    _updateVisibility(animate = true, force = false) {
        const top = this._chrome.top_bar;
        const heldOpen = this._heldOpen || this._menuIsOpen();
        const hidden = topBarShouldHide(
            top.visibility, this._primaryHasOverlap(),
            Main.overview.visible || Main.overview.visibleTarget, heldOpen);
        // Intelligent and auto-hide bars are overlays. Keeping their strut off
        // while they reveal prevents a maximize/visibility feedback loop.
        this._setStrut(top.visibility === 'always');
        // Where the box actually is counts as much as what we want: a target
        // that already matches while the box sits at the opposite edge is
        // exactly how a reveal used to strand itself with no way back.
        const settled = hidden
            ? this._panelState === PANEL_STATES.HIDDEN ||
                this._panelState === PANEL_STATES.HIDING
            : this._panelState === PANEL_STATES.SHOWN ||
                this._panelState === PANEL_STATES.SHOWING;
        if (!force && hidden === this._hidden && settled)
            return;
        this._hidden = hidden;
        if (hidden && animate && this._panelState === PANEL_STATES.SHOWING) {
            // Match Dash to Dock: a reveal is allowed to finish before a hide
            // can consume it, avoiding the split-second flash at the edge. The
            // queued pass decides again once the box has actually arrived.
            this._queueHide();
            return;
        }
        this._animatePanel(hidden, animate);
    }

    _animatePanel(hidden, animate = true) {
        const top = this._chrome.top_bar;
        const translationY = panelTranslation(top.position, top.height, hidden);
        this._panelBox.remove_all_transitions();
        if (!animate || !St.Settings.get().enable_animations) {
            this._panelBox.translation_y = translationY;
            this._panelState = hidden ? PANEL_STATES.HIDDEN : PANEL_STATES.SHOWN;
            this._settleAtEdge(hidden);
            return;
        }

        this._panelState = hidden ? PANEL_STATES.HIDING : PANEL_STATES.SHOWING;
        if (!hidden)
            this._removeRevealTrigger();
        this._panelBox.ease({
            translation_y: translationY,
            duration: toMs(this._behavior.animation_time),
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onComplete: () => {
                if (this._destroyed)
                    return;
                this._panelState = hidden ? PANEL_STATES.HIDDEN : PANEL_STATES.SHOWN;
                this._settleAtEdge(hidden);
            },
        });
    }

    /** Arm whichever watchdog belongs to the edge the box just arrived at. */
    _settleAtEdge(hidden) {
        if (hidden) {
            this._stopCorridorWatch();
            this._rebuildRevealTrigger();
            return;
        }
        this._removeRevealTrigger();
        if (this._heldOpen)
            this._startCorridorWatch();
    }

    _setStrut(value) {
        if (this._strut === value)
            return;
        this._strut = value;
        try {
            Main.layoutManager.untrackChrome(this._panelBox);
            Main.layoutManager.trackChrome(this._panelBox, {
                affectsStruts: value,
                trackFullscreen: true,
            });
        } catch (error) {
            console.error(`desktop-forge: could not update top-bar work area: ${error}`);
        }
    }

    _panelHoverChanged() {
        if (this._panelBox.hover) {
            // A hidden bar still shows a one-pixel sliver, so this is the
            // pointer landing on the reveal edge itself: the promptest dwell
            // signal there is, and it costs no polling.
            if (this._panelState === PANEL_STATES.HIDDEN)
                this._armRevealDwell();
            else
                this._holdOpen();
            return;
        }
        this._cancelShowDelay();
        if (this._panelState !== PANEL_STATES.HIDDEN)
            this._queueHide();
    }

    _queueHide() {
        this._cancelHide();
        if (this._chrome.top_bar.visibility === 'always')
            return;
        this._hideId = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            toMs(this._behavior.hide_delay), () => {
                this._hideId = 0;
                if (this._destroyed)
                    return GLib.SOURCE_REMOVE;
                // Only keep waiting while there is something still on screen
                // to wait for; re-queueing against a hidden bar just spins.
                if (this._panelState !== PANEL_STATES.HIDDEN &&
                    (this._menuIsOpen() || this._pointerInCorridor())) {
                    this._queueHide();
                    return GLib.SOURCE_REMOVE;
                }
                this._heldOpen = false;
                this._updateVisibility();
                return GLib.SOURCE_REMOVE;
            });
    }

    _holdOpen() {
        this._cancelHide();
        this._cancelShowDelay();
        this._heldOpen = true;
        this._startCorridorWatch();
        this._updateVisibility();
    }

    _triggerReveal() {
        if (!this._revealAllowed())
            return;
        this._removeRevealTrigger();
        this._holdOpen();
    }

    _shownPanelBox() {
        const monitor = Main.layoutManager.primaryMonitor;
        const {position, height} = this._chrome.top_bar;
        return {
            x: monitor?.x ?? 0,
            y: position === 'bottom' && monitor
                ? monitor.y + monitor.height - height : monitor?.y ?? 0,
            width: monitor?.width ?? 0,
            height,
        };
    }

    _pointerInCorridor() {
        const [pointerX, pointerY] = global.get_pointer();
        return pointerInPanelCorridor(
            this._chrome.top_bar.position, Main.layoutManager.primaryMonitor,
            this._shownPanelBox(), pointerX, pointerY);
    }

    /**
     * The retention watchdog for a revealed bar.
     *
     * Hover alone cannot carry this: the hidden bar's sliver is already
     * hovered before a reveal, so notify::hover never fires as it slides in.
     */
    _startCorridorWatch() {
        if (this._corridorId || this._destroyed ||
            this._chrome.top_bar.visibility === 'always')
            return;
        this._corridorId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, CORRIDOR_CHECK_MS, () => {
            if (this._destroyed) {
                this._corridorId = 0;
                return GLib.SOURCE_REMOVE;
            }
            if (this._pointerInCorridor() || this._menuIsOpen() || this._panelBox.hover)
                return GLib.SOURCE_CONTINUE;
            this._corridorId = 0;
            this._queueHide();
            return GLib.SOURCE_REMOVE;
        });
    }

    _stopCorridorWatch() {
        if (this._corridorId) {
            GLib.source_remove(this._corridorId);
            this._corridorId = 0;
        }
    }

    _revealAllowed() {
        const monitor = Main.layoutManager.primaryMonitor;
        return this._chrome.top_bar.visibility !== 'always' &&
            (!monitor?.inFullscreen || this._behavior.autohide_in_fullscreen);
    }

    _edgeContains(x, y) {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return false;
        const insideX = x >= monitor.x && x < monitor.x + monitor.width;
        if (!insideX)
            return false;
        return this._chrome.top_bar.position === 'bottom'
            ? y >= monitor.y + monitor.height - REVEAL_EDGE &&
                y < monitor.y + monitor.height
            : y >= monitor.y && y < monitor.y + REVEAL_EDGE;
    }

    _armRevealDwell() {
        if (this._showDelayId || this._edgeDwelling || !this._revealAllowed())
            return;
        this._edgeDwelling = true;
        this._showDelayId = GLib.timeout_add(GLib.PRIORITY_DEFAULT,
            toMs(this._behavior.show_delay), () => {
                this._showDelayId = 0;
                this._edgeDwelling = false;
                if (!this._destroyed && this._pointerOnRevealEdge())
                    this._triggerReveal();
                return GLib.SOURCE_REMOVE;
            });
    }

    _pointerOnRevealEdge() {
        return this._panelBox.hover ||
            this._edgeContains(...global.get_pointer().slice(0, 2));
    }

    _checkDwell(x, y) {
        if (this._edgeContains(x, y) || this._panelBox.hover)
            this._armRevealDwell();
        else
            this._cancelShowDelay();
    }

    _rebuildRevealTrigger() {
        this._removeRevealTrigger();
        if (this._destroyed || this._panelState !== PANEL_STATES.HIDDEN ||
            !this._hidden || !this._revealAllowed())
            return;
        // Backstop for the sliver's own hover, which anything drawn over the
        // screen edge would otherwise swallow.
        this._dwellWatch = PointerWatcher.getPointerWatcher().addWatch(
            DWELL_CHECK_MS, (x, y) => this._checkDwell(x, y));
        // The pointer may already be resting on the edge that just closed.
        this._checkDwell(...global.get_pointer().slice(0, 2));
    }

    _removeRevealTrigger() {
        this._cancelShowDelay();
        if (this._dwellWatch) {
            this._dwellWatch.remove();
            this._dwellWatch = null;
        }
    }

    _cancelShowDelay() {
        this._edgeDwelling = false;
        if (this._showDelayId) {
            GLib.source_remove(this._showDelayId);
            this._showDelayId = 0;
        }
    }

    _cancelHide() {
        if (this._hideId) {
            GLib.source_remove(this._hideId);
            this._hideId = 0;
        }
    }

    destroy() {
        this._destroyed = true;
        this._cancelHide();
        this._removeRevealTrigger();
        this._stopCorridorWatch();
        if (this._visibilityId) {
            global.compositor.get_laters().remove(this._visibilityId);
            this._visibilityId = 0;
        }
        this._panelBox.disconnectObject(this);
        Main.overview.disconnectObject(this);
        Main.layoutManager.disconnectObject(this);
        global.display.disconnectObject(this);
        global.workspace_manager.disconnectObject(this);
        for (const window of this._watchedWindows)
            window.disconnectObject(this);
        this._watchedWindows.clear();
        if (this._dockChangedId)
            this._dockSettings.disconnect(this._dockChangedId);
        this._dockChangedId = 0;
        this._dockSettings = null;

        this._panelBox.remove_all_transitions();
        this._panelBox.translation_y = this._original.boxTranslation;
        this._panelBox.set_position(...this._original.boxPosition);
        this._panelBox.set_size(...this._original.boxSize);
        this._panel.set_size(...this._original.panelSize);
        this._panelBox.track_hover = this._original.trackHover;
        this._setStrut(true);
        this._desktopIcons?.destroy();
        this._desktopIcons = null;
        if (this._stylesheetLoaded) {
            this._theme.unload_stylesheet(this._stylesheetFile);
            this._stylesheetLoaded = false;
        }
    }
}
