import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {DesktopWidget} from './base.js';
import {TodoDialog} from './todoDialog.js';
import {styleScrollbar} from './scrollbar.js';
import {
    TODO_STATUSES, loadTodos, moveTodo, removeTodo, updateTodo,
} from '../store.js';

const STATUS_LABELS = {
    todo: 'To Do',
    in_progress: 'In Progress',
    blocked: 'Blocked',
    done: 'Done',
};

export const TodosWidget = GObject.registerClass(
class TodosWidget extends DesktopWidget {
    get tracksHover() { return true; }

    get interactive() {
        return true;
    }

    buildBody(body) {
        this._menus = [];
        this._rowEntries = [];
        this._dropIndex = null;
        this._renderAfterDrag = false;
        this._renderId = 0;
        this._menuManager = new PopupMenu.PopupMenuManager(this);

        const heading = this.addHeader(body, 'To-Do', 'view-list-bullet-symbolic');
        const {header} = heading;
        this._title = heading.title;

        const add = new St.Button({
            style_class: 'df-add',
            label: '+',
            can_focus: true,
            accessible_name: 'Add a task',
            y_align: Clutter.ActorAlign.CENTER,
        });
        add.connect('clicked', () => new TodoDialog(
            null, () => this._renderLocal(), this._style.dark).open());
        header.add_child(add);

        const scroll = new St.ScrollView({
            style_class: 'df-todo-scroll df-subtle-scroll',
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
        });
        this._scroll = scroll;
        this._rows = new St.BoxLayout({
            style_class: 'df-list', vertical: true, x_expand: true,
        });
        this._rows._delegate = this;
        scroll.set_child(this._rows);
        body.add_child(scroll);
        styleScrollbar(this, scroll);

        this.connect('destroy', () => {
            this._destroyMenus();
            if (this._renderId)
                GLib.source_remove(this._renderId);
            this._renderId = 0;
        });
    }

    render(data) {
        this._destroyMenus();
        this._rowEntries = [];
        this._rows.destroy_all_children();

        const items = data.items ?? [];
        const open = items.filter(item => item.status !== 'done').length;
        this._title.text = open ? `To-Do · ${open} open` : 'To-Do';

        if (!items.length) {
            this._rows.add_child(new St.Label({
                style_class: 'df-empty', text: 'No tasks — use + to add one',
            }));
            return;
        }

        for (const item of items)
            this._addRow(item);
    }

    _addRow(item) {
        const row = new St.BoxLayout({style_class: 'df-todo-row', x_expand: true});

        const handle = new St.Button({
            style_class: 'df-drag-handle',
            can_focus: true,
            accessible_name: `Reorder ${item.text}`,
            child: new St.Icon({icon_name: 'list-drag-handle-symbolic', icon_size: 13}),
        });
        const source = {
            itemId: item.id,
            widget: this,
            getDragActor: () => new Clutter.Clone({source: row}),
            getDragActorSource: () => row,
        };
        handle._delegate = source;
        const draggable = DND.makeDraggable(handle, {
            restoreOnSuccess: true,
            dragActorOpacity: 190,
        });
        draggable.connect('drag-end', () => {
            this._clearDropTarget();
            if (this._renderAfterDrag) {
                this._renderAfterDrag = false;
                this._queueLocalRender();
            }
        });
        row.add_child(handle);

        const textLabel = new St.Label({
            text: item.text ?? '',
            x_expand: true,
            x_align: Clutter.ActorAlign.START,
        });
        textLabel.clutter_text.ellipsize = 3;
        const text = new St.Button({
            style_class: 'df-todo-text',
            child: textLabel,
            x_expand: true,
            x_align: Clutter.ActorAlign.FILL,
            can_focus: true,
            accessible_name: `Rename ${item.text}`,
        });
        text.connect('clicked', () => new TodoDialog(
            item, () => this._renderLocal(), this._style.dark).open());
        row.add_child(text);

        const status = new St.Button({
            style_class: `df-status df-status-${item.status}`,
            label: STATUS_LABELS[item.status] ?? STATUS_LABELS.todo,
            can_focus: true,
            accessible_name: `Status for ${item.text}`,
        });
        const menu = this._statusMenu(status, item);
        status.connect('clicked', () => menu.toggle());
        row.add_child(status);

        const remove = new St.Button({
            style_class: 'df-todo-delete',
            can_focus: true,
            accessible_name: `Delete ${item.text}`,
            child: new St.Icon({icon_name: 'user-trash-symbolic', icon_size: 13}),
        });
        remove.connect('clicked', () => {
            if (removeTodo(item.id))
                this._queueLocalRender();
            else
                this.setStatus('Could not delete the task', false);
        });
        row.add_child(remove);

        this._rows.add_child(row);
        this._rowEntries.push({item, row});
    }

    _statusMenu(button, item) {
        const menu = new PopupMenu.PopupMenu(button, 0.5, St.Side.TOP);
        // BoxPointer actors start at the stage origin until their first open.
        // Shell's own menus explicitly hide them before adding them to
        // uiGroup; without this, menus rebuilt after a status change flash as
        // an orphaned dropdown in the top-left corner.
        menu.actor.hide();
        menu.actor.add_style_class_name('df-status-menu');
        menu.actor.add_style_class_name(
            this._style.dark ? 'df-theme-dark' : 'df-theme-light');
        Main.uiGroup.add_child(menu.actor);
        this._menuManager.addMenu(menu);
        this._menus.push(menu);

        for (const status of TODO_STATUSES) {
            const choice = new PopupMenu.PopupMenuItem(STATUS_LABELS[status]);
            choice.connect('activate', () => {
                // Begin closing before the idle redraw destroys this menu and
                // its source button. This keeps PopupMenuManager's active-menu
                // bookkeeping consistent across the rebuild.
                menu.close();
                if (updateTodo(item.id, {status}))
                    this._queueLocalRender();
                else
                    this.setStatus('Could not update the task', false);
            });
            menu.addMenuItem(choice);
        }
        return menu;
    }

    _destroyMenus() {
        for (const menu of this._menus ?? [])
            menu.destroy();
        this._menus = [];
    }

    handleDragOver(source, _actor, _x, y, _time) {
        if (source?.widget !== this)
            return DND.DragMotionResult.CONTINUE;
        this._dropIndex = this._indexAt(source.itemId, y);
        this._showDropTarget(source.itemId, this._dropIndex);
        return DND.DragMotionResult.MOVE_DROP;
    }

    acceptDrop(source, _actor, _x, _y, _time) {
        if (source?.widget !== this || this._dropIndex === null)
            return false;
        const changed = moveTodo(source.itemId, this._dropIndex);
        this._renderAfterDrag = changed;
        if (!changed)
            this.setStatus('Could not reorder the task', false);
        return changed;
    }

    _indexAt(sourceId, y) {
        let index = 0;
        for (const entry of this._rowEntries) {
            if (entry.item.id === sourceId)
                continue;
            if (y < entry.row.y + entry.row.height / 2)
                return index;
            index++;
        }
        return index;
    }

    _showDropTarget(sourceId, index) {
        this._clearDropTarget(false);
        const rows = this._rowEntries.filter(entry => entry.item.id !== sourceId);
        if (index < rows.length)
            rows[index].row.add_style_class_name('df-drop-before');
        else if (rows.length)
            rows.at(-1).row.add_style_class_name('df-drop-after');
    }

    _clearDropTarget(resetIndex = true) {
        for (const {row} of this._rowEntries) {
            row.remove_style_class_name('df-drop-before');
            row.remove_style_class_name('df-drop-after');
        }
        if (resetIndex)
            this._dropIndex = null;
    }

    _queueLocalRender() {
        if (this._renderId)
            return;
        this._renderId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._renderId = 0;
            this._renderLocal();
            return GLib.SOURCE_REMOVE;
        });
    }

    _renderLocal() {
        const items = loadTodos().map(item => ({
            id: item.id,
            text: item.text ?? '',
            status: TODO_STATUSES.includes(item.status) ? item.status : 'todo',
        }));
        const counts = Object.fromEntries(TODO_STATUSES.map(status => [status, 0]));
        for (const item of items)
            counts[item.status]++;
        this.renderData({items, counts, total: items.length});
        this.setStatus(null, false);
    }
});
