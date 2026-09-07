import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {ModalDialog} from 'resource:///org/gnome/shell/ui/modalDialog.js';

import {addTodo, updateTodo} from '../store.js';

export const TodoDialog = GObject.registerClass(
class TodoDialog extends ModalDialog {
    /** @param {Object|null} item null creates; an item edits its text. */
    _init(item, onSaved, dark = false) {
        super._init({styleClass: 'df-dialog'});
        this.dialogLayout.add_style_class_name(
            dark ? 'df-theme-dark' : 'df-theme-light');
        this._item = item;
        this._onSaved = onSaved;

        this.contentLayout.add_child(new St.Label({
            style_class: 'df-dialog-title',
            text: item ? 'Rename task' : 'New task',
        }));

        this._entry = new St.Entry({
            style_class: 'df-entry',
            hint_text: 'What needs to be done?',
            text: item?.text ?? '',
            can_focus: true,
            x_expand: true,
        });
        this._entry.clutter_text.connect('activate', () => this._save());
        this.contentLayout.add_child(this._entry);

        this._error = new St.Label({style_class: 'df-error', visible: false});
        this.contentLayout.add_child(this._error);

        this.setButtons([
            {label: 'Cancel', action: () => this.close(), key: Clutter.KEY_Escape},
            {label: item ? 'Save' : 'Add', action: () => this._save(), default: true},
        ]);
        this.setInitialKeyFocus(this._entry);
    }

    _save() {
        const text = this._entry.get_text().trim();
        if (!text)
            return;
        const saved = this._item
            ? updateTodo(this._item.id, {text})
            : addTodo(text);
        if (!saved) {
            this._error.text = 'Could not save the task';
            this._error.visible = true;
            return;
        }
        this._onSaved();
        this.close();
    }
});
