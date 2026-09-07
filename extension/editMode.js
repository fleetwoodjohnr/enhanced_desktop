/**
 * Layout editing on the desktop.
 *
 * Cards normally live behind application windows, where input delivery is not
 * reliable. Edit mode temporarily reparents them into a full-screen modal
 * overlay. Transparent move surfaces cover their normal controls, and eight
 * overlay grips provide resizing from every edge and corner.
 */
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {
    MIN_HEIGHT, MIN_WIDTH, clampPosition, monitorForEntry, settlePosition,
    settleResize, workAreaForEntry,
} from './geometry.js';

const GRIP_SIZE = 18;
const RESIZE_DIRECTIONS = [
    {name: 'nw', horizontal: -1, vertical: -1},
    {name: 'n', horizontal: 0, vertical: -1},
    {name: 'ne', horizontal: 1, vertical: -1},
    {name: 'w', horizontal: -1, vertical: 0},
    {name: 'e', horizontal: 1, vertical: 0},
    {name: 'sw', horizontal: -1, vertical: 1},
    {name: 's', horizontal: 0, vertical: 1},
    {name: 'se', horizontal: 1, vertical: 1},
];

export class EditMode {
    /**
     * @param {object} params
     * @param {Array<{widget: object, entry: object}>} params.entries widgets on screen
     * @param {Function} params.onDetach takes a card off the desktop layer
     * @param {Function} params.onGeometry called with (entry, x, y, width, height)
     * @param {Function} params.onDone called once, when the user leaves edit mode
     */
    constructor({entries, onDetach, onGeometry, onDone}) {
        this._entries = entries;
        this._onDetach = onDetach;
        this._onGeometry = onGeometry;
        this._onDone = onDone;
        this._controls = [];
        this._controlSets = new Map();
        this._activeWidget = null;
        this._grab = null;
        this._liveGeometry = null;
        this._closed = false;

        this._backdrop = new St.Widget({
            style_class: 'df-edit-backdrop',
            layout_manager: new Clutter.FixedLayout(),
            reactive: true,
            can_focus: true,
            visible: false,
        });
        this._backdrop.add_constraint(new Clutter.BindConstraint({
            source: global.stage,
            coordinate: Clutter.BindCoordinate.ALL,
        }));
    }

    open() {
        Main.layoutManager.addTopChrome(this._backdrop);

        // A Clutter grab reaches only the grabbed actor and descendants, so
        // every card has to become a child of this overlay while editing.
        for (const {widget} of this._entries) {
            const [x, y] = widget.get_position();
            this._onDetach(widget);
            this._backdrop.add_child(widget);
            widget.set_position(x, y);
            widget.setBlurEnabled(false);
        }

        // All surfaces come after all cards, and all grips after all surfaces.
        // This keeps a resize handle reachable even where two cards overlap.
        for (const {widget, entry} of this._entries)
            this._addMoveSurface(widget, entry);
        for (const {widget, entry} of this._entries) {
            for (const direction of RESIZE_DIRECTIONS)
                this._addGrip(widget, entry, direction);
        }

        this._activateWidget(this._entries[0]?.widget ?? null);

        this._addHintBar();

        this._backdrop.connect('key-press-event', (_actor, event) => {
            if (event.get_key_symbol() === Clutter.KEY_Escape) {
                this.close();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._backdrop.connect('button-press-event', (actor, event) => {
            if (event.get_source() !== actor)
                return Clutter.EVENT_PROPAGATE;
            this.close();
            return Clutter.EVENT_STOP;
        });

        // Reveal one complete frame instead of showing cards, surfaces and
        // handles in separate allocation passes while edit mode is assembled.
        this._backdrop.show();
        this._grab = Main.pushModal(this._backdrop);
        global.stage.set_key_focus(this._backdrop);
    }

    close() {
        if (this._closed)
            return;
        this._closed = true;

        if (this._grab) {
            try {
                Main.popModal(this._grab);
            } catch (error) {
                logError(error, 'desktop-forge: could not release the edit grab');
            }
            this._grab = null;
        }

        for (const control of this._controls)
            control.destroy();
        this._controls = [];
        this._controlSets.clear();
        this._activeWidget = null;
        this._liveGeometry = null;

        // The cards must outlive the overlay: remove them before destroying it
        // and let the extension return each one to its normal desktop layer.
        for (const {widget} of this._entries) {
            widget.disconnectObject(this);
            widget.setBlurEnabled(true);
            this._backdrop.remove_child(widget);
        }

        Main.layoutManager.removeChrome(this._backdrop);
        this._backdrop.destroy();
        this._backdrop = null;
        this._onDone();
    }

    // -- move --------------------------------------------------------------

    _addMoveSurface(widget, entry) {
        const surface = new St.Widget({
            style_class: 'df-move-surface',
            reactive: true,
            track_hover: true,
        });
        this._backdrop.add_child(surface);
        this._controls.push(surface);
        this._controlSets.set(widget, {surface, grips: []});
        surface.connect('notify::hover', () => {
            if (surface.hover)
                this._activateWidget(widget);
        });
        surface.connect('button-press-event', () => {
            this._activateWidget(widget);
            return Clutter.EVENT_PROPAGATE;
        });

        const place = () => {
            const [x, y] = widget.get_position();
            const [width, height] = widget.get_size();
            surface.set_position(x, y);
            surface.set_size(width, height);
        };
        place();
        widget.connectObject('notify::allocation', place, this);

        if (Clutter.PanGesture) {
            const pan = new Clutter.PanGesture();
            pan.connect('pan-update', gesture => {
                const delta = gesture.get_delta_abs();
                this._move(widget, entry, delta.get_x(), delta.get_y());
            });
            pan.connect('end', () => this._settleMove(widget, entry));
            surface.add_action(pan);
            return;
        }

        // Compatibility with the Shell versions before PanGesture.
        const drag = new Clutter.DragAction();
        drag.connect('drag-progress', (_action, _actor, dx, dy) => {
            this._move(widget, entry, dx, dy);
            return false;
        });
        drag.connect('drag-end', () => this._settleMove(widget, entry));
        surface.add_action(drag);
    }

    _move(widget, entry, dx, dy) {
        const bounds = workAreaForEntry(entry);
        if (!bounds)
            return;
        const live = this._geometryFor(widget, 'move');
        live.x += dx;
        live.y += dy;
        [live.x, live.y] = clampPosition(
            bounds, live.x, live.y, live.width, live.height);
        const [sx, sy] = settlePosition(
            bounds, live.x, live.y, live.width, live.height,
            this._peerRects(widget, entry));
        widget.set_position(sx, sy);
    }

    _settleMove(widget, entry) {
        const bounds = workAreaForEntry(entry);
        if (!bounds)
            return;
        const live = this._geometryFor(widget, 'move');
        const {x, y, width, height} = live;
        const [sx, sy] = settlePosition(
            bounds, x, y, width, height, this._peerRects(widget, entry));
        this._liveGeometry = null;
        widget.set_position(sx, sy);
        this._onGeometry(entry, sx, sy, width, height);
    }

    // -- resize ------------------------------------------------------------

    _addGrip(widget, entry, direction) {
        const grip = new St.Widget({
            style_class: `df-grip df-grip-${direction.name}`,
            reactive: true,
            track_hover: true,
        });
        grip.set_size(GRIP_SIZE, GRIP_SIZE);
        grip.hide();
        this._backdrop.add_child(grip);
        this._controls.push(grip);
        this._controlSets.get(widget)?.grips.push(grip);

        const place = () => {
            const [x, y] = widget.get_position();
            const [width, height] = widget.get_size();
            const gx = direction.horizontal < 0
                ? x - GRIP_SIZE / 2
                : direction.horizontal > 0
                    ? x + width - GRIP_SIZE / 2
                    : x + (width - GRIP_SIZE) / 2;
            const gy = direction.vertical < 0
                ? y - GRIP_SIZE / 2
                : direction.vertical > 0
                    ? y + height - GRIP_SIZE / 2
                    : y + (height - GRIP_SIZE) / 2;
            grip.set_position(Math.round(gx), Math.round(gy));
        };
        place();
        widget.connectObject('notify::allocation', place, this);

        if (Clutter.PanGesture) {
            const pan = new Clutter.PanGesture();
            pan.connect('pan-update', gesture => {
                const delta = gesture.get_delta_abs();
                this._resize(widget, entry, direction,
                    delta.get_x(), delta.get_y());
            });
            pan.connect('end', () => this._settleResize(widget, entry, direction));
            grip.add_action(pan);
            return;
        }

        const drag = new Clutter.DragAction();
        drag.connect('drag-progress', (_action, _actor, dx, dy) => {
            this._resize(widget, entry, direction, dx, dy);
            return false;
        });
        drag.connect('drag-end', () => this._settleResize(widget, entry, direction));
        grip.add_action(drag);
    }

    _activateWidget(widget) {
        if (!widget || this._activeWidget === widget)
            return;
        this._activeWidget = widget;
        for (const [candidate, controls] of this._controlSets) {
            const active = candidate === widget;
            if (active)
                controls.surface.add_style_class_name('df-move-surface-active');
            else
                controls.surface.remove_style_class_name('df-move-surface-active');
            for (const grip of controls.grips)
                grip.visible = active;
        }
    }

    /** Live feedback uses the same grid and magnetic targets as final placement. */
    _resize(widget, entry, direction, dx, dy) {
        const bounds = workAreaForEntry(entry);
        if (!bounds)
            return;
        const key = `resize-${direction.horizontal}-${direction.vertical}`;
        const live = this._geometryFor(widget, key);
        let left = live.x;
        let right = live.x + live.width;
        let top = live.y;
        let bottom = live.y + live.height;

        if (direction.horizontal < 0)
            left = Math.max(bounds.x, Math.min(left + dx, right - MIN_WIDTH));
        else if (direction.horizontal > 0)
            right = Math.max(left + MIN_WIDTH,
                Math.min(right + dx, bounds.x + bounds.width));

        if (direction.vertical < 0)
            top = Math.max(bounds.y, Math.min(top + dy, bottom - MIN_HEIGHT));
        else if (direction.vertical > 0)
            bottom = Math.max(top + MIN_HEIGHT,
                Math.min(bottom + dy, bounds.y + bounds.height));

        live.x = Math.round(left);
        live.y = Math.round(top);
        live.width = Math.round(right - left);
        live.height = Math.round(bottom - top);

        const [sx, sy, width, height] = settleResize(
            bounds, live.x, live.y, live.width, live.height,
            direction.horizontal, direction.vertical,
            this._peerRects(widget, entry));
        widget.set_position(sx, sy);
        widget.set_size(width, height);
    }

    _settleResize(widget, entry, direction) {
        const bounds = workAreaForEntry(entry);
        if (!bounds)
            return;
        const key = `resize-${direction.horizontal}-${direction.vertical}`;
        const live = this._geometryFor(widget, key);
        const [sx, sy, w, h] = settleResize(
            bounds, live.x, live.y, live.width, live.height,
            direction.horizontal, direction.vertical,
            this._peerRects(widget, entry));
        this._liveGeometry = null;
        widget.set_position(sx, sy);
        widget.set_size(w, h);
        this._onGeometry(entry, sx, sy, w, h);
    }

    /** Raw gesture geometry stays separate from its snapped visual preview. */
    _geometryFor(widget, kind) {
        if (this._liveGeometry?.widget === widget &&
            this._liveGeometry.kind === kind)
            return this._liveGeometry;

        const [x, y] = widget.get_position();
        const [width, height] = widget.get_size();
        this._liveGeometry = {widget, kind, x, y, width, height};
        return this._liveGeometry;
    }

    /** Only cards on the same monitor should act as magnetic anchors. */
    _peerRects(widget, entry) {
        const monitor = monitorForEntry(entry);
        const peers = [];
        for (const other of this._entries) {
            if (other.widget === widget || monitorForEntry(other.entry) !== monitor)
                continue;
            const [x, y] = other.widget.get_position();
            const [width, height] = other.widget.get_size();
            peers.push({x, y, width, height});
        }
        return peers;
    }

    // -- chrome ------------------------------------------------------------

    _addHintBar() {
        const bar = new St.BoxLayout({style_class: 'df-edit-hint'});
        bar.add_child(new St.Label({
            text: 'Move or resize a widget · nearby cards snap into alignment',
            y_align: Clutter.ActorAlign.CENTER,
        }));

        const done = new St.Button({
            style_class: 'df-edit-done',
            label: 'Done',
            can_focus: true,
        });
        done.connect('clicked', () => this.close());
        bar.add_child(done);
        this._backdrop.add_child(bar);

        const monitor = Main.layoutManager.primaryMonitor;
        const workspace = global.workspace_manager.get_active_workspace();
        const bounds = workspace?.get_work_area_for_monitor(monitor.index) ?? monitor;
        const [, , width, height] = bar.get_preferred_size();
        bar.set_position(
            Math.round(bounds.x + (bounds.width - width) / 2),
            Math.round(bounds.y + bounds.height - height - 24));
    }
}
