import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {
    describeConnection, displayHostname, formatRate, formatTemperature,
    thermalLevel, visibleSensors,
} from '../widgetLogic.js';
import {DesktopWidget, meter} from './base.js';

// Temperature chips per row; five across does not fit a card of the default
// width without ellipsizing every label.
const CHIPS_PER_ROW = 3;

export const SystemWidget = GObject.registerClass(
class SystemWidget extends DesktopWidget {
    buildBody(body) {
        const options = this._config.options ?? {};
        this._showThermals = options.show_thermals === true;
        this._showNetwork = options.show_network === true;
        this._showIp = options.show_ip !== false;
        this._unit = options.temperature_unit === 'fahrenheit' ? 'fahrenheit' : 'celsius';
        this._sensorSet = options.thermal_sensors === 'all' ? 'all' : 'main';

        this._title = this.addTitle(body, 'System', 'applications-system-symbolic');
        this._meters = {};
        for (const [key, label] of [['cpu', 'CPU'], ['memory', 'Memory'], ['disk', 'Disk']])
            this._meters[key] = this._addMeter(body, label);

        if (this._showThermals)
            this._buildThermals(body);
        if (this._showNetwork)
            this._buildNetwork(body);

        this._footer = new St.BoxLayout({style_class: 'df-system-footer', x_expand: true});
        this._footer.set_style(`color: ${this.muted}`);
        this._footerParts = {};
        for (const key of ['battery', 'network', 'uptime']) {
            const separator = new St.Label({style_class: 'df-detail', text: '  ·  '});
            const label = new St.Label({style_class: 'df-detail'});
            this._footer.add_child(separator);
            this._footer.add_child(label);
            this._footerParts[key] = {separator, label};
        }
        body.add_child(this._footer);
    }

    _addMeter(body, label) {
        const group = new St.BoxLayout({
            style_class: 'df-meter-group', vertical: true, x_expand: true,
        });
        const header = new St.BoxLayout({x_expand: true});
        header.add_child(new St.Label({
            style_class: 'df-meter-label', text: label, x_expand: true,
        }));
        const value = new St.Label({style_class: 'df-meter-value'});
        header.add_child(value);
        group.add_child(header);

        const bar = meter(this.accent);
        group.add_child(bar.actor);
        body.add_child(group);
        return {value, bar};
    }

    _buildThermals(body) {
        this._thermals = new St.BoxLayout({
            style_class: 'df-system-section', vertical: true, x_expand: true,
        });
        this._thermals.add_child(new St.Label({
            style_class: 'df-meter-label', text: 'Temperatures',
        }));
        this._chipRows = new St.BoxLayout({vertical: true, x_expand: true});
        this._thermals.add_child(this._chipRows);
        this._thermalSignature = null;
        body.add_child(this._thermals);
    }

    _buildNetwork(body) {
        const section = new St.BoxLayout({
            style_class: 'df-system-section', vertical: true, x_expand: true,
        });
        const header = new St.BoxLayout({x_expand: true, style_class: 'df-system-network'});
        this._networkIcon = new St.Icon({
            icon_name: 'network-wired-symbolic', style_class: 'df-system-network-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        header.add_child(this._networkIcon);
        this._networkTitle = new St.Label({
            style_class: 'df-meter-label', x_expand: true, y_align: Clutter.ActorAlign.CENTER,
        });
        this._networkTitle.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        header.add_child(this._networkTitle);
        this._networkRates = new St.Label({
            style_class: 'df-meter-value', y_align: Clutter.ActorAlign.CENTER,
        });
        header.add_child(this._networkRates);
        section.add_child(header);
        this._networkDetail = new St.Label({style_class: 'df-detail'});
        this._networkDetail.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._networkDetail.set_style(`color: ${this.muted}`);
        section.add_child(this._networkDetail);
        body.add_child(section);
    }

    render(data) {
        setText(this._title, displayHostname(data.hostname));

        this._set('cpu', data.cpu_percent);
        this._set('memory', data.memory?.percent);
        this._set('disk', data.disk?.percent);
        if (this._showThermals)
            this._renderThermals(data.thermals);
        if (this._showNetwork)
            this._renderNetwork(data.network);

        const bits = {
            battery: data.battery?.percent != null
                ? `Battery ${data.battery.percent}% ${data.battery.status ?? ''}`.trim() : '',
            // The network section carries the rates itself when it is shown.
            network: !this._showNetwork && data.network?.rx_rate != null
                ? `↓${formatRate(data.network.rx_rate)} ↑${formatRate(data.network.tx_rate)}` : '',
            uptime: data.uptime_seconds != null
                ? `Up ${Math.floor(data.uptime_seconds / 3600)}h` : '',
        };
        let hasPrevious = false;
        for (const [key, text] of Object.entries(bits)) {
            const {label, separator} = this._footerParts[key];
            setText(label, text);
            label.visible = !!text;
            separator.visible = !!text && hasPrevious;
            hasPrevious ||= !!text;
        }
    }

    _renderThermals(thermals) {
        const sensors = visibleSensors(thermals, this._sensorSet);
        // Rebuild the chips only when the set of sensors changes; a reading
        // changing is a text update, not a relayout of the whole section.
        const signature = sensors.map(sensor => sensor.id).join('|');
        if (signature !== this._thermalSignature) {
            this._thermalSignature = signature;
            this._chipRows.destroy_all_children();
            this._chips = new Map();
            let row = null;
            sensors.forEach((sensor, index) => {
                if (index % CHIPS_PER_ROW === 0) {
                    row = new St.BoxLayout({x_expand: true, style_class: 'df-temp-row'});
                    this._chipRows.add_child(row);
                }
                const chip = new St.BoxLayout({style_class: 'df-temp-chip', x_expand: true});
                const label = new St.Label({style_class: 'df-detail', text: sensor.label});
                label.set_style(`color: ${this.muted}`);
                const value = new St.Label({style_class: 'df-temp-value', x_expand: true,
                    x_align: Clutter.ActorAlign.END});
                chip.add_child(label);
                chip.add_child(value);
                row.add_child(chip);
                this._chips.set(sensor.id, value);
            });
            if (!sensors.length) {
                const empty = new St.Label({style_class: 'df-detail', text: 'No temperature sensors'});
                empty.set_style(`color: ${this.muted}`);
                this._chipRows.add_child(empty);
            }
        }
        for (const sensor of sensors) {
            const value = this._chips.get(sensor.id);
            if (!value)
                continue;
            setText(value, formatTemperature(sensor.celsius, this._unit));
            const level = thermalLevel(sensor);
            for (const name of ['warm', 'hot']) {
                if (name === level)
                    value.add_style_class_name(`df-temp-${name}`);
                else
                    value.remove_style_class_name(`df-temp-${name}`);
            }
        }
    }

    _renderNetwork(network) {
        const {title, detail, icon} = describeConnection(network, {showIp: this._showIp});
        setText(this._networkTitle, title);
        setText(this._networkDetail, detail);
        this._networkDetail.visible = !!detail;
        if (this._networkIcon.icon_name !== icon)
            this._networkIcon.icon_name = icon;
        setText(this._networkRates, network?.rx_rate != null
            ? `↓${formatRate(network.rx_rate)} ↑${formatRate(network.tx_rate)}` : '');
    }

    _set(key, percent) {
        const target = this._meters[key];
        setText(target.value, percent != null ? `${percent.toFixed(0)}%` : '--');
        target.bar.set(percent);
    }
});

function setText(label, text) {
    if (label.text !== text)
        label.text = text;
}
