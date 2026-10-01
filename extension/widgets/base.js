import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {meterFillWidth} from '../layoutLogic.js';
import {WallpaperGlass} from '../widgetBackground.js';

/**
 * Shared behaviour for every desktop widget: the card, its colours, its
 * position on a monitor. Moving and resizing are owned by edit mode, leaving
 * pointer gestures inside interactive cards free for their controls.
 *
 * Subclasses implement buildBody() once and render(payload) on every update.
 * None of them fetch anything -- the payload always arrives from a state file
 * the daemon wrote, keeping all network and system access outside GNOME Shell.
 */
export const DesktopWidget = GObject.registerClass({
    Signals: {
        'geometry-changed': {
            param_types: [
                GObject.TYPE_INT, GObject.TYPE_INT,  // x, y
                GObject.TYPE_INT, GObject.TYPE_INT,  // width, height
            ],
        },
    },
}, class DesktopWidget extends St.Widget {
    _init(widgetConfig, style) {
        super._init({
            style_class: 'df-widget',
            layout_manager: new Clutter.BinLayout(),
            reactive: true,
            track_hover: this.tracksHover,
            can_focus: false,
            clip_to_allocation: true,
        });

        this._config = widgetConfig;
        this._style = style;
        this._dataSignature = null;
        this.add_style_class_name(style.dark ? 'df-theme-dark' : 'df-theme-light');

        // Wallpaper, tint, and foreground are separate surfaces. The wallpaper
        // blur never samples text or application pixels from the framebuffer.
        this._glass = new St.Widget({
            style_class: 'df-widget-glass',
            x_expand: true,
            y_expand: true,
        });
        this.add_child(this._glass);
        this._applyStyle();
        this._applyBlur();

        this.set_size(widgetConfig.width, widgetConfig.height);

        this._body = new St.BoxLayout({
            style_class: 'df-widget-body',
            vertical: true,
            x_expand: true,
            y_expand: true,
        });
        this.add_child(this._body);
        this.buildBody(this._body);
    }

    get tracksHover() {
        return false;
    }

    /**
     * Report where the widget ended up and how big it is.
     *
     * Resizing happens from outside -- the grips belong to the edit-mode
     * overlay, not to the card -- so this is public and both the drag gesture
     * and the grips call it.
     */
    emitGeometry() {
        const [x, y] = this.get_position();
        const [width, height] = this.get_size();
        this.emit('geometry-changed',
            Math.round(x), Math.round(y), Math.round(width), Math.round(height));
    }

    _applyStyle() {
        const s = this._style;
        const opacity = Math.max(0.1, Math.min(1, s.opacity ?? 0.58));
        const background = s.background ?? (s.dark ? '#18202c' : '#f8fbff');
        const text = s.text_color ?? (s.dark ? '#f7faff' : '#172033');
        const top = hexToRgba(background, Math.min(1, opacity + (s.dark ? 0.06 : 0.10)));
        const bottom = hexToRgba(background, Math.max(0.08, opacity - 0.06));
        const border = hexToRgba(
            s.glass_highlight ?? '#ffffff', s.dark ? 0.16 : 0.36);
        this._glass.set_style([
            `background-gradient-start: ${top}`,
            `background-gradient-end: ${bottom}`,
            'background-gradient-direction: vertical',
            `border-radius: ${s.corner_radius ?? 24}px`,
            'border-width: 1px',
            `border-color: ${border}`,
            'box-shadow: none',
        ].join('; '));
        this.set_style([
            `color: ${text}`,
            `font-size: ${Math.round(13 * (s.font_scale ?? 1))}px`,
        ].join('; '));
    }

    _applyBlur() {
        const radius = Math.max(0, Math.min(64, this._style.blur_radius ?? 24));
        if (!radius || !Shell.BlurEffect)
            return;

        try {
            this._wallpaper = new WallpaperGlass(this, this._config, this._style);
            this.insert_child_below(this._wallpaper, this._glass);
        } catch (error) {
            // Translucent gradients remain a complete visual fallback on
            // Shell versions or renderers that cannot create a blur pipeline.
            logError(error, 'desktop-forge: native glass blur unavailable');
        }
    }

    get accent() {
        return this._style.accent ?? '#0a84ff';
    }

    get muted() {
        const text = this._style.text_color ?? (this._style.dark ? '#f7faff' : '#172033');
        return hexToRgba(text, 0.68);
    }

    get surface() {
        return this._style.dark
            ? 'rgba(255,255,255,0.08)'
            : 'rgba(255,255,255,0.46)';
    }

    /** Edit mode temporarily disables blur without knowing where it lives. */
    setBlurEnabled(enabled) {
        this._wallpaper?.setBlurEnabled(enabled);
    }

    /** Invoked before a covered interactive card is moved behind a window. */
    clearDesktopInteraction() {
        const stage = this.get_stage();
        const focus = stage?.get_key_focus();
        if (focus && this.contains(focus))
            stage.set_key_focus(null);
        this.set_hover(false);
    }

    /** Subclasses build their fixed structure here. */
    buildBody(_body) {}

    /** Subclasses fill in values here. `payload` is the daemon's `data` field. */
    render(_payload) {}

    /**
     * Render only when provider data actually changed. State files also carry
     * a fresh timestamp on every poll, which must not rebuild identical rows.
     */
    renderData(payload) {
        const signature = JSON.stringify(payload);
        if (signature === this._dataSignature)
            return false;
        this.render(payload);
        this._dataSignature = signature;
        return true;
    }

    /**
     * Called with the whole state file. Handles the presentation of failure
     * in one place so no individual widget has to: stale data stays visible
     * and is labelled, a first-time failure shows the reason.
     */
    update(state) {
        if (!state) {
            this.setStatus('Waiting for desktop-forged…', false);
            return;
        }
        if (state.data)
            this.renderData(state.data);

        if (state.ok)
            this.setStatus(null, false);
        else if (state.stale)
            this.setStatus(`Last updated ${shortTime(state.updated)} — ${state.error}`, true);
        else
            this.setStatus(state.error ?? 'No data', false);
    }

    setStatus(text, stale) {
        if (!this._status) {
            this._status = new St.Label({style_class: 'df-error'});
            this._body.add_child(this._status);
        }
        this._status.visible = !!text;
        if (text) {
            this._status.text = text;
            this._status.style_class = stale ? 'df-stale' : 'df-error';
        }
    }

    addHeader(body, text, iconName = null) {
        const header = new St.BoxLayout({style_class: 'df-header', x_expand: true});
        let icon = null;
        if (iconName) {
            icon = new St.Icon({icon_name: iconName, icon_size: 14});
            const badge = new St.Bin({
                style_class: 'df-header-icon', child: icon,
                y_align: Clutter.ActorAlign.CENTER,
            });
            badge.set_style([
                `color: ${this.accent}`,
                `background-color: ${hexToRgba(this.accent, 0.16)}`,
            ].join('; '));
            header.add_child(badge);
        }

        const label = new St.Label({
            style_class: 'df-title', text, x_expand: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        label.set_style(`color: ${this.accent}`);
        header.add_child(label);
        body.add_child(header);
        return {header, title: label, icon};
    }

    addTitle(body, text, iconName = null) {
        return this.addHeader(body, text, iconName).title;
    }
});

/** A thin progress bar, used by the system monitor. */
/**
 * A progress track whose fill is sized during the track's own allocation.
 *
 * Resizing the fill from a notify::allocation handler queued a relayout in
 * the middle of layout, leaving the whole card unallocated for a frame
 * ("Can't update stage views ... needs an allocation").
 */
const MeterTrack = GObject.registerClass(
class MeterTrack extends St.Widget {
    _init(accent) {
        super._init({
            style_class: 'df-meter-track',
            x_expand: true,
            clip_to_allocation: true,
            layout_manager: new Clutter.FixedLayout(),
        });
        this._percent = 0;
        this._fill = new St.Widget({style_class: 'df-meter-fill'});
        this._fill.set_style(`background-color: ${accent}`);
        this.add_child(this._fill);
    }

    setPercent(value) {
        const percent = Math.max(0, Math.min(100, value ?? 0));
        if (percent === this._percent)
            return;
        this._percent = percent;
        this.queue_relayout();
    }

    vfunc_allocate(box) {
        this.set_allocation(box);
        const fill = new Clutter.ActorBox();
        fill.set_origin(0, 0);
        fill.set_size(meterFillWidth(box.get_width(), this._percent), box.get_height());
        this._fill.allocate(fill);
    }
});

export function meter(accent) {
    const track = new MeterTrack(accent);
    return {
        actor: track,
        set(value) {
            track.setPercent(value);
        },
    };
}

export function hexToRgba(hex, alpha) {
    const match = /^#?([0-9a-f]{6})$/i.exec(hex ?? '');
    if (!match)
        return `rgba(0,0,0,${alpha})`;
    const value = parseInt(match[1], 16);
    return `rgba(${(value >> 16) & 255},${(value >> 8) & 255},${value & 255},${alpha})`;
}

export function shortTime(iso) {
    if (!iso)
        return 'never';
    const date = new Date(iso);
    return isNaN(date) ? 'never'
        : date.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'});
}
