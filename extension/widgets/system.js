import GObject from 'gi://GObject';
import St from 'gi://St';

import {displayHostname} from '../widgetLogic.js';
import {DesktopWidget, meter} from './base.js';

export const SystemWidget = GObject.registerClass(
class SystemWidget extends DesktopWidget {
    buildBody(body) {
        this._title = this.addTitle(body, 'System', 'applications-system-symbolic');
        this._meters = {};
        for (const [key, label] of [['cpu', 'CPU'], ['memory', 'Memory'], ['disk', 'Disk']])
            this._meters[key] = this._addMeter(body, label);

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

    render(data) {
        setText(this._title, displayHostname(data.hostname));

        this._set('cpu', data.cpu_percent);
        this._set('memory', data.memory?.percent);
        this._set('disk', data.disk?.percent);

        const bits = {
            battery: data.battery?.percent != null
                ? `Battery ${data.battery.percent}% ${data.battery.status ?? ''}`.trim() : '',
            network: data.network?.rx_rate != null
                ? `↓${rate(data.network.rx_rate)} ↑${rate(data.network.tx_rate)}` : '',
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

function rate(bytesPerSecond) {
    if (bytesPerSecond == null)
        return '--';
    const units = ['B', 'K', 'M', 'G'];
    let value = bytesPerSecond;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
    }
    return `${value < 10 ? value.toFixed(1) : Math.round(value)}${units[index]}/s`;
}
