import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Graphene from 'gi://Graphene';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const SIGNALS = ['map', 'destroy', 'minimize', 'unminimize'];
const ANIMATED_TYPES = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG];
const WORKSPACE_FACTORS = {default: 1, fast: 0.5, slow: 2, instant: 0.01};
const MODES = {
    fade: Clutter.AnimationMode.EASE_OUT_QUAD,
    scale: Clutter.AnimationMode.EASE_OUT_CUBIC,
    slide: Clutter.AnimationMode.EASE_OUT_CUBIC,
    pop: Clutter.AnimationMode.EASE_OUT_BACK,
};

/** Where a style starts an appearing window (or ends a disappearing one). */
function hiddenState(style, actor) {
    switch (style) {
    case 'scale':
        return {opacity: 0, scale_x: 0.88, scale_y: 0.88};
    case 'slide':
        return {opacity: 0, translation_y: Math.min(80, actor.height * 0.08)};
    case 'pop':
        return {opacity: 0, scale_x: 0.6, scale_y: 0.6};
    default:
        return {opacity: 0};
    }
}

/**
 * Window open, close and minimize animations, overall speed, and the speed
 * of switching workspaces.
 *
 * Shell connects its animation handlers with bind() when it starts, so its
 * methods cannot be replaced afterwards. Instead, while a style other than
 * "GNOME default" is chosen, Shell's four handlers are blocked and these run
 * in their place. They still call Shell's own method -- which does the
 * bookkeeping for dialogs, dimming and transients -- after skipNextEffect(),
 * so Shell completes the operation at once without animating; the animation
 * then plays on the window (appearing) or on a snapshot of it (closing and
 * minimizing, when the real window is already gone or hidden).
 */
export class WindowAnimations {
    constructor() {
        this._wm = Main.wm;
        this._shellwm = global.window_manager;
        this._settings = St.Settings.get();
        this._originalFactor = this._settings.slow_down_factor;
        this._values = {};
        this._blocked = [];
        this._handlers = [];
        this._running = new Set();
        this._workspaceFactor = 1;
        this._injections = new InjectionManager();
        const self = this;
        const workspaceAnimation = this._wm._workspaceAnimation;
        if (workspaceAnimation) {
            this._injections.overrideMethod(workspaceAnimation, 'animateSwitch', original =>
                function (...args) {
                    // Shell eases the switch synchronously inside this call,
                    // so a factor set around it applies to this switch only.
                    const factor = self._settings.slow_down_factor;
                    self._settings.slow_down_factor = factor * self._workspaceFactor;
                    try {
                        return original.apply(this, args);
                    } finally {
                        self._settings.slow_down_factor = factor;
                    }
                });
        }
    }

    update({animations = {}}) {
        this._values = animations;
        const speed = Number.isFinite(animations.speed) ? Math.max(0.25, Math.min(3, animations.speed)) : 1;
        this._settings.slow_down_factor = this._originalFactor / speed;
        this._workspaceFactor = WORKSPACE_FACTORS[animations.workspace] ?? 1;
        const custom = ['window_open', 'window_close', 'minimize']
            .some(key => (animations[key] ?? 'gnome') !== 'gnome');
        if (custom)
            this._takeOver();
        else
            this._handBack();
    }

    get takenOver() {
        return this._blocked.length > 0;
    }

    _takeOver() {
        if (this._blocked.length)
            return;
        const handlers = {
            map: (wm, actor) => this._map(wm, actor),
            destroy: (wm, actor) => this._destroy(wm, actor),
            minimize: (wm, actor) => this._minimize(wm, actor),
            unminimize: (wm, actor) => this._unminimize(wm, actor),
        };
        const found = SIGNALS.map(signal => GObject.signal_handler_find(this._shellwm, {signalId: signal}));
        if (found.some(id => !id))
            throw new Error("Shell's window animation handlers were not found");
        for (const id of found) {
            GObject.signal_handler_block(this._shellwm, id);
            this._blocked.push(id);
        }
        for (const signal of SIGNALS)
            this._handlers.push(this._shellwm.connect(signal, handlers[signal]));
    }

    _handBack() {
        for (const id of this._handlers)
            this._shellwm.disconnect(id);
        this._handlers = [];
        for (const id of this._blocked)
            GObject.signal_handler_unblock(this._shellwm, id);
        this._blocked = [];
    }

    _style(key) {
        return this._values[key] ?? 'gnome';
    }

    get _duration() {
        const value = this._values.duration;
        return Number.isFinite(value) ? Math.max(50, Math.min(1000, value)) : 250;
    }

    /** Whether Shell itself would animate this window now. */
    _animatable(actor) {
        try {
            if (!this._wm._shouldAnimate() || !actor.get_texture())
                return false;
            return ANIMATED_TYPES.includes(this._wm._getAnimationWindowType(actor));
        } catch {
            return false;
        }
    }

    _map(shellwm, actor) {
        const style = this._style('window_open');
        const animate = style !== 'gnome' && this._animatable(actor);
        if (animate)
            this._wm.skipNextEffect(actor);
        this._wm._mapWindow(shellwm, actor)?.catch?.(logError);
        if (animate && style !== 'none')
            this._appear(actor, style);
    }

    _unminimize(shellwm, actor) {
        const style = this._style('minimize');
        const animate = style !== 'gnome' && this._animatable(actor);
        if (animate)
            this._wm.skipNextEffect(actor);
        this._wm._unminimizeWindow(shellwm, actor);
        if (animate && style !== 'none')
            this._appear(actor, style);
    }

    _destroy(shellwm, actor) {
        const style = this._style('window_close');
        const animate = style !== 'gnome' && this._animatable(actor);
        const ghost = animate && style !== 'none' ? this._snapshot(actor) : null;
        if (animate)
            this._wm.skipNextEffect(actor);
        this._wm._destroyWindow(shellwm, actor);
        if (ghost)
            this._disappear(ghost, style);
    }

    _minimize(shellwm, actor) {
        const style = this._style('minimize');
        const animate = style !== 'gnome' && this._animatable(actor);
        const ghost = animate && style !== 'none' ? this._snapshot(actor) : null;
        if (animate)
            this._wm.skipNextEffect(actor);
        this._wm._minimizeWindow(shellwm, actor);
        if (!ghost)
            return;
        if (style === 'scale') {
            // Shrink toward the app's dock icon, as GNOME does.
            const [ok, icon] = actor.meta_window.get_icon_geometry();
            if (ok) {
                ghost.set_pivot_point(0, 0);
                this._ease(ghost, {
                    opacity: 0, x: icon.x, y: icon.y,
                    scale_x: icon.width / Math.max(1, ghost.width),
                    scale_y: icon.height / Math.max(1, ghost.height),
                }, MODES.scale, () => ghost.destroy());
                return;
            }
        }
        this._disappear(ghost, style);
    }

    /** Animate a real window in from its style's hidden state. */
    _appear(actor, style) {
        let gone = false;
        const destroyId = actor.connect('destroy', () => {
            gone = true;
        });
        actor.set_pivot_point(0.5, 0.5);
        const hidden = hiddenState(style, actor);
        for (const [key, value] of Object.entries(hidden))
            actor[key] = value;
        this._ease(actor, {opacity: 255, scale_x: 1, scale_y: 1, translation_y: 0}, MODES[style] ?? MODES.fade,
            () => {
                if (gone)
                    return;
                actor.disconnect(destroyId);
                actor.set_pivot_point(0, 0);
                actor.opacity = 255;
                actor.set_scale(1, 1);
                actor.translation_y = 0;
            });
    }

    _disappear(ghost, style) {
        ghost.set_pivot_point(0.5, 0.5);
        const target = hiddenState(style === 'pop' ? 'scale' : style, ghost);
        this._ease(ghost, target, Clutter.AnimationMode.EASE_IN_QUAD, () => ghost.destroy());
    }

    _ease(actor, target, mode, done) {
        this._running.add(actor);
        actor.ease({
            ...target,
            duration: this._duration,
            mode,
            onStopped: () => {
                this._running.delete(actor);
                done();
            },
        });
    }

    /** A picture of the window, left in its place in the stacking order. */
    _snapshot(actor) {
        let content = null;
        try {
            content = actor.paint_to_content(null);
        } catch {
            return null;
        }
        if (!content)
            return null;
        const ghost = new Clutter.Actor({
            content, reactive: false,
            x: actor.x, y: actor.y, width: actor.width, height: actor.height,
            opacity: actor.opacity,
            pivot_point: new Graphene.Point({x: 0.5, y: 0.5}),
        });
        const parent = actor.get_parent() ?? global.window_group;
        if (actor.get_parent())
            parent.insert_child_above(ghost, actor);
        else
            parent.add_child(ghost);
        return ghost;
    }

    destroy() {
        this._injections.clear();
        this._handBack();
        // Finish anything still playing: ghosts are destroyed and real
        // windows reset by their onStopped handlers.
        for (const actor of [...this._running])
            actor.remove_all_transitions();
        this._running.clear();
        this._settings.slow_down_factor = this._originalFactor;
    }
}
