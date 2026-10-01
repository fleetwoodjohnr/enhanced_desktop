import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {systemIsDark} from '../themeLogic.js';
import {WallpaperGlass} from '../widgetBackground.js';

/**
 * Top bar extras that belong to the user session: the clock's position, the
 * workspace indicator, and blurred wallpaper behind the bar.
 *
 * The floating shape is passed through to the PanelController, which keeps
 * it across the lock screen (it changes the work area); destroying this
 * module leaves it in place on purpose.
 */
export class TopBarExtras {
    constructor(panelController) {
        this._controller = panelController;
        this._panel = Main.panel;
        this._clock = this._panel.statusArea.dateMenu?.container ?? null;
        this._clockHome = this._clock?.get_parent() ?? null;
        this._clockIndex = this._clockHome ? this._clockHome.get_children().indexOf(this._clock) : -1;
        this._activities = this._panel.statusArea.activities?.container ?? null;
        this._blur = null;
        this._values = {};
    }

    update({top_bar: values = {}}) {
        this._values = values;
        this._controller?.setExtras({
            floating: values.floating === true, margin: values.margin, radius: values.radius,
        });
        if (this._activities)
            this._activities.visible = values.show_activities !== false;
        this._placeClock(values.clock_position ?? 'center');
        this._syncBlur();
    }

    _placeClock(position) {
        if (!this._clock || !this._clockHome)
            return;
        const target = {left: this._panel._leftBox, right: this._panel._rightBox}[position] ?? this._clockHome;
        if (this._clock.get_parent() === target)
            return;
        this._clock.get_parent()?.remove_child(this._clock);
        if (target === this._clockHome)
            target.insert_child_at_index(this._clock, Math.max(0, this._clockIndex));
        else if (target === this._panel._leftBox)
            target.add_child(this._clock);
        else
            target.insert_child_at_index(this._clock, 0);
    }

    _syncBlur() {
        const radius = this._values.floating ? this._values.radius ?? 12 : 0;
        const wanted = this._values.blur === true;
        if (!wanted) {
            this._destroyBlur();
        } else if (this._blur) {
            // A radius slider moves in small steps: reshape, do not rebuild.
            this._blur.setRadius(radius);
        } else {
            try {
                this._blur = new PanelBlur(radius);
            } catch (error) {
                logError(error, 'desktop-forge: top bar blur unavailable');
            }
        }
    }

    _destroyBlur() {
        this._blur?.destroy();
        this._blur = null;
    }

    destroy() {
        this._destroyBlur();
        if (this._activities)
            this._activities.visible = true;
        this._placeClock('center');
    }
}

/**
 * Wallpaper glass kept exactly under the bar as it moves and hides.
 *
 * It sits below the overview and the lock screen, which cover it, and
 * above the windows; only the bar's own rectangle is drawn.
 */
class PanelBlur {
    constructor(radius) {
        this._panel = Main.panel;
        this._box = Main.layoutManager.panelBox;
        this._host = new St.Widget({layout_manager: new Clutter.BinLayout(), reactive: false});
        Main.layoutManager.uiGroup.insert_child_below(this._host, Main.layoutManager.overviewGroup);
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        // The primary display can change; look it up each time.
        const entry = {get monitor() {
            return Main.layoutManager.primaryIndex;
        }};
        this._glass = new WallpaperGlass(this._host, entry,
            {blur_radius: 24, corner_radius: radius, dark: this._dark()});
        this._host.add_child(this._glass);
        this._interface.connectObject('changed::color-scheme',
            () => this._glass.setAppearance({dark: this._dark()}), this);
        this._laterId = 0;
        this._panel.connectObject('notify::allocation', () => this._queueSync(), this);
        this._box.connectObject(
            'notify::allocation', () => this._queueSync(),
            'notify::translation-y', () => this._queueSync(),
            'notify::visible', () => this._queueSync(), this);
        this._queueSync();
    }

    _dark() {
        return systemIsDark(this._interface.get_string('color-scheme'), Main.getStyleVariant?.());
    }

    setRadius(radius) {
        this._glass.setAppearance({corner_radius: radius});
    }

    /**
     * Follow the bar just before the next frame: moving or resizing the
     * glass from inside the bar's allocation would re-lay out mid-allocation.
     */
    _queueSync() {
        if (this._laterId)
            return;
        this._laterId = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._laterId = 0;
            this._sync();
            return GLib.SOURCE_REMOVE;
        });
    }

    _sync() {
        const [x, y] = this._panel.get_transformed_position();
        const [width, height] = this._panel.get_transformed_size();
        if (![x, y, width, height].every(Number.isFinite))
            return;
        this._host.set_position(x, y);
        this._host.set_size(width, height);
        this._host.visible = this._box.visible && width > 0 && height > 0;
    }

    destroy() {
        if (this._laterId) {
            global.compositor.get_laters().remove(this._laterId);
            this._laterId = 0;
        }
        this._panel.disconnectObject(this);
        this._box.disconnectObject(this);
        this._interface.disconnectObject(this);
        this._host.destroy();
    }
}
