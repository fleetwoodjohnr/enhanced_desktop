import Cogl from 'gi://Cogl';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import {actionsFor} from '../rulesLogic.js';
import {describeWindow, isAppWindow, maximizeFlags} from '../windowInfo.js';

const EFFECT = 'desktop-forge-window-style';
const ACCENTS = {
    blue: '#3584e4', teal: '#2190a4', green: '#3a944a', yellow: '#c88800', orange: '#ed5b00',
    red: '#e62d42', pink: '#d56199', purple: '#9141ac', slate: '#6f8396',
};

function rgba(hex, alpha = 1) {
    const match = /^#([0-9a-f]{6})$/i.exec(hex ?? '');
    const value = match ? parseInt(match[1], 16) : 0x3584e4;
    return [(value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255, alpha];
}

function number(value, low, high, fallback) {
    return Number.isFinite(value) ? Math.max(low, Math.min(high, value)) : fallback;
}

/**
 * Rounded corners, a border, dimming and opacity for one window, in one pass.
 *
 * Coordinates are the actor's own: `size` is the actor (the client's whole
 * buffer, including any shadow it draws) and `bounds` the window frame inside
 * it. The frame's corners are cut away along with the client-drawn shadow
 * just beyond them, which is square; its shadow along the edges stays.
 */
const WindowStyleEffect = GObject.registerClass(
class WindowStyleEffect extends Shell.GLSLEffect {
    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT,
            'uniform vec2 df_size; uniform vec4 df_bounds; uniform float df_radius;' +
            ' uniform float df_border; uniform vec4 df_border_color; uniform float df_dim;' +
            ' uniform float df_alpha; uniform vec4 df_border_color2; uniform float df_gradient;' +
            ' uniform float df_angle;', `
            vec2 p = cogl_tex_coord_in[0].xy * df_size;
            vec2 centre = (df_bounds.xy + df_bounds.zw) * 0.5;
            vec2 half_size = (df_bounds.zw - df_bounds.xy) * 0.5;
            vec2 square = abs(p - centre) - half_size;
            float inside = 1.0 - step(0.0, max(square.x, square.y));
            vec2 q = square + vec2(df_radius);
            float d = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - df_radius;
            vec4 colour = cogl_color_out;
            colour.rgb *= 1.0 - df_dim;
            if (df_border > 0.0) {
                float edge = smoothstep(-df_border - 0.5, -df_border + 0.5, d)
                    * (1.0 - smoothstep(-0.5, 0.5, d)) * inside;
                // A gradient runs across the window at df_angle.
                vec2 along = vec2(cos(df_angle), sin(df_angle));
                float t = clamp(dot(p - centre, along) / max(length(half_size), 1.0) * 0.5 + 0.5, 0.0, 1.0);
                vec4 border = mix(df_border_color, df_border_color2, t * df_gradient);
                colour = mix(colour, vec4(border.rgb * border.a, border.a), edge);
            }
            // Clear the whole corner quadrant outside the rounded shape: the
            // window's square corner and the client's square-cornered
            // shadow beyond it. Shadows along the edges are left alone.
            if (df_radius > 0.0) {
                float corner = step(0.0, q.x) * step(0.0, q.y);
                colour *= 1.0 - corner * smoothstep(-0.5, 0.5, d);
            }
            cogl_color_out = colour * df_alpha;
            `, false);
    }

    setStyle(style) {
        const set = (name, values) =>
            this.set_uniform_float(this.get_uniform_location(name), values.length, values);
        set('df_size', style.size);
        set('df_bounds', style.bounds);
        set('df_radius', [style.radius]);
        set('df_border', [style.border]);
        set('df_border_color', style.color);
        set('df_dim', [style.dim]);
        set('df_alpha', [style.alpha]);
        set('df_border_color2', style.color2 ?? style.color);
        set('df_gradient', [style.gradient ? 1 : 0]);
        this.setAngle(style.angle ?? 0);
    }

    setAngle(angle) {
        this.set_uniform_float(this.get_uniform_location('df_angle'), 1, [angle]);
        this.queue_repaint();
    }
});

const SPIN_MS = 50;
const SPIN_TURN_MS = 6000;

/**
 * Window transparency, dimming, corners and focus borders.
 *
 * An effect is attached only to windows that need one and removed as soon
 * as they do not, so a default setup costs nothing. Fullscreen windows are
 * never touched (the compositor can then show games and video directly), nor
 * are excluded apps or windows a rule marks "no effects". Corners and borders
 * are drawn on Wayland windows only: for X11 windows the picture includes
 * Mutter's own shadow and the frame cannot be placed reliably.
 *
 * The focused window's border can be a two-colour gradient, which can turn
 * slowly; only that one window is redrawn for it.
 */
export class WindowEffects {
    constructor() {
        this._values = {};
        this._rules = [];
        this._tracked = new Map();
        this._moving = null;
        this._focused = null;
        this._pending = new Map();  // window -> retries left
        this._angle = 0;
        this._spinId = 0;
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._interface.connectObject('changed::accent-color', () => this._restyleAll(), this);
        global.display.connectObject(
            'window-created', (_display, window) => this._track(window),
            'notify::focus-window', () => this._focusChanged(),
            'grab-op-begin', (_display, window, op) => this._grab(window, op),
            'grab-op-end', () => this._grab(null, null), this);
        this._focused = global.display.focus_window;
        for (const actor of global.get_window_actors())
            this._track(actor.meta_window);
    }

    update({windows = {}, rules = {}}) {
        this._values = windows;
        this._rules = Array.isArray(rules.list) ? rules.list : [];
        this._restyleAll();
        this._syncSpin();
    }

    /** Only the windows that gained or lost focus change. */
    _focusChanged() {
        const previous = this._focused;
        this._focused = global.display.focus_window;
        for (const window of new Set([previous, this._focused])) {
            if (window)
                this._restyle(window);
        }
        this._syncSpin();
    }

    /** Turn the focused window's gradient border, when asked to. */
    _syncSpin() {
        const v = this._values;
        const wanted = !this._destroyed && v.border_rotate === true && v.border_gradient === true &&
            number(v.border_width, 0, 8, 0) > 0 && !!this._tracked.get(this._focused)?.effect;
        if (wanted === !!this._spinId)
            return;
        if (!wanted) {
            GLib.source_remove(this._spinId);
            this._spinId = 0;
            return;
        }
        this._spinId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, SPIN_MS, () => {
            this._angle = (this._angle + 2 * Math.PI * SPIN_MS / SPIN_TURN_MS) % (2 * Math.PI);
            this._tracked.get(this._focused)?.effect?.setAngle(this._angle);
            return GLib.SOURCE_CONTINUE;
        });
    }

    /** Windows that currently carry the effect, for the smoke test. */
    get styled() {
        return [...this._tracked.values()].filter(t => t.effect).map(t => t.window);
    }

    _accent() {
        const name = this._interface.get_string('accent-color');
        return ACCENTS[name] ?? ACCENTS.blue;
    }

    _grab(window, op) {
        const moving = window && [Meta.GrabOp.MOVING, Meta.GrabOp.MOVING_UNCONSTRAINED,
            Meta.GrabOp.KEYBOARD_MOVING].includes(op) ? window : null;
        const previous = this._moving;
        this._moving = moving;
        for (const changed of [previous, moving]) {
            if (changed)
                this._restyle(changed);
        }
    }

    _track(window) {
        if (!window || this._tracked.has(window) || !isAppWindow(window))
            return;
        const actor = window.get_compositor_private();
        if (!actor) {
            // Not composited yet: try again when the main loop is idle, a
            // few times (a window closed this early never will be).
            if (this._pending.has(window))
                return;
            const left = this._retries?.get(window) ?? 10;
            if (left <= 0)
                return;
            this._pending.set(window, GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._pending.delete(window);
                (this._retries ??= new WeakMap()).set(window, left - 1);
                this._track(window);
                return GLib.SOURCE_REMOVE;
            }));
            return;
        }
        const tracked = {window, actor, effect: null};
        this._tracked.set(window, tracked);
        window.connectObject(
            'size-changed', () => this._restyle(window),
            'notify::fullscreen', () => this._restyle(window),
            'notify::maximized-horizontally', () => this._restyle(window),
            'notify::maximized-vertically', () => this._restyle(window),
            'notify::title', () => this._restyle(window),
            // The close animation runs after this: keep its corners.
            'unmanaged', () => this._untrack(window, false), this);
        actor.connectObject('destroy', () => this._untrack(window, false), this);
        this._restyle(window);
    }

    _untrack(window, detach = true) {
        const tracked = this._tracked.get(window);
        if (!tracked)
            return;
        this._tracked.delete(window);
        window.disconnectObject(this);
        tracked.actor.disconnectObject(this);
        if (detach)
            this._detach(tracked);
    }

    _restyleAll() {
        for (const window of this._tracked.keys())
            this._restyle(window);
    }

    _style(window, actor) {
        const v = this._values;
        // Minimized windows keep their style: hidden actors are not painted,
        // and the minimize animation keeps its corners.
        if (window.is_fullscreen())
            return null;
        const record = describeWindow(window);
        if ((v.exclude ?? []).includes(record.desktop_id))
            return null;
        const rule = actionsFor(this._rules, record);
        if (rule.no_effects)
            return null;
        const focused = global.display.focus_window === window;
        let alpha = this._moving === window
            ? number(v.moving_opacity, 0.3, 1, 1)
            : focused ? number(v.active_opacity, 0.5, 1, 1) : number(v.inactive_opacity, 0.3, 1, 1);
        if (rule.opacity < 1)
            alpha = Math.min(alpha, number(rule.opacity, 0.2, 1, 1));
        const wayland = window.get_client_type() === Meta.WindowClientType.WAYLAND;
        const square = maximizeFlags(window) === Meta.MaximizeFlags.BOTH;
        const radius = wayland && !square ? number(v.corner_radius, 0, 32, 0) : 0;
        const border = wayland
            ? focused ? number(v.border_width, 0, 8, 0) : number(v.inactive_border_width, 0, 4, 0) : 0;
        const dim = focused ? 0 : number(v.dim_inactive, 0, 0.6, 0);
        if (!(radius > 0 || border > 0 || dim > 0 || alpha < 1))
            return null;
        const frame = window.get_frame_rect();
        const buffer = window.get_buffer_rect();
        const left = frame.x - buffer.x;
        const top = frame.y - buffer.y;
        const colour = focused
            ? (v.border_accent !== false ? this._accent() : v.border_color)
            : v.inactive_border_color;
        // The buffer rect, not the actor: at size-changed the actor may not
        // have been given its new size yet.
        const size = wayland ? [buffer.width, buffer.height] : [actor.width, actor.height];
        const gradient = focused && v.border_gradient === true;
        return {
            size: size.map(value => Math.max(1, value)),
            bounds: [left, top, left + frame.width, top + frame.height],
            radius, border, dim, alpha, color: rgba(colour),
            gradient, color2: rgba(gradient ? v.border_color2 : colour), angle: this._angle,
        };
    }

    _restyle(window) {
        const tracked = this._tracked.get(window);
        if (!tracked)
            return;
        let style = null;
        try {
            style = this._style(window, tracked.actor);
        } catch (error) {
            logError(error, 'desktop-forge: could not style a window');
        }
        if (!style) {
            this._detach(tracked);
            return;
        }
        if (!tracked.effect) {
            tracked.effect = new WindowStyleEffect();
            tracked.actor.add_effect_with_name(EFFECT, tracked.effect);
        }
        tracked.effect.setStyle(style);
        if (window === this._focused)
            this._syncSpin();
    }

    _detach(tracked) {
        if (tracked.effect) {
            tracked.actor.remove_effect(tracked.effect);
            tracked.effect = null;
        }
    }

    destroy() {
        this._destroyed = true;
        global.display.disconnectObject(this);
        this._interface.disconnectObject(this);
        this._syncSpin();
        for (const window of [...this._tracked.keys()])
            this._untrack(window);
        for (const id of this._pending.values())
            GLib.source_remove(id);
        this._pending.clear();
    }
}
