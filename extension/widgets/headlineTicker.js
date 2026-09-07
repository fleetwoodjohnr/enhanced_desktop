import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {headlinePanDistance, headlineTickerDuration} from '../widgetLogic.js';

const PAN_DELAY_MS = 250;

/** A bounded viewport whose children always receive their full text width. */
export const HeadlineTicker = GObject.registerClass(
class HeadlineTicker extends St.Widget {
    _init(text) {
        super._init({
            style_class: 'df-news-headline-viewport',
            x_expand: true,
            x_align: Clutter.ActorAlign.FILL,
            clip_to_allocation: true,
        });
        this._delayId = 0;
        this._timeline = null;
        this._hovered = false;
        this._layoutKey = null;
        this._cycle = 0;
        this._track = new St.Widget({
            style_class: 'df-news-ticker',
        });
        this._label = this._makeLabel(text);
        this._separator = this._makeLabel('·');
        this._tail = this._makeLabel(text);
        this._separator.opacity = 0;
        this._tail.opacity = 0;
        this.add_child(this._track);
        this.connect('notify::mapped', () => {
            if (!this.mapped)
                this.setHovered(false);
        });
        this.connect('destroy', () => this._stop());
    }

    _makeLabel(text) {
        const label = new St.Label({style_class: 'df-news-headline', text});
        label.clutter_text.set({
            ellipsize: Pango.EllipsizeMode.NONE,
            single_line_mode: true,
        });
        this._track.add_child(label);
        return label;
    }

    vfunc_get_preferred_width(_forHeight) {
        return [0, 0];
    }

    vfunc_get_preferred_height(_forWidth) {
        const [min, natural] = this._label.get_preferred_height(-1);
        return this.get_theme_node().adjust_preferred_height(min, natural);
    }

    vfunc_allocate(box) {
        this.set_allocation(box);
        const content = this.get_theme_node().get_content_box(box);
        const width = content.get_width();
        const height = content.get_height();
        const labelWidth = Math.ceil(this._label.get_preferred_width(-1)[1]);
        const separatorWidth = Math.ceil(this._separator.get_preferred_width(-1)[1]);
        const gap = this._track.get_theme_node().get_length('spacing');
        const cycle = labelWidth + gap * 2 + separatorWidth;
        const key = `${width}:${height}:${labelWidth}:${separatorWidth}:${gap}`;

        // Opacity and translation never change size requests. Reallocating an
        // unchanged row (e.g. after another widget updates) leaves its timeline
        // alone, including the initial hover delay.
        if (key !== this._layoutKey) {
            this._stop();
            this._layoutKey = key;
            this._cycle = headlinePanDistance(width, labelWidth) > 0 ? cycle : 0;
            if (this._hovered)
                this._queueTicker();
        }
        const trackBox = new Clutter.ActorBox({
            x1: content.x1, y1: content.y1,
            x2: content.x1 + cycle + labelWidth, y2: content.y2,
        });
        this._track.allocate(trackBox);
        // Allocate, rather than set_width(): an explicit width request would
        // hide changes in the text's natural width after a font/scale change.
        for (const [label, x, w] of [
            [this._label, 0, labelWidth],
            [this._separator, labelWidth + gap, separatorWidth],
            [this._tail, cycle, labelWidth],
        ]) {
            label.allocate(new Clutter.ActorBox({x1: x, y1: 0, x2: x + w, y2: height}));
        }
    }

    setHovered(hovered) {
        const active = !!hovered;
        if (active === this._hovered)
            return;
        this._hovered = active;
        if (active)
            this._queueTicker();
        else
            this._stop();
    }

    _queueTicker() {
        this._stop();
        if (!this._cycle || !this.mapped)
            return;
        this._separator.opacity = 255;
        this._tail.opacity = 255;
        this._delayId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, PAN_DELAY_MS, () => {
            this._delayId = 0;
            if (!this._hovered || !this.mapped)
                return GLib.SOURCE_REMOVE;
            // A repeating timeline also works with Shell animations disabled;
            // recursive ease(onComplete) can recurse synchronously in that case.
            this._timeline = new Clutter.Timeline({
                actor: this,
                duration: headlineTickerDuration(this._cycle),
                repeat_count: -1,
            });
            this._timeline.connect('new-frame', timeline => {
                this._track.translation_x = -this._cycle * timeline.get_progress();
            });
            this._timeline.start();
            return GLib.SOURCE_REMOVE;
        });
    }

    _stop() {
        if (this._delayId)
            GLib.source_remove(this._delayId);
        this._delayId = 0;
        this._timeline?.stop();
        this._timeline = null;
        this._track.translation_x = 0;
        this._separator.opacity = 0;
        this._tail.opacity = 0;
    }
});
