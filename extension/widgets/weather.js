import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {DesktopWidget} from './base.js';

const HOURLY_COLUMNS = 6;

export const WeatherWidget = GObject.registerClass(
class WeatherWidget extends DesktopWidget {
    buildBody(body) {
        this._title = this.addTitle(body, 'Weather');

        // The icon is 44px and the temperature is much taller, so the two
        // have to be centred against each other or the icon sits on the
        // baseline. This used to read St.Align.MIDDLE, which never worked:
        // St.Align was a different enum from the Clutter.ActorAlign the
        // y-align property actually takes, and its MIDDLE (1) landed on
        // ActorAlign.START. St.Align was then removed outright in GNOME 50,
        // turning a silent no-op into an exception that took the whole
        // extension down with it.
        const top = new St.BoxLayout({style_class: 'df-weather-current'});
        this._icon = new St.Icon({icon_size: 44, y_align: Clutter.ActorAlign.CENTER});
        this._temp = new St.Label({
            style_class: 'df-temp', y_align: Clutter.ActorAlign.CENTER,
        });
        top.add_child(this._icon);
        top.add_child(this._temp);
        body.add_child(top);

        this._condition = new St.Label({style_class: 'df-condition'});
        this._detail = new St.Label({style_class: 'df-detail'});
        this._detail.set_style(`color: ${this.muted}`);
        body.add_child(this._condition);
        body.add_child(this._detail);

        this._hourly = new St.BoxLayout({style_class: 'df-forecast'});
        body.add_child(this._hourly);
        this._forecast = new St.BoxLayout({style_class: 'df-forecast'});
        body.add_child(this._forecast);
    }

    render(data) {
        this._title.text = data.place ?? 'Weather';
        this._temp.text = data.temperature != null
            ? `${Math.round(data.temperature)}${data.unit ?? ''}` : '--';
        this._condition.text = data.condition ?? '';
        this._icon.icon_name = `${data.icon ?? 'weather-clear'}-symbolic`;
        this._icon.set_style(`color: ${this.accent}`);

        const bits = [];
        if (data.feels_like != null)
            bits.push(`Feels ${Math.round(data.feels_like)}${data.unit ?? ''}`);
        if (data.humidity != null)
            bits.push(`${data.humidity}% humidity`);
        if (data.wind_speed != null)
            bits.push(`${Math.round(data.wind_speed)} ${data.wind_unit ?? ''}`);
        this._detail.text = bits.join('  ·  ');

        const hours = Array.isArray(data.hourly) ? data.hourly : [];
        // Fall back to the daily strip whenever there is no hourly data to
        // show -- an older daemon, or a response missing the hourly block.
        const mode = hours.length ? (data.forecast_mode ?? 'hourly') : 'daily';

        this._hourly.destroy_all_children();
        this._hourly.visible = mode !== 'daily';
        if (this._hourly.visible) {
            for (const hour of hours.slice(0, HOURLY_COLUMNS)) {
                this._hourly.add_child(forecastColumn(
                    hourLabel(hour.time), hour.icon, hour.temperature));
            }
        }

        this._forecast.destroy_all_children();
        this._forecast.visible = mode !== 'hourly';
        if (this._forecast.visible) {
            // Skip index 0: the API's first daily entry is today, which the big
            // current-conditions readout above already covers.
            for (const day of (data.forecast ?? []).slice(1, 5)) {
                this._forecast.add_child(forecastColumn(
                    weekday(day.date), day.icon, day.high));
            }
        }
    }
});

function forecastColumn(label, icon, temperature) {
    const column = new St.BoxLayout({
        vertical: true, style_class: 'df-forecast-day', x_expand: true,
    });
    column.add_child(new St.Label({text: label}));
    column.add_child(new St.Icon({
        icon_name: `${icon ?? 'weather-clear'}-symbolic`, icon_size: 16,
    }));
    column.add_child(new St.Label({
        text: temperature != null ? `${Math.round(temperature)}°` : '--',
    }));
    return column;
}

function hourLabel(iso) {
    const date = new Date(iso);
    return isNaN(date) ? '' :
        date.toLocaleTimeString(undefined, {hour: 'numeric'});
}

function weekday(iso) {
    const date = new Date(`${iso}T00:00:00`);
    return isNaN(date) ? '' : date.toLocaleDateString(undefined, {weekday: 'short'});
}
