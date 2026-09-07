import GLib from 'gi://GLib';

import {
    addReminder, addTodo, completeReminder, loadReminders, loadTodos,
    moveTodo, removeTodo, updateTodo, watchJson, writeJson, readJson,
} from '../extension/store.js';

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

const reminder = addReminder('Desktop reminder', new Date(2026, 8, 4, 12, 30), 'weekly');
assert(reminder !== null, 'could not add a reminder');
assert(loadReminders().length === 1, 'reminder was not persisted');
assert(completeReminder(reminder.id), 'could not complete a reminder');
assert(loadReminders()[0].done === true, 'completion was not persisted');

const first = addTodo('First');
const second = addTodo('Second');
assert(first !== null && second !== null, 'could not add To-Do items');
assert(updateTodo(first.id, {text: 'Renamed', status: 'blocked'}),
    'could not update text and status');
assert(moveTodo(second.id, 0), 'could not reorder To-Do items');
assert(loadTodos().map(item => item.text).join(',') === 'Second,Renamed',
    'manual order was not persisted');
assert(removeTodo(first.id), 'could not delete a To-Do item');
assert(loadTodos().length === 1, 'To-Do deletion was not persisted');

const statePath = `${GLib.get_user_data_dir()}/new-parent/state/news.json`;
let seen = null;
let notifications = 0;
const monitor = watchJson(statePath, () => {
    seen = readJson(statePath);
    notifications++;
});
const context = GLib.MainContext.default();
for (let version = 1; version <= 3; version++) {
    assert(writeJson(statePath, {version}), 'Could not atomically write news state');
    const deadline = GLib.get_monotonic_time() + 1000000;
    while (seen?.version !== version && GLib.get_monotonic_time() < deadline) {
        while (context.pending())
            context.iteration(false);
        GLib.usleep(1000);
    }
    assert(seen?.version === version, `Lost atomic news update ${version}`);
}
const before = notifications;
writeJson(`${statePath}.unrelated`, {ignored: true});
const deadline = GLib.get_monotonic_time() + 100000;
while (GLib.get_monotonic_time() < deadline) {
    while (context.pending())
        context.iteration(false);
    GLib.usleep(1000);
}
assert(notifications === before, 'Unrelated file caused a news refresh');
monitor.cancel();
