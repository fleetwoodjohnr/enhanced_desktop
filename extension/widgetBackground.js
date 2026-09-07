import Clutter from 'gi://Clutter';
import Cogl from 'gi://Cogl';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {monitorForEntry} from './geometry.js';

// Applied to the cropped result, AFTER the wallpaper has been blurred. CSS
// border-radius alone does not clip an actor's children or a blur effect.
const RoundedMask = GObject.registerClass(
class RoundedMask extends Shell.GLSLEffect {
    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT,
            'uniform vec2 card_size; uniform float card_radius;', `
            vec2 half_size = card_size * 0.5;
            vec2 p = cogl_tex_coord_in[0].xy * card_size - half_size;
            vec2 q = abs(p) - half_size + vec2(card_radius);
            float distance = length(max(q, vec2(0.0)))
                + min(max(q.x, q.y), 0.0) - card_radius;
            cogl_color_out *= 1.0 - smoothstep(-0.5, 0.5, distance);
            `, false);
    }

    setGeometry(width, height, radius) {
        this.set_uniform_float(this.get_uniform_location('card_size'), 2,
            [width, height]);
        this.set_uniform_float(this.get_uniform_location('card_radius'), 1,
            [Math.min(radius, width / 2, height / 2)]);
    }
});

/**
 * Paint only wallpaper into a bounded offscreen surface. BACKGROUND blur reads
 * the stage framebuffer, whose partially repainted regions may contain stale
 * window or text pixels. ACTOR blur never samples those unrelated pixels.
 */
export const WallpaperGlass = GObject.registerClass(
class WallpaperGlass extends St.Widget {
    _init(owner, entry, style) {
        super._init({
            x_expand: true, y_expand: true,
            clip_to_allocation: true,
        });
        this._owner = owner;
        this._entry = entry;
        this._radius = Math.max(0, Math.min(64, style.blur_radius ?? 24));
        this._cornerRadius = Math.max(0, style.corner_radius ?? 24);
        this._enabled = true;
        this._manager = null;
        this._geometryKey = null;
        this._sample = new St.Widget({
            clip_to_allocation: true,
        });
        this.add_child(this._sample);
        this._blur = new Shell.BlurEffect({
            mode: Shell.BlurMode.ACTOR,
            brightness: style.dark ? 0.82 : 1.0,
        });
        this._sample.add_effect_with_name('desktop-forge-wallpaper-blur', this._blur);
        this._mask = new RoundedMask();
        this.add_effect_with_name('desktop-forge-rounded-mask', this._mask);
        this._ensureBackground();

        owner.connectObject(
            'notify::allocation', () => this.syncGeometry(),
            'notify::mapped', () => this.syncGeometry(),
            'resource-scale-changed', () => this.syncGeometry(), this);
        Main.layoutManager.connectObject('monitors-changed', () => {
            this._clearBackground();
            this._ensureBackground();
            this.queue_relayout();
        }, this);
        this.connect('destroy', () => {
            owner.disconnectObject(this);
            Main.layoutManager.disconnectObject(this);
            this._clearBackground();
        });
    }

    vfunc_get_preferred_width(_forHeight) {
        return [0, 0];
    }

    vfunc_get_preferred_height(_forWidth) {
        return [0, 0];
    }

    vfunc_allocate(box) {
        this.set_allocation(box);
        this.syncGeometry();
    }

    _ensureBackground() {
        const monitor = monitorForEntry(this._entry);
        if (!monitor)
            return;
        this._monitorIndex = monitor.index;
        this._manager = new Background.BackgroundManager({
            container: this._sample,
            monitorIndex: monitor.index,
            controlPosition: false,
        });
        // Allocate all wallpaper actors, including a new image while it loads
        // and the old actor while it fades out. Never use their natural size.
        this._sample.connectObject('child-added', () => {
            this._geometryKey = null;
            this.queue_relayout();
        }, this);
        this._manager.connect('changed', () => {
            this._geometryKey = null;
            this.queue_relayout();
        });
    }

    syncGeometry() {
        const monitor = monitorForEntry(this._entry);
        if (!monitor || !this._enabled || !this._owner.mapped || !this.has_allocation())
            return;
        const [x, y] = this._owner.get_transformed_position();
        const [width, height] = this.get_size();
        if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0)
            return;
        const scale = this._owner.get_resource_scale();
        const key = `${monitor.index}:${monitor.x}:${monitor.y}:${monitor.width}:${monitor.height}:${x}:${y}:${width}:${height}:${scale}`;
        const changed = key !== this._geometryKey;

        try {
            this._geometryKey = key;
            this._wallpaperGeometry = {monitor, x, y};
            const margin = Math.ceil(this._radius * 2);
            this._margin = margin;
            // Changing width requests during an allocation invalidates the
            // whole card again. Allocate the sample and its wallpaper directly
            // so a resize finishes in the same layout pass.
            this._sample.allocate(new Clutter.ActorBox({
                x1: -margin, y1: -margin, x2: width + margin, y2: height + margin,
            }));
            if (changed) {
                if (this._blur.constructor.find_property('radius'))
                    this._blur.radius = this._radius * scale;
                else
                    this._blur.sigma = this._radius * scale / 2;
                const themeScale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
                this._mask.setGeometry(width, height, this._cornerRadius * themeScale);
            }
            this._placeWallpaper();
        } catch (error) {
            // Keep the existing translucent gradient usable on unsupported
            // renderers; retry only after an explicit appearance/geometry change.
            this._geometryKey = key;
            this.hide();
            logError(error, 'desktop-forge: wallpaper glass unavailable');
        }
    }

    _placeWallpaper() {
        if (!this._wallpaperGeometry)
            return;
        const {monitor, x, y} = this._wallpaperGeometry;
        for (const actor of this._sample.get_children()) {
            const left = monitor.x - x + this._margin;
            const top = monitor.y - y + this._margin;
            actor.allocate(new Clutter.ActorBox({
                x1: left, y1: top,
                x2: left + monitor.width, y2: top + monitor.height,
            }));
            actor.content.brightness = 1;
        }
    }

    setBlurEnabled(enabled) {
        this._enabled = !!enabled;
        this.visible = this._enabled;
        if (this._enabled) {
            this._geometryKey = null;
            this.syncGeometry();
        }
    }

    _clearBackground() {
        this._sample.disconnectObject(this);
        this._manager?.destroy();
        this._sample.destroy_all_children();
        this._manager = null;
        this._geometryKey = null;
        this._wallpaperGeometry = null;
    }
});
