import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {DesktopWidget} from './base.js';
import {ROTATION_SECONDS} from './pager.js';
import {styleScrollbar} from './scrollbar.js';

/** A fixed header and a native, naturally sized feed shared by News/Markets. */
export const ScrollableWidget = GObject.registerClass(
class ScrollableWidget extends DesktopWidget {
    get interactive() { return true; }
    get tracksHover() { return true; }

    initFeed(body, title, icon, listClass) {
        this._feedEntries = [];
        this._rotationId = 0;
        this._viewportId = 0;
        this._settleId = 0;
        this._pendingData = null;
        this._pendingAnchor = null;
        this._destroying = false;
        body.y_expand = true;
        const {header} = this.addHeader(body, title, icon);
        this._range = new St.Label({
            style_class: 'df-header-meta', y_align: Clutter.ActorAlign.CENTER,
            style: `color: ${this.muted}`,
        });
        header.add_child(this._range);
        this._scroll = new St.ScrollView({
            style_class: 'df-subtle-scroll',
            overlay_scrollbars: true,
            x_expand: true, y_expand: true, reactive: true, can_focus: true,
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        this._rows = new St.BoxLayout({
            style_class: listClass, vertical: true, x_expand: true,
        });
        this._scroll.set_child(this._rows);
        body.add_child(this._scroll);
        styleScrollbar(this, this._scroll);
        this._adjustment = this._scroll.get_vadjustment();
        this._adjustment.connectObject('notify::value', () => {
            this._updateRange();
            this._syncHoveredRows(false);
            if (this._settleId)
                GLib.source_remove(this._settleId);
            this._settleId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
                this._settleId = 0;
                this._syncHoveredRows();
                return GLib.SOURCE_REMOVE;
            });
        }, 'notify::upper', () => this._queueViewportUpdate(),
        'notify::page-size', () => this._queueViewportUpdate(), this);
        this._rows.connect('notify::allocation', () => this._queueViewportUpdate());
        this._scroll.connect('notify::allocation', () => this._queueViewportUpdate());
        this.connect('notify::hover', () => this._interactionChanged());
        this.connect('notify::mapped', () => {
            if (!this.mapped)
                this.clearDesktopInteraction();
            this._interactionChanged();
        });
        global.stage.connectObject('notify::key-focus', () => {
            const focus = global.stage.get_key_focus();
            const entry = this._feedEntries.find(e => e.row === focus || (focus && e.row.contains(focus)));
            if (entry)
                this._adjustment.clamp_page(entry.row.y, entry.row.y + entry.row.height);
            this._interactionChanged();
        }, this);
        // Native content handles its own events; this catches the fixed header
        // and footer too. Consuming at the bounds avoids desktop workspace scroll.
        this.connect('scroll-event', (_actor, event) => {
            const direction = event.get_scroll_direction();
            let delta = 0;
            if (direction === Clutter.ScrollDirection.UP)
                delta = -1;
            else if (direction === Clutter.ScrollDirection.DOWN)
                delta = 1;
            else if (direction === Clutter.ScrollDirection.SMOOTH)
                [, delta] = event.get_scroll_delta();
            else
                return Clutter.EVENT_PROPAGATE;
            this._adjustment.adjust_for_scroll_event(delta);
            return Clutter.EVENT_STOP;
        });
        this.connect('key-press-event', (_actor, event) => {
            const a = this._adjustment;
            switch (event.get_key_symbol()) {
            case Clutter.KEY_Page_Down: a.value += a.page_size; break;
            case Clutter.KEY_Page_Up: a.value -= a.page_size; break;
            case Clutter.KEY_Home: a.value = 0; break;
            case Clutter.KEY_End: a.value = a.upper; break;
            default: return Clutter.EVENT_PROPAGATE;
            }
            return Clutter.EVENT_STOP;
        });
        this.connect('destroy', () => {
            this._destroying = true;
            this._stopRotation();
            for (const key of ['_viewportId', '_settleId']) {
                if (this[key])
                    GLib.source_remove(this[key]);
                this[key] = 0;
            }
        });
    }

    _isReading() {
        const focus = global.stage.get_key_focus();
        return this.hover || this._scrollbarDragging || !!(focus && this.contains(focus));
    }

    renderData(payload) {
        if (JSON.stringify(payload) === this._dataSignature) {
            this._pendingData = null;
            return false;
        }
        if (this._isReading() && this._feedEntries.length) {
            this._pendingData = payload;
            return false;
        }
        this._pendingData = null;
        return super.renderData(payload);
    }

    _interactionChanged() {
        if (this._destroying)
            return;
        this._syncHoveredRows();
        if (!this._isReading() && this._pendingData) {
            const data = this._pendingData;
            this._pendingData = null;
            this.renderData(data);
        }
        this._restartRotation();
    }

    _captureAnchor() {
        if (this._pendingAnchor)
            return this._pendingAnchor;
        const value = this._adjustment.value;
        const first = this._feedEntries.find(e => e.row.y + e.row.height > value);
        return {key: first?.key, offset: first ? value - first.row.y : 0, value};
    }

    renderItems(items, keyFor, makeRow, emptyText) {
        const anchor = this._captureAnchor();
        this._feedEntries = [];
        this._rows.destroy_all_children();
        items.forEach((item, index) => {
            const row = makeRow(item, index);
            this._rows.add_child(row);
            this._feedEntries.push({key: keyFor(item), row});
        });
        if (!items.length)
            this._rows.add_child(new St.Label({style_class: 'df-empty', text: emptyText}));
        this._pendingAnchor = anchor;
        this._queueViewportUpdate();
    }

    _queueViewportUpdate() {
        if (this._destroying || this._viewportId)
            return;
        this._viewportId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._viewportId = 0;
            if (!this._rows.has_allocation() || this._feedEntries.some(e => !e.row.has_allocation()))
                return GLib.SOURCE_REMOVE;
            if (this._pendingAnchor) {
                const anchor = this._pendingAnchor;
                this._pendingAnchor = null;
                const entry = this._feedEntries.find(e => e.key === anchor.key);
                this._adjustment.value = entry ? entry.row.y + anchor.offset : anchor.value;
            }
            this._updateRange();
            this._syncHoveredRows();
            const overflow = this._adjustment.upper > this._adjustment.page_size;
            if (!overflow)
                this._stopRotation();
            else if (!this._rotationId)
                this._restartRotation();
            return GLib.SOURCE_REMOVE;
        });
    }

    _updateRange() {
        const {value, page_size: height, upper} = this._adjustment;
        const visible = this._feedEntries.map((e, i) => ({row: e.row, index: i}))
            .filter(e => e.row.y + e.row.height > value && e.row.y < value + height);
        this._range.visible = upper > height && visible.length > 0;
        if (visible.length)
            this._range.text = `${visible[0].index + 1}–${visible.at(-1).index + 1} of ${this._feedEntries.length}`;
    }

    _advanceFeed() {
        const a = this._adjustment;
        const maximum = Math.max(0, a.upper - a.page_size);
        if (a.value >= maximum - 1) {
            a.value = 0;
            return;
        }
        const complete = this._feedEntries.filter(e => e.row.y + e.row.height <= a.value + a.page_size);
        const next = this._feedEntries[complete.length];
        a.value = next && next.row.y > a.value ? next.row.y : a.value + a.page_size;
    }

    _restartRotation() {
        this._stopRotation();
        if (this._destroying || !this.mapped || this._isReading() ||
            this._adjustment.upper <= this._adjustment.page_size)
            return;
        this._rotationId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, ROTATION_SECONDS, () => {
            this._advanceFeed();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopRotation() {
        if (this._rotationId)
            GLib.source_remove(this._rotationId);
        this._rotationId = 0;
    }

    _syncHoveredRows(_enabled = true) {}

    clearDesktopInteraction() {
        this._syncHoveredRows(false);
        super.clearDesktopInteraction();
    }
});
