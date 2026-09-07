/**
 * The extension's side of the JSON contract described in desktop_forge/config.py.
 *
 * Both the GTK app and this extension write config.json, and now both write
 * the reminder store as well, so every write here is atomic and every write
 * that changes a shared file is a read-modify-write of the file on disk rather
 * than of a copy held in memory. That is what stops a widget drag from
 * clobbering a setting the app changed a moment earlier.
 */
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const CONFIG_PATH = GLib.build_filenamev(
    [GLib.get_user_config_dir(), 'desktop-forge', 'config.json']);
export const STATE_DIR = GLib.build_filenamev(
    [GLib.get_user_data_dir(), 'desktop-forge', 'state']);
export const REMINDERS_PATH = GLib.build_filenamev(
    [GLib.get_user_data_dir(), 'desktop-forge', 'reminders.json']);
export const TODOS_PATH = GLib.build_filenamev(
    [GLib.get_user_data_dir(), 'desktop-forge', 'todos.json']);

export const TODO_STATUSES = ['todo', 'in_progress', 'blocked', 'done'];

/** Serialised text of the last write this process made, keyed by path. */
const lastWritten = new Map();

export function readText(path) {
    try {
        const [ok, contents] = Gio.File.new_for_path(path).load_contents(null);
        return ok ? new TextDecoder().decode(contents) : null;
    } catch {
        return null;
    }
}

export function readJson(path) {
    const text = readText(path);
    if (text === null)
        return null;
    try {
        return JSON.parse(text);
    } catch {
        // Missing (the daemon has not run yet) or mid-write. Either way the
        // caller renders a placeholder rather than throwing inside the shell.
        return null;
    }
}

/** Watch atomic replacements, including a state file not created yet. */
export function watchJson(path, onChanged) {
    const file = Gio.File.new_for_path(path);
    const directory = file.get_parent();
    try {
        directory.make_directory_with_parents(null);
    } catch {
        // Usually already present.
    }
    const monitor = directory.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
    monitor.connect('changed', (_monitor, changed, other) => {
        if ([changed, other].some(candidate => candidate?.get_basename() === file.get_basename()))
            onChanged();
    });
    return monitor;
}

/**
 * Write JSON the same way config.write_json() does on the Python side.
 *
 * The extension and daemon file-watch these paths, so a reader that
 * catches a half-written file sees invalid JSON and blanks the widget.
 * Writing to a temp file in the same directory and renaming it into place
 * makes a reader see either the old file or the new one, never a partial one.
 */
export function writeJson(path, payload) {
    // Formatted exactly as config.write_json() does on the Python side, so a
    // file this process wrote is recognisable byte for byte by isOwnWrite().
    const text = JSON.stringify(payload, null, 2);
    const target = Gio.File.new_for_path(path);

    try {
        target.get_parent()?.make_directory_with_parents(null);
    } catch {
        // Already there, which is the normal case.
    }

    const tmp = Gio.File.new_for_path(`${path}.tmp-${GLib.uuid_string_random()}`);
    try {
        tmp.replace_contents(
            new TextEncoder().encode(text),
            null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        tmp.move(target, Gio.FileCopyFlags.OVERWRITE, null, null);
        lastWritten.set(path, text);
        return true;
    } catch (error) {
        try {
            tmp.delete(null);
        } catch {
            // Nothing useful to do if even the cleanup fails.
        }
        logError(error, `desktop-forge: could not write ${path}`);
        return false;
    }
}

/**
 * True when the file's current contents are exactly what this process last
 * wrote. The config file monitor uses it to ignore its own writes, which would
 * otherwise tear down and rebuild every widget in the middle of a drag.
 */
export function isOwnWrite(path) {
    const previous = lastWritten.get(path);
    return previous !== undefined && previous === readText(path);
}

// -- reminders -------------------------------------------------------------

/**
 * Local time, with no zone marker.
 *
 * providers/reminders.py parses this with datetime.fromisoformat() and
 * compares the result against a naive datetime.now(). An offset or a trailing
 * Z here would produce an aware datetime and make that comparison raise
 * TypeError inside the daemon's once-a-second reminder tick, so the format
 * has to stay naive.
 */
export function localIso(date) {
    const pad = n => `${n}`.padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
        `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function loadReminders() {
    const payload = readJson(REMINDERS_PATH) ?? {};
    return Array.isArray(payload.reminders) ? payload.reminders : [];
}

function saveReminders(reminders) {
    return writeJson(REMINDERS_PATH, {version: 1, reminders});
}

/** Matches the record providers/reminders.py:add() creates. */
export function addReminder(text, due, repeat) {
    const reminder = {
        id: GLib.uuid_string_random().replaceAll('-', '').slice(0, 12),
        text: text.trim(),
        due: localIso(due),
        repeat: ['none', 'daily', 'weekly', 'monthly'].includes(repeat) ? repeat : 'none',
        done: false,
        notified: false,
    };
    const reminders = loadReminders();
    reminders.push(reminder);
    return saveReminders(reminders) ? reminder : null;
}

export function completeReminder(id) {
    const reminders = loadReminders();
    const reminder = reminders.find(r => r.id === id);
    if (!reminder)
        return false;
    // Marking done rather than deleting keeps the record for a repeating
    // reminder, which the daemon re-arms on its next tick.
    reminder.done = true;
    return saveReminders(reminders);
}

// -- to-do items ----------------------------------------------------------

export function loadTodos() {
    const payload = readJson(TODOS_PATH) ?? {};
    return Array.isArray(payload.items) ? payload.items.filter(item => item && typeof item === 'object') : [];
}

function saveTodos(items) {
    return writeJson(TODOS_PATH, {version: 1, items});
}

export function addTodo(text) {
    const clean = text.trim();
    if (!clean)
        return null;
    const item = {
        id: GLib.uuid_string_random().replaceAll('-', '').slice(0, 12),
        text: clean,
        status: 'todo',
    };
    const items = loadTodos();
    items.push(item);
    return saveTodos(items) ? item : null;
}

export function updateTodo(id, changes) {
    const items = loadTodos();
    const item = items.find(candidate => candidate.id === id);
    if (!item)
        return false;

    if (Object.hasOwn(changes, 'text')) {
        const text = `${changes.text ?? ''}`.trim();
        if (!text)
            return false;
        item.text = text;
    }
    if (TODO_STATUSES.includes(changes.status))
        item.status = changes.status;
    return saveTodos(items);
}

export function removeTodo(id) {
    const items = loadTodos();
    const remaining = items.filter(item => item.id !== id);
    return remaining.length !== items.length && saveTodos(remaining);
}

/** Move an item to an index in the list after the source item is removed. */
export function moveTodo(id, targetIndex) {
    const items = loadTodos();
    const sourceIndex = items.findIndex(item => item.id === id);
    if (sourceIndex < 0)
        return false;
    const [item] = items.splice(sourceIndex, 1);
    const index = Math.max(0, Math.min(Math.trunc(targetIndex), items.length));
    items.splice(index, 0, item);
    return saveTodos(items);
}
