/**
 * Adding a reminder without leaving the desktop.
 *
 * A shell dialog rather than an entry inside the card: a modal dialog is the
 * one place on the desktop where keyboard focus is guaranteed, and it leaves
 * room for a due time and a repeat, which an inline box on a 300px card does
 * not have.
 *
 * The record it writes has to match providers/reminders.py exactly -- see the
 * note on localIso() in store.js for the one detail that must not drift.
 */
import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {ModalDialog} from 'resource:///org/gnome/shell/ui/modalDialog.js';

import {addReminder} from '../store.js';

/** Label, and the due date it stands for. Evaluated when the user commits. */
const WHEN_CHOICES = [
    {label: 'In 1 hour', due: () => shift(new Date(), {hours: 1})},
    {label: 'This evening', due: () => nextAt(18, 0)},
    {label: 'Tomorrow 9am', due: () => nextAt(9, 0, 1)},
    {label: 'Next week', due: () => nextAt(9, 0, 7)},
];

const REPEAT_CHOICES = [
    {label: 'Never', value: 'none'},
    {label: 'Daily', value: 'daily'},
    {label: 'Weekly', value: 'weekly'},
    {label: 'Monthly', value: 'monthly'},
];

export const AddReminderDialog = GObject.registerClass(
class AddReminderDialog extends ModalDialog {
    /** @param {Function} onAdded called with the new reminder record. */
    _init(onAdded, dark = false) {
        super._init({styleClass: 'df-dialog'});
        this.dialogLayout.add_style_class_name(
            dark ? 'df-theme-dark' : 'df-theme-light');
        this._onAdded = onAdded;

        this.contentLayout.add_child(new St.Label({
            style_class: 'df-dialog-title', text: 'New reminder',
        }));

        const content = new St.BoxLayout({
            style_class: 'df-dialog-content', vertical: true,
        });
        this.contentLayout.add_child(content);

        this._entry = new St.Entry({
            style_class: 'df-entry',
            hint_text: 'What should I remind you about?',
            can_focus: true,
            x_expand: true,
        });
        this._entry.clutter_text.connect('activate', () => this._add());
        content.add_child(this._entry);

        this._error = new St.Label({style_class: 'df-error', visible: false});
        content.add_child(this._error);

        content.add_child(label('WHEN'));
        this._when = chips(content, WHEN_CHOICES.map(c => c.label), 0);

        content.add_child(label('REPEAT'));
        this._repeat = chips(content, REPEAT_CHOICES.map(c => c.label), 0);

        this.setButtons([
            {label: 'Cancel', action: () => this.close(), key: Clutter.KEY_Escape},
            {label: 'Add', action: () => this._add(), default: true},
        ]);
        // After setButtons: addButton() claims the initial focus for the
        // default button, and the entry is what the user wants to type into.
        this.setInitialKeyFocus(this._entry);
    }

    _add() {
        const text = this._entry.get_text().trim();
        if (!text)
            return;

        const reminder = addReminder(
            text,
            WHEN_CHOICES[this._when.selected()].due(),
            REPEAT_CHOICES[this._repeat.selected()].value);

        if (!reminder) {
            this._error.text = 'Could not save the reminder';
            this._error.visible = true;
            return;
        }
        this._onAdded(reminder);
        this.close();
    }
});

// -- small pieces ----------------------------------------------------------

function label(text) {
    return new St.Label({style_class: 'df-dialog-label', text});
}

/**
 * A row of mutually exclusive toggle buttons.
 *
 * St.Button's toggle mode gives the :checked styling for free; keeping the
 * group exclusive is left to the caller, which is all this does.
 */
function chips(parent, labels, initial) {
    const row = new St.BoxLayout({style_class: 'df-chips'});
    const buttons = labels.map((text, index) => {
        const button = new St.Button({
            style_class: 'df-chip',
            label: text,
            toggle_mode: true,
            can_focus: true,
            checked: index === initial,
        });
        row.add_child(button);
        return button;
    });

    for (const button of buttons) {
        button.connect('clicked', () => {
            for (const other of buttons)
                other.checked = other === button;
        });
    }

    parent.add_child(row);
    return {
        selected: () => Math.max(0, buttons.findIndex(b => b.checked)),
    };
}

function shift(date, {hours = 0, days = 0}) {
    const result = new Date(date.getTime());
    result.setSeconds(0, 0);
    result.setDate(result.getDate() + days);
    result.setHours(result.getHours() + hours);
    return result;
}

/**
 * The next time the clock reads hour:minute, `days` from now.
 *
 * Rolling past a time that has already gone is what makes "This evening"
 * mean tonight in the morning and tomorrow night at 11pm, rather than
 * scheduling a reminder into the past.
 */
function nextAt(hour, minute, days = 0) {
    const result = new Date();
    result.setSeconds(0, 0);
    result.setDate(result.getDate() + days);
    result.setHours(hour, minute);
    if (result <= new Date())
        result.setDate(result.getDate() + 1);
    return result;
}
