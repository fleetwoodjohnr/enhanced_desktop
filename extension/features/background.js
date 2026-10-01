import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BLUR = 'desktop-forge-wallpaper-blur';
const DIM = 'desktop-forge-wallpaper-dim';

/**
 * Blur and dim the desktop wallpaper.
 *
 * The effects go on each monitor's wallpaper actor, not on the background
 * group: the desktop cards live in that group and must stay sharp. Shell
 * replaces wallpaper actors when the picture changes and rebuilds the
 * managers when monitors change, so both are followed.
 */
export class BackgroundEffects {
    constructor() {
        this._blur = 0;
        this._dim = 0;
        this._managers = new Set();
        this._idleId = 0;
        Main.layoutManager.connectObject('monitors-changed', () => this._queue(), this);
    }

    update({wallpaper = {}}) {
        this._blur = Number.isFinite(wallpaper.blur) ? Math.max(0, Math.min(60, wallpaper.blur)) : 0;
        this._dim = Number.isFinite(wallpaper.dim) ? Math.max(0, Math.min(0.8, wallpaper.dim)) : 0;
        this._apply();
    }

    _queue() {
        if (this._idleId)
            return;
        // Shell rebuilds its background managers in its own monitors-changed
        // handler; wait for that before looking for the new actors.
        this._idleId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._idleId = 0;
            this._apply();
            return GLib.SOURCE_REMOVE;
        });
    }

    _apply() {
        const managers = new Set(Main.layoutManager._bgManagers ?? []);
        for (const manager of this._managers) {
            if (!managers.has(manager))
                manager.disconnectObject(this);
        }
        for (const manager of managers) {
            if (!this._managers.has(manager))
                manager.connectObject('changed', () => this._style(manager.backgroundActor), this);
            this._style(manager.backgroundActor);
        }
        this._managers = managers;
    }

    _style(actor) {
        if (!actor)
            return;
        let blur = actor.get_effect(BLUR);
        if (this._blur > 0) {
            if (!blur) {
                blur = new Shell.BlurEffect({mode: Shell.BlurMode.ACTOR});
                actor.add_effect_with_name(BLUR, blur);
            }
            if (blur.constructor.find_property('radius'))
                blur.radius = this._blur;
            else
                blur.sigma = this._blur / 2;
        } else if (blur) {
            actor.remove_effect(blur);
        }
        let dim = actor.get_effect(DIM);
        if (this._dim > 0) {
            if (!dim) {
                dim = new Clutter.BrightnessContrastEffect();
                actor.add_effect_with_name(DIM, dim);
            }
            dim.set_brightness(-this._dim);
        } else if (dim) {
            actor.remove_effect(dim);
        }
    }

    destroy() {
        if (this._idleId) {
            GLib.source_remove(this._idleId);
            this._idleId = 0;
        }
        Main.layoutManager.disconnectObject(this);
        this._blur = 0;
        this._dim = 0;
        for (const manager of this._managers) {
            manager.disconnectObject(this);
            this._style(manager.backgroundActor);
        }
        this._managers.clear();
    }
}
