import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {EditMode} from './editMode.js';
import {
    MIN_HEIGHT, MIN_WIDTH, clampPosition, monitorForEntry, settlePosition,
    workAreaForEntry,
} from './geometry.js';
import {CONFIG_PATH, STATE_DIR, isOwnWrite, readJson, watchJson, writeJson} from './store.js';
import {systemIsDark} from './themeLogic.js';
import {newsOptions, rectanglesOverlap, widgetLayer} from './widgetLogic.js';

import {CalendarWidget} from './widgets/calendar.js';
import {ClockWidget} from './widgets/clock.js';
import {NewsWidget} from './widgets/news.js';
import {RemindersWidget} from './widgets/reminders.js';
import {StocksWidget} from './widgets/stocks.js';
import {SystemWidget} from './widgets/system.js';
import {TodosWidget} from './widgets/todos.js';
import {WeatherWidget} from './widgets/weather.js';
import {CliveWidget} from './widgets/clive.js';
import {CliveClient} from './cliveClient.js';

const WIDGET_CLASSES = {
    clive: CliveWidget,
    clock: ClockWidget,
    weather: WeatherWidget,
    stocks: StocksWidget,
    calendar: CalendarWidget,
    news: NewsWidget,
    reminders: RemindersWidget,
    todos: TodosWidget,
    system: SystemWidget,
};

/** Which provider each widget type renders. Mirrors config.WIDGET_TYPES. */
const WIDGET_PROVIDER = {
    clive: null,
    clock: null,
    weather: 'weather',
    stocks: 'stocks',
    calendar: 'calendar',
    news: 'news',
    reminders: 'reminders',
    todos: 'todos',
    system: 'system',
};

const DEFAULT_STYLE = {
    opacity: 0.58,
    corner_radius: 24,
    accent: '#0a84ff',
    font_scale: 1.0,
    theme_mode: 'system',
    colorful_accents: true,
    blur_radius: 24,
};

const LIGHT_GLASS = {
    background: '#f8fbff',
    text_color: '#172033',
    glass_highlight: '#ffffff',
    dark: false,
};

const DARK_GLASS = {
    background: '#18202c',
    text_color: '#f7faff',
    glass_highlight: '#ffffff',
    dark: true,
};

const WIDGET_ACCENTS = {
    clive: '#32ade6',
    clock: '#bf5af2',
    weather: '#32ade6',
    stocks: '#0a84ff',
    calendar: '#ff9f0a',
    reminders: '#ff375f',
    todos: '#5e5ce6',
    news: '#af52de',
    system: '#30d158',
};

// Long enough to coalesce a burst of writes from the settings app, short
// enough that a change still feels immediate.
const REBUILD_DELAY_MS = 400;
const SAVE_DELAY_MS = 300;

export default class DesktopForgeExtension extends Extension {
    enable() {
        this._clive = null;
        this._clivePanel = null;
        this._widgets = [];
        this._stateMonitors = new Map();
        this._configMonitor = null;
        this._rebuildId = 0;
        this._saveId = 0;
        this._editMode = null;
        this._rebuildPending = false;
        this._fingerprint = null;
        this._disabling = false;
        this._interactionId = 0;
        this._windowActors = new Map();
        this._pendingWindows = new Set();
        this._interfaceSettings = new Gio.Settings({
            schema_id: 'org.gnome.desktop.interface',
        });
        this._interfaceSettings.connectObject(
            'changed::color-scheme', () => {
                this._syncShellTheme();
                if (this._editMode)
                    this._rebuildPending = true;
                else
                    this._scheduleRebuild();
            }, this);

        this._syncShellTheme();

        this._watchConfig();
        this._setupInteractionTracking();
        this._build();

        // Widgets are positioned per monitor, so plugging in the HDMI output
        // (or unplugging it) has to re-place every one of them or they end up
        // stranded off-screen.
        Main.layoutManager.connectObject(
            'monitors-changed', () => this._positionAll(), this);
    }

    disable() {
        // Set before anything else: closing edit mode calls back into
        // _onEditDone(), which would otherwise schedule a rebuild that fires
        // after the extension is gone.
        this._disabling = true;
        Main.layoutManager.disconnectObject(this);
        global.display.disconnectObject(this);
        global.workspace_manager.disconnectObject(this);
        global.window_group.disconnectObject(this);
        Main.overview.disconnectObject(this);
        for (const record of this._windowActors.values())
            this._untrackWindow(record);
        for (const window of this._pendingWindows)
            window.disconnectObject(this);
        this._pendingWindows.clear();

        // Leaving edit mode first returns the cards to the desktop, so
        // _destroyWidgets() finds them where it expects them.
        this._editMode?.close();
        this._editMode = null;

        // A drag that ended in the last few hundred milliseconds still has its
        // position sitting in a pending write.
        if (this._saveId) {
            GLib.source_remove(this._saveId);
            this._saveId = 0;
            this._flushGeometry();
        }
        if (this._rebuildId) {
            GLib.source_remove(this._rebuildId);
            this._rebuildId = 0;
        }
        if (this._interactionId) {
            global.compositor.get_laters().remove(this._interactionId);
            this._interactionId = 0;
        }

        this._interfaceSettings?.disconnectObject(this);
        this._interfaceSettings = null;
        this._clearShellTheme();

        this._configMonitor?.cancel();
        this._configMonitor = null;

        for (const monitor of this._stateMonitors.values())
            monitor.cancel();
        this._stateMonitors.clear();

        this._destroyWidgets();
        this._cliveUnsubscribe?.();
        this._clivePanel?.destroy();
        this._clivePanel = null;
        this._clive?.destroy();
        this._clive = null;
    }

    // -- construction ------------------------------------------------------

    _build() {
        const config = readJson(CONFIG_PATH) ?? {widgets: [], style: {}};
        this._fingerprint = fingerprint(config);
        const baseStyle = resolveStyle(config.style ?? {}, this._interfaceSettings);

        for (const entry of config.widgets ?? []) {
            if (entry.enabled === false)
                continue;
            const WidgetClass = WIDGET_CLASSES[entry.type];
            if (!WidgetClass)
                continue;

            const widgetStyle = {...baseStyle};
            if (baseStyle.colorful_accents)
                widgetStyle.accent = WIDGET_ACCENTS[entry.type] ?? baseStyle.accent;
            Object.assign(widgetStyle, entry.style ?? {});
            const widget = new WidgetClass(entry, widgetStyle);
            if (entry.type === 'clive') {
                this._ensureClive();
                widget.setClient(this._clive);
            }
            if (entry.type === 'news')
                widget.setNewsFilter(newsOptions(config));
            widget.connect('geometry-changed', (_w, x, y, width, height) => {
                // In edit mode the overlay snaps the result first and saves
                // it itself, so this would only write an unsnapped duplicate.
                if (this._editMode)
                    return;
                this._settleAndSave(widget, entry, x, y, width, height);
            });

            const record = {widget, entry, layer: null};
            this._widgets.push(record);
            this._setWidgetLayer(record, 'background');

            const provider = WIDGET_PROVIDER[entry.type];
            if (provider)
                this._watchState(provider);
        }

        this._positionAll();
        this._updateInteractionLayers();
        this._refreshAll();

        if (config.edit_layout)
            this._enterEditMode();
    }

    /**
     * Put an actor on the desktop.
     *
     * _backgroundGroup is the layer between the wallpaper and the windows, so
     * a widget added there behaves like part of the desktop -- visible when
     * windows are moved aside, never floating over them. It is a private
     * field of the layout manager, but it is the only way to reach that layer
     * and is what every mature desktop-widget extension uses.
     *
     * Interactive cards move into regular chrome only while their rectangle is
     * clear. Shell 50 no longer builds an input region for trackChrome(), so a
     * reactive actor left inside _backgroundGroup cannot receive pointer input.
     * Moving a covered card back here keeps it visually behind applications.
     */
    _setWidgetLayer(record, layer) {
        if (record.layer === layer && record.widget.get_parent())
            return;
        if (layer === 'background')
            record.widget.clearDesktopInteraction();
        this._removeFromDesktop(record.widget);
        if (layer === 'chrome') {
            Main.layoutManager.addChrome(record.widget);
            // Keep desktop interactions below Shell's transient overlay plane.
            // Dash-to-Dock labels and popup menus are uiGroup children and
            // must remain above a temporarily interactive desktop card.
            const uiGroup = Main.uiGroup ?? Main.layoutManager.uiGroup;
            const panelBox = Main.layoutManager.panelBox;
            if (record.widget.get_parent() === uiGroup &&
                panelBox?.get_parent() === uiGroup)
                uiGroup.set_child_below_sibling(record.widget, panelBox);
        } else {
            Main.layoutManager._backgroundGroup.add_child(record.widget);
        }
        record.widget.show();
        record.layer = layer;
    }

    _ensureClive() {
        if (this._clive)
            return;
        this._clive = new CliveClient();
        this._clivePanel = new PanelMenu.Button(0.0, 'CLIVE');
        // A card covered by a window drops to the background layer and stops
        // taking pointer input, so this menu is the only remaining way to
        // reach CLIVE. It stays visible; only its label follows the task.
        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        box.add_child(new St.Icon({
            icon_name: 'system-help-symbolic', style_class: 'system-status-icon',
        }));
        const indicator = new St.Label({
            text: 'CLIVE', y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(indicator);
        this._clivePanel.add_child(box);
        this._clivePanel.menu.addAction('Open CLIVE', () => this._clive.open());
        // A task waiting on approval can otherwise only be answered from the
        // card, which is exactly what a covering window takes away.
        const approve = this._clivePanel.menu.addAction('Approve task', () => this._clive.call(
            'approve', {id: this._clive.state?.id, version: this._clive.state?.version}));
        approve.setSensitive(false);
        this._clivePanel.menu.addAction('Stop task', () => this._clive.call('cancel'));
        Main.panel.addToStatusArea('desktop-forge-clive', this._clivePanel);
        this._cliveUnsubscribe = this._clive.subscribe(state => {
            const waiting = state.status === 'awaiting_approval';
            const busy = ['planning', 'running', 'awaiting_approval'].includes(state.status);
            approve.setSensitive(waiting);
            indicator.text = waiting ? 'CLIVE · approve' : busy ? 'CLIVE · working' : 'CLIVE';
        });
    }

    _syncShellTheme() {
        const uiGroup = Main.uiGroup ?? Main.layoutManager.uiGroup;
        if (!uiGroup)
            return;
        const colorScheme = this._interfaceSettings?.get_string('color-scheme');
        const dark = systemIsDark(colorScheme, Main.getStyleVariant?.());
        uiGroup.remove_style_class_name(dark ? 'df-shell-light' : 'df-shell-dark');
        uiGroup.add_style_class_name(dark ? 'df-shell-dark' : 'df-shell-light');
    }

    _clearShellTheme() {
        const uiGroup = Main.uiGroup ?? Main.layoutManager.uiGroup;
        uiGroup?.remove_style_class_name('df-shell-light');
        uiGroup?.remove_style_class_name('df-shell-dark');
    }

    _removeFromDesktop(widget) {
        const parent = widget.get_parent();
        if (!parent)
            return;
        if (parent === Main.layoutManager.uiGroup)
            Main.layoutManager.removeChrome(widget);
        else
            parent.remove_child(widget);
    }

    _destroyWidgets() {
        for (const record of this._widgets ?? []) {
            const {widget} = record;
            widget.clearDesktopInteraction();
            this._removeFromDesktop(widget);
            record.layer = null;
            widget.destroy();
        }
        this._widgets = [];
    }

    _positionAll() {
        for (const {widget, entry} of this._widgets) {
            const monitor = monitorForEntry(entry);
            const bounds = workAreaForEntry(entry);
            if (!monitor || !bounds)
                continue;

            const width = Math.min(
                bounds.width, Math.max(MIN_WIDTH, entry.width || MIN_WIDTH));
            const height = Math.min(
                bounds.height, Math.max(MIN_HEIGHT, entry.height || MIN_HEIGHT));
            widget.set_size(width, height);

            // Coordinates are stored relative to the widget's own monitor, so
            // a layout survives monitors being rearranged. Clamped rather than
            // snapped: a saved layout should come back exactly as saved.
            const [x, y] = clampPosition(
                bounds,
                monitor.x + (entry.x ?? 0),
                monitor.y + (entry.y ?? 0),
                width, height);
            widget.set_position(x, y);

            const relativeX = Math.round(x - monitor.x);
            const relativeY = Math.round(y - monitor.y);
            if (entry.x !== relativeX || entry.y !== relativeY ||
                entry.width !== width || entry.height !== height)
                this._saveGeometry(entry, x, y, width, height);
        }
        this._queueInteractionUpdate();
    }

    // -- desktop-only interaction -----------------------------------------

    _setupInteractionTracking() {
        global.display.connectObject(
            'window-created', (_display, window) => {
                // A newly-created window may not have an allocated actor yet.
                // Put every interactive card somewhere unquestionably safe
                // until the before-redraw pass can inspect the live actor list.
                this._pendingWindows.add(window);
                window.connectObject('unmanaged', () => {
                    this._pendingWindows.delete(window);
                    this._queueInteractionUpdate();
                }, this);
                this._demoteInteractiveWidgets();
                this._syncWindowActors();
            },
            'restacked', () => {
                this._syncWindowActors();
                this._queueInteractionUpdate();
            },
            'in-fullscreen-changed', () => {
                this._demoteInteractiveWidgets();
                this._queueInteractionUpdate();
            },
            'workareas-changed', () => this._positionAll(), this);
        global.workspace_manager.connectObject(
            'active-workspace-changed', () => {
                this._demoteInteractiveWidgets();
                this._positionAll();
                this._syncWindowActors();
            }, this);
        Main.overview.connectObject(
            'showing', () => {
                this._demoteInteractiveWidgets();
                this._queueInteractionUpdate();
            },
            'hidden', () => this._queueInteractionUpdate(), this);
        this._syncWindowActors();
    }

    _syncWindowActors() {
        if (this._disabling)
            return;
        const actors = new Set(global.get_window_actors());
        for (const record of this._windowActors.values()) {
            if (!actors.has(record.actor))
                this._untrackWindow(record);
        }
        for (const actor of actors) {
            if (this._windowActors.has(actor))
                continue;
            const metaWindow = actor.meta_window;
            this._pendingWindows.delete(metaWindow);
            const record = {actor, window: metaWindow};
            this._windowActors.set(actor, record);
            actor.connectObject(
                'destroy', () => {
                    this._untrackWindow(record);
                    this._queueInteractionUpdate();
                },
                'notify::allocation', () => this._queueInteractionUpdate(),
                'notify::visible', () => this._queueInteractionUpdate(),
                'notify::x', () => this._queueInteractionUpdate(),
                'notify::y', () => this._queueInteractionUpdate(),
                'notify::width', () => this._queueInteractionUpdate(),
                'notify::height', () => this._queueInteractionUpdate(), this);
            metaWindow?.connectObject(
                'notify::minimized', () => this._queueInteractionUpdate(),
                'notify::fullscreen', () => this._queueInteractionUpdate(),
                'notify::maximized-horizontally', () => this._queueInteractionUpdate(),
                'notify::maximized-vertically', () => this._queueInteractionUpdate(),
                'position-changed', () => this._queueInteractionUpdate(),
                'size-changed', () => this._queueInteractionUpdate(),
                'workspace-changed', () => this._queueInteractionUpdate(),
                'unmanaged', () => {
                    this._pendingWindows.delete(metaWindow);
                    metaWindow.disconnectObject(this);
                    record.window = null;
                    this._queueInteractionUpdate();
                }, this);
        }
        this._queueInteractionUpdate();
    }

    _untrackWindow(record) {
        if (!this._windowActors.delete(record.actor))
            return;
        record.actor.disconnectObject(this);
        // Never read actor.meta_window here: the destroy signal may already
        // have disposed the window. Unmanaged cleared our saved reference.
        record.window?.disconnectObject(this);
        this._pendingWindows.delete(record.window);
        record.window = null;
    }

    _queueInteractionUpdate() {
        if (this._interactionId || this._disabling)
            return;
        const laters = global.compositor.get_laters();
        this._interactionId = laters.add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._interactionId = 0;
            this._updateInteractionLayers();
            return GLib.SOURCE_REMOVE;
        });
    }

    _demoteInteractiveWidgets() {
        if (this._editMode || this._disabling)
            return;
        for (const record of this._widgets) {
            if (record.widget.interactive)
                this._setWidgetLayer(record, 'background');
        }
    }

    _updateInteractionLayers() {
        if (this._editMode || this._disabling)
            return;
        for (const record of this._widgets) {
            const layer = widgetLayer(
                record.widget.interactive,
                this._widgetIsCovered(record));
            this._setWidgetLayer(record, layer);
        }
    }

    _widgetIsCovered(record) {
        if (Main.overview.visible)
            return true;
        const {widget, entry} = record;
        const monitor = monitorForEntry(entry);
        if (monitor?.inFullscreen)
            return true;
        const [x, y] = widget.get_position();
        const [width, height] = widget.get_size();
        const widgetRect = {x, y, width, height};
        const active = global.workspace_manager.get_active_workspace();
        const actors = global.get_window_actors();
        for (const actor of actors)
            this._pendingWindows.delete(actor.meta_window);
        if (this._pendingWindows.size)
            return true;

        // Do not trust the signal-maintained cache for correctness. In
        // particular, newly-created Wayland actors can arrive between the
        // window-created and restacked signals. The live list is the source of
        // truth; the cache exists only to subscribe to geometry changes.
        for (const actor of actors) {
            const window = actor.meta_window;
            if (!window || window.minimized)
                continue;
            const type = window.get_window_type();
            if (type === Meta.WindowType.DESKTOP || type === Meta.WindowType.DOCK)
                continue;
            if (!window.is_on_all_workspaces()) {
                const workspace = window.get_workspace();
                if (!workspace || workspace.index() !== active.index())
                    continue;
            }
            if (!window.showing_on_its_workspace())
                continue;
            const frame = window.get_frame_rect();
            if (rectanglesOverlap(widgetRect, frame))
                return true;
        }
        return false;
    }

    // -- edit mode ---------------------------------------------------------

    _enterEditMode() {
        if (this._editMode)
            return;
        // Edit mode pushes its own modal grab; a card still holding the
        // keyboard would sit underneath it with its typing cue stuck on.
        for (const {widget} of this._widgets)
            widget.clearDesktopInteraction();
        this._editMode = new EditMode({
            entries: this._widgets,
            onDetach: widget => {
                const record = this._widgets.find(item => item.widget === widget);
                if (record) {
                    this._removeFromDesktop(widget);
                    record.layer = null;
                }
            },
            onGeometry: (entry, x, y, width, height) =>
                this._saveGeometry(entry, x, y, width, height),
            onDone: () => this._onEditDone(),
        });
        this._editMode.open();
    }

    _onEditDone() {
        this._editMode = null;

        // The overlay handed the cards back unparented; put them where they
        // normally live and re-apply the layout they now have.
        for (const record of this._widgets)
            this._setWidgetLayer(record, 'background');
        this._positionAll();
        this._updateInteractionLayers();

        if (this._disabling)
            return;

        this._writeConfig(config => {
            config.edit_layout = false;
        });

        if (this._rebuildPending) {
            this._rebuildPending = false;
            this._scheduleRebuild();
        }
    }

    // -- data --------------------------------------------------------------

    _watchState(provider) {
        if (this._stateMonitors.has(provider))
            return;
        let monitor;
        try {
            monitor = watchJson(GLib.build_filenamev([STATE_DIR, `${provider}.json`]),
                () => this._refreshProvider(provider));
        } catch {
            return;
        }
        this._stateMonitors.set(provider, monitor);
    }

    _refreshAll() {
        for (const provider of new Set(Object.values(WIDGET_PROVIDER).filter(p => p)))
            this._refreshProvider(provider);
        // Providerless widgets (the clock) still need one update to leave the
        // "waiting for the daemon" state they start in.
        for (const {widget, entry} of this._widgets) {
            if (!WIDGET_PROVIDER[entry.type])
                widget.setStatus(null, false);
        }
    }

    _refreshProvider(provider) {
        const state = readJson(GLib.build_filenamev([STATE_DIR, `${provider}.json`]));
        for (const {widget, entry} of this._widgets) {
            if (WIDGET_PROVIDER[entry.type] === provider)
                widget.update(state);
        }
    }

    // -- config ------------------------------------------------------------

    _watchConfig() {
        try {
            this._configMonitor = watchJson(CONFIG_PATH, () => this._onConfigChanged());
        } catch {
            return;
        }
    }

    _onConfigChanged() {
        // Our own geometry writes come straight back through this monitor.
        // Rebuilding on them would destroy and recreate the very card the user
        // is dragging, so the write is recognised and ignored.
        if (isOwnWrite(CONFIG_PATH))
            return;

        const config = readJson(CONFIG_PATH);
        if (!config)
            return;

        const changed = fingerprint(config) !== this._fingerprint;

        // Filtering must take effect before a deferred layout rebuild, and
        // must discard pending hover data from the previous request.
        for (const {widget, entry} of this._widgets) {
            if (entry.type === 'news')
                widget.setNewsFilter(newsOptions(config));
        }
        this._refreshProvider('news');

        if (config.edit_layout && !this._editMode) {
            this._enterEditMode();
            this._rebuildPending = changed;
            return;
        }
        if (!config.edit_layout && this._editMode) {
            this._rebuildPending = changed;
            this._editMode.close();
            return;
        }

        if (!changed)
            return;
        if (this._editMode) {
            // A rebuild now would destroy actors the overlay is holding.
            this._rebuildPending = true;
            return;
        }
        this._scheduleRebuild();
    }

    _scheduleRebuild() {
        // Debounce: the GTK app writes the whole file on every keystroke in
        // the settings, and rebuilding every actor per keystroke is both
        // visibly janky and wasteful.
        if (this._rebuildId)
            GLib.source_remove(this._rebuildId);
        this._rebuildId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, REBUILD_DELAY_MS, () => {
            this._rebuildId = 0;
            this._destroyWidgets();
            this._build();
            return GLib.SOURCE_REMOVE;
        });
    }

    // -- config writes -----------------------------------------------------

    /** Snap a card dragged outside edit mode, then persist where it landed. */
    _settleAndSave(widget, entry, x, y, width, height) {
        const monitor = monitorForEntry(entry);
        const bounds = workAreaForEntry(entry);
        if (!monitor || !bounds)
            return;
        const peers = [];
        for (const other of this._widgets) {
            if (other.widget === widget || monitorForEntry(other.entry) !== monitor)
                continue;
            const [px, py] = other.widget.get_position();
            const [pwidth, pheight] = other.widget.get_size();
            peers.push({x: px, y: py, width: pwidth, height: pheight});
        }
        const [sx, sy] = settlePosition(bounds, x, y, width, height, peers);
        widget.set_position(sx, sy);
        this._saveGeometry(entry, sx, sy, width, height);
    }

    /**
     * Persist a widget's position and size.
     *
     * Coalesced, because a resize drag settles once per gesture but a monitor
     * change can settle every card at once.
     */
    _saveGeometry(entry, x, y, width, height) {
        const monitor = monitorForEntry(entry);
        entry.x = Math.round(x - (monitor?.x ?? 0));
        entry.y = Math.round(y - (monitor?.y ?? 0));
        entry.width = Math.round(width);
        entry.height = Math.round(height);

        if (this._saveId)
            GLib.source_remove(this._saveId);
        this._saveId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, SAVE_DELAY_MS, () => {
            this._saveId = 0;
            this._flushGeometry();
            return GLib.SOURCE_REMOVE;
        });
    }

    _flushGeometry() {
        this._writeConfig(config => {
            for (const {entry} of this._widgets) {
                const stored = config.widgets?.find(w => w.id === entry.id);
                if (!stored)
                    continue;
                stored.x = entry.x;
                stored.y = entry.y;
                stored.width = entry.width;
                stored.height = entry.height;
            }
        });
    }

    /**
     * Read-modify-write config.json.
     *
     * Re-reading the file first and changing only what this extension owns
     * means a drag never clobbers a setting the GTK app wrote in the meantime.
     */
    _writeConfig(mutate) {
        const config = readJson(CONFIG_PATH);
        if (!config?.widgets)
            return;
        mutate(config);
        this._fingerprint = fingerprint(config);
        writeJson(CONFIG_PATH, config);
    }
}

/**
 * Everything about a config that requires rebuilding the widgets.
 *
 * edit_layout is excluded: entering and leaving edit mode moves the existing
 * actors around, and rebuilding them would throw away the cards mid-edit.
 */
function fingerprint(config) {
    const {edit_layout: _editLayout, ...rest} = config ?? {};
    return JSON.stringify(rest);
}

function resolveStyle(configured, interfaceSettings) {
    const requestedMode = configured.theme_mode ?? DEFAULT_STYLE.theme_mode;
    const themeMode = ['system', 'light', 'dark'].includes(requestedMode)
        ? requestedMode : 'system';
    const systemDark = systemIsDark(
        interfaceSettings?.get_string('color-scheme'), Main.getStyleVariant?.());
    const dark = themeMode === 'dark' || (themeMode === 'system' && systemDark);

    // Before schema v2, setting accent meant a single global accent. Keep that
    // behavior unless colorful_accents is explicitly present.
    const hasAccentOverride = Object.hasOwn(configured, 'accent');
    const colorfulAccents = Object.hasOwn(configured, 'colorful_accents')
        ? !!configured.colorful_accents : !hasAccentOverride;

    return {
        ...DEFAULT_STYLE,
        ...(dark ? DARK_GLASS : LIGHT_GLASS),
        ...configured,
        theme_mode: themeMode,
        colorful_accents: colorfulAccents,
        dark,
    };
}
