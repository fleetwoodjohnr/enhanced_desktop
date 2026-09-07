import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {DesktopWidget} from './base.js';
import {AddReminderDialog} from './reminderDialog.js';
import {completeReminder, loadReminders} from '../store.js';

const MAX_ROWS = 6;

export const RemindersWidget = GObject.registerClass(
class RemindersWidget extends DesktopWidget {
    get interactive() {
        return true;
    }

    buildBody(body) {
        const heading = this.addHeader(body, 'Reminders', 'alarm-symbolic');
        const {header} = heading;
        this._title = heading.title;

        const add = new St.Button({
            style_class: 'df-add',
            label: '+',
            can_focus: true,
            accessible_name: 'Add a reminder',
            y_align: Clutter.ActorAlign.CENTER,
        });
        add.connect('clicked', () => this._openDialog());
        header.add_child(add);

        this._rows = new St.BoxLayout({
            style_class: 'df-list', vertical: true, x_expand: true,
        });
        body.add_child(this._rows);
    }

    _openDialog() {
        new AddReminderDialog(() => this._renderLocal(), this._style.dark).open();
    }

    /**
     * Redraw straight from the store after a write of our own.
     *
     * The daemon notices the store changing and republishes its state file a
     * moment later, which is what normally drives this widget; rendering the
     * same projection here just means the click feels instant.
     */
    _renderLocal() {
        this.renderData(project());
    }

    render(data) {
        this._rows.destroy_all_children();

        const items = data.reminders ?? [];
        this._title.text = data.overdue
            ? `Reminders · ${data.overdue} due` : 'Reminders';

        if (!items.length) {
            this._rows.add_child(new St.Label({
                style_class: 'df-empty', text: 'Nothing pending',
            }));
            return;
        }

        for (const item of items.slice(0, MAX_ROWS)) {
            const row = new St.BoxLayout({
                style_class: `df-row df-reminder-row${item.overdue ? ' df-reminder-overdue' : ''}`,
                x_expand: true,
            });

            const tick = new St.Button({
                style_class: 'df-tick',
                can_focus: true,
                y_align: Clutter.ActorAlign.CENTER,
                child: new St.Icon({
                    icon_name: item.overdue ? 'alarm-symbolic' : 'checkbox-symbolic',
                    icon_size: 14,
                    style: item.overdue ? 'color: #e01b24' : '',
                }),
            });
            tick.connect('clicked', () => {
                if (completeReminder(item.id))
                    this._renderLocal();
                else
                    this.setStatus('Could not update the reminder', false);
            });
            row.add_child(tick);

            const text = new St.Label({
                style_class: 'df-reminder-text', text: item.text, x_expand: true,
            });
            text.clutter_text.ellipsize = 3;
            row.add_child(text);

            row.add_child(new St.Label({
                style_class: `df-due-badge${item.overdue ? ' df-due-overdue' : ''}`,
                text: relative(item.due_in_seconds),
            }));
            this._rows.add_child(row);
        }
    }
});

/**
 * The same projection RemindersProvider.fetch() does, in JavaScript.
 *
 * Only used between a click here and the daemon's next write, so the two
 * cannot drift far -- but they do have to agree on what a due date means, and
 * they do: the store holds local time with no zone, which is exactly how
 * `new Date(...)` reads it.
 */
function project() {
    const now = new Date();
    const items = [];

    for (const reminder of loadReminders()) {
        if (reminder.done)
            continue;
        const due = reminder.due ? new Date(reminder.due) : null;
        const valid = due && !isNaN(due);
        items.push({
            id: reminder.id,
            text: reminder.text ?? '',
            due: reminder.due,
            repeat: reminder.repeat ?? 'none',
            overdue: !!(valid && due < now),
            due_in_seconds: valid ? Math.round((due - now) / 1000) : null,
        });
    }

    items.sort((a, b) => `${a.due ?? ''}`.localeCompare(`${b.due ?? ''}`));
    return {
        reminders: items,
        overdue: items.filter(item => item.overdue).length,
        total: items.length,
    };
}

function relative(seconds) {
    if (seconds == null)
        return '';
    const overdue = seconds < 0;
    let value = Math.abs(seconds);

    const units = [[86400, 'd'], [3600, 'h'], [60, 'm']];
    for (const [size, suffix] of units) {
        if (value >= size)
            return `${overdue ? '-' : ''}${Math.floor(value / size)}${suffix}`;
    }
    return overdue ? 'now' : '<1m';
}
