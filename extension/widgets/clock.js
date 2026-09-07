import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {DesktopWidget} from './base.js';

/**
 * The only widget with no provider behind it -- the time is already on this
 * machine, so routing it through the daemon would add latency and a file
 * write per second for nothing.
 */
export const ClockWidget = GObject.registerClass(
class ClockWidget extends DesktopWidget {
    buildBody(body) {
        this._time = new St.Label({style_class: 'df-clock-time'});
        this._time.set_style(`color: ${this.accent}`);
        this._date = new St.Label({style_class: 'df-clock-date'});
        this._date.set_style(`color: ${this.muted}`);
        body.add_child(this._time);
        body.add_child(this._date);

        this._tick();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._tick();
            return GLib.SOURCE_CONTINUE;
        });

        // A timeout outlives the actor unless it is explicitly removed, and a
        // stale source firing against a destroyed actor crashes the shell.
        this.connect('destroy', () => {
            if (this._timer) {
                GLib.source_remove(this._timer);
                this._timer = null;
            }
        });
    }

    _tick() {
        const now = GLib.DateTime.new_now_local();
        const seconds = this._config.options?.show_seconds ?? false;
        const ampm = this._config.options?.twelve_hour ?? true;

        let format = ampm ? '%-I:%M' : '%H:%M';
        if (seconds)
            format += ':%S';
        if (ampm)
            format += ' %p';

        this._time.text = now.format(format);
        this._date.text = now.format('%A, %e %B %Y').replace(/\s+/g, ' ');
    }
});
