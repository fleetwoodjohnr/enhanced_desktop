/**
 * Top-bar scenarios for the isolated Shell smoke test.
 *
 * Every assertion drives the real panel through the production extension:
 * configuration changes arrive through config.json exactly as the settings app
 * writes them, the pointer is a virtual device, and windows are real clients.
 */
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));

export function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

const HIDDEN = 0;
const SHOWN = 2;

function configPath() {
    return `${GLib.getenv('XDG_CONFIG_HOME')}/desktop-forge/config.json`;
}

export function readConfig() {
    const [ok, bytes] = GLib.file_get_contents(configPath());
    assert(ok, 'Could not read the smoke-test config');
    return JSON.parse(new TextDecoder().decode(bytes));
}

export function writeConfig(config) {
    GLib.file_set_contents(configPath(), JSON.stringify(config));
}

function box() {
    return Main.layoutManager.panelBox;
}

function describe(controller) {
    return `translation=${box().translation_y} visible=${box().visible} state=${
        controller.state} held=${controller.held} overlap=${controller.overlapping} armed=${
        controller.revealArmed} tracking=${controller.tracking}`;
}

function hiddenAt(offset) {
    return box().translation_y === offset && !box().visible;
}

function shown() {
    return box().translation_y === 0 && box().visible;
}

export async function waitFor(predicate, timeout = 3000) {
    for (let waited = 0; waited < timeout; waited += 50) {
        if (predicate())
            return true;
        await sleep(50);
    }
    return predicate();
}

/** `env` is extra VAR=value entries, such as GDK_BACKEND=x11. */
export async function openWindow(launcher, name = null, env = []) {
    const title = name ? `DF ${name} test` : 'DF repaint test';
    const args = [...(env.length ? ['env', ...env] : []), 'python3',
        `${GLib.getenv('DF_TEST_ROOT')}/tests/shell-smoke/window.py`];
    if (name)
        args.push(name);
    launcher.spawnv(args);
    let window = null;
    await waitFor(() => {
        window = global.get_window_actors().map(actor => actor.meta_window)
            .find(candidate => candidate?.title === title) ?? null;
        return !!window;
    }, 5000);
    assert(window, `Test window "${title}" did not open`);
    // Mutter places a new window once its first frame arrives; moving it
    // before then is undone by that placement.
    await waitFor(() => window.get_compositor_private()?.visible &&
        window.get_frame_rect().width > 0, 3000);
    await sleep(500);
    return window;
}

/** Move a window and wait until Mutter reports it where it was sent. */
export async function place(window, x, y, width, height) {
    window.move_resize_frame(false, x, y, width, height);
    const arrived = await waitFor(() => {
        const rect = window.get_frame_rect();
        return rect.x === x && rect.y === y;
    }, 2000);
    const rect = window.get_frame_rect();
    assert(arrived, `Window did not move to ${x},${y}; it is at ${rect.x},${rect.y}`);
}

function workArea() {
    const area = global.workspace_manager.get_active_workspace()
        .get_work_area_for_monitor(Main.layoutManager.primaryIndex);
    return [area.x, area.y, area.width, area.height].join(',');
}

async function setAnimations(interfaceSettings, enabled) {
    interfaceSettings.set_boolean('enable-animations', enabled);
    await waitFor(() => St.Settings.get().enable_animations === enabled, 1000);
    assert(St.Settings.get().enable_animations === enabled,
        `Could not ${enabled ? 'enable' : 'disable'} animations`);
}

/** The single-monitor pass, run inside the main smoke test. */
export async function runSingleMonitor(ctx) {
    const {forge, movePointer, launcher, uiLauncher, interfaceSettings, margins} = ctx;
    const controller = forge._panelController;
    const monitor = Main.layoutManager.primaryMonitor;
    const widgetActors = forge._widgets.map(record => record.widget);
    const bottomEdge = () => movePointer(monitor.x + monitor.width / 2, monitor.y + monitor.height - 1);
    const away = () => movePointer(monitor.x + monitor.width - 100, monitor.y + monitor.height / 2);

    // A chrome-only change updates the panel without rebuilding any card.
    const config = readConfig();
    config.chrome = {
        top_bar: {
            visibility: 'auto', position: 'bottom', height: 40,
            background: '#336699', opacity: 0.5, foreground_mode: 'auto',
            reveal_delay: 0.1, hide_delay: 0.3, animation_time: 0.2,
        },
        dock: {background: '#112233', opacity: 0.4, foreground_mode: 'auto'},
    };
    writeConfig(config);
    await sleep(900);
    assert(forge._widgets.every((record, index) => record.widget === widgetActors[index]),
        'A chrome-only config change rebuilt desktop widgets');
    assert(box().y === monitor.y + monitor.height - 40 && box().height === 40,
        'Bottom top-bar position or height was not applied');
    assert(hiddenAt(40), `Always-hidden bottom bar did not leave its monitor: ${describe(controller)}`);
    assert(controller.tracking === 'overlay' && controller.revealArmed,
        `A hiding bar must be an overlay with its reveal armed: ${describe(controller)}`);
    assert(margins()?.top === 0 && margins()?.bottom === 40,
        `Auto-hidden bottom bar did not reserve its desktop-icon area: ${JSON.stringify(margins())}`);
    assert(controller.stylesheetLoaded, 'Generated shell chrome stylesheet was not loaded');

    // Reveal on a plain edge hover, stay while the pointer rests, hide after.
    bottomEdge();
    assert(await waitFor(shown, 600), `Pointer at the edge did not reveal the bar: ${describe(controller)}`);
    await sleep(900);
    assert(shown(), `Bar disappeared while the pointer rested on it: ${describe(controller)}`);

    // The reported "random" flashes: an unrelated setting saved while the
    // bar was revealed used to reset the hold and snap it away.
    const transitions = controller.transitions;
    const styled = readConfig();
    styled.style = {...(styled.style ?? {}), opacity: 0.61};
    writeConfig(styled);
    await sleep(900);
    assert(shown() && controller.transitions === transitions,
        `A widget rebuild disturbed the revealed bar: ${describe(controller)}`);
    away();
    assert(await waitFor(() => hiddenAt(40), 1500),
        `Bar did not hide after the pointer left: ${describe(controller)}`);
    assert(controller.revealArmed, 'Bar hid without re-arming its reveal');
    writeConfig(config);
    await sleep(900);

    // Intelligent auto-hide over a maximized window, revealed as an overlay.
    config.chrome.top_bar.visibility = 'intelligent';
    writeConfig(config);
    assert(await waitFor(shown, 1500), `Intelligent bar hid with nothing covering it: ${describe(controller)}`);
    assert(margins()?.bottom === 40, 'Intelligent bottom bar did not keep its desktop-icon area');
    const window = await openWindow(launcher);
    const bridgeWindows = JSON.parse(forge._desktopBridge.Windows());
    const bridgeWindow = bridgeWindows.find(candidate => candidate.title === 'DF repaint test');
    assert(bridgeWindow?.desktop_id, 'Desktop bridge did not publish a stable app ID for the test window');
    assert(forge._desktopBridge.Activate(bridgeWindow.desktop_id),
        'Desktop bridge could not activate the test application');
    const bridgeProbe = uiLauncher.spawnv(
        ['python3', `${GLib.getenv('DF_TEST_ROOT')}/tests/clive_shell_smoke.py`]);
    const bridgeOutput = await new Promise((resolve, reject) => {
        bridgeProbe.communicate_utf8_async(null, null, (process, result) => {
            try {
                resolve(process.communicate_utf8_finish(result)[1]);
            } catch (error) {
                reject(error);
            }
        });
    });
    assert(bridgeProbe.get_successful(), `CLIVE could not read the Shell bridge over D-Bus: ${bridgeOutput}`);
    window.activate(global.get_current_time());
    await sleep(150);
    const overlayArea = workArea();
    window.maximize();
    assert(await waitFor(() => hiddenAt(40), 1500),
        `Intelligent bar did not hide for a maximized window: ${describe(controller)}`);
    bottomEdge();
    assert(await waitFor(shown, 800), `Intelligent bar did not reveal over the maximized window: ${describe(controller)}`);
    assert(workArea() === overlayArea, 'Revealing the intelligent bar resized the work area');
    away();
    assert(await waitFor(() => hiddenAt(40), 1500), `Bar did not hide again over the maximized window: ${describe(controller)}`);

    // Keyboard-opened menus: Shell refuses to toggle an unmapped indicator, so
    // the bar has to arrive before the menu does.
    const dateMenu = Main.panel.statusArea.dateMenu?.menu;
    Main.panel.toggleCalendar();
    await sleep(300);
    assert(dateMenu?.isOpen && shown(), `Super+V did not open the calendar over a hidden bar: ${describe(controller)}`);
    dateMenu.close();
    await sleep(100);
    away();
    assert(await waitFor(() => hiddenAt(40), 1500), `Bar stayed after its menu closed: ${describe(controller)}`);

    // Fullscreen apps keep the bar away unless the user asked otherwise.
    window.make_fullscreen();
    await waitFor(() => window.is_fullscreen(), 1500);
    await sleep(300);
    assert(hiddenAt(40) && !controller.revealArmed,
        `Fullscreen did not disarm the reveal: ${describe(controller)}`);
    bottomEdge();
    await sleep(500);
    assert(hiddenAt(40), `The bar revealed over a fullscreen window: ${describe(controller)}`);
    config.chrome.top_bar.reveal_in_fullscreen = true;
    writeConfig(config);
    await sleep(700);
    away();
    await sleep(200);
    bottomEdge();
    assert(await waitFor(shown, 800), `Reveal over fullscreen did not work when enabled: ${describe(controller)}`);
    away();
    await waitFor(() => hiddenAt(40), 1500);
    window.unmake_fullscreen();
    config.chrome.top_bar.reveal_in_fullscreen = false;
    writeConfig(config);
    await sleep(700);

    // The lock screen keeps the controller, shows the bar for its own
    // indicators, removes everything personal, and resizes nothing.
    const lockedArea = workArea();
    Main.sessionMode.pushMode('unlock-dialog');
    await sleep(500);
    assert(shown(), `The lock screen hid its own top bar: ${describe(controller)}`);
    assert(forge._widgets.length === 0 && !forge._desktopBridge && !forge._clive,
        'Desktop cards, CLIVE or the D-Bus bridge survived onto the lock screen');
    assert(workArea() === lockedArea, 'Locking changed the work area and resized windows');
    Main.sessionMode.popMode('unlock-dialog');
    assert(await waitFor(() => forge._widgets.length > 0, 3000), 'Cards did not return after unlocking');
    assert(await waitFor(() => hiddenAt(40), 1500),
        `Unlocking did not restore the hidden bar over the maximized window: ${describe(controller)}`);
    assert(workArea() === lockedArea, 'Unlocking changed the work area');

    // Animated pass: a window dragged back and forth across the strip must not
    // make the bar churn, and one sitting still must be decided once.
    window.unmaximize();
    await setAnimations(interfaceSettings, true);
    await place(window, 200, 100, 320, 300);
    assert(await waitFor(shown, 2000), `Bar did not return once nothing covered it: ${describe(controller)}`);
    const before = controller.transitions;
    for (let i = 0; i < 40; i++) {
        const y = i % 2 ? 100 : monitor.height - 150;
        window.move_resize_frame(false, 200, y, 320, 300);
        await sleep(30);
    }
    window.move_resize_frame(false, 200, 100, 320, 300);
    await sleep(900);
    assert(shown() && controller.transitions === before,
        `A window moving across the bar made it churn ${controller.transitions - before} times`);

    // Overlap follows geometry, not focus, in "any window" mode ...
    const second = await openWindow(launcher, 'second');
    await place(second, 80, monitor.height - 300, 400, 300);
    window.activate(global.get_current_time());
    assert(await waitFor(() => hiddenAt(40), 2000),
        `A covering window was ignored because another app had focus: ${describe(controller)}`);
    // ... and follows focus in "focused app" mode.
    config.chrome.top_bar.hide_when = 'focused';
    writeConfig(config);
    assert(await waitFor(shown, 2000), `Focused mode hid the bar for an unfocused window: ${describe(controller)}`);
    second.activate(global.get_current_time());
    assert(await waitFor(() => hiddenAt(40), 2000), `Focused mode ignored the focused covering window: ${describe(controller)}`);
    config.chrome.top_bar.hide_when = 'any';
    writeConfig(config);
    second.delete(global.get_current_time());
    assert(await waitFor(shown, 2500), `Bar stayed hidden after the covering window closed: ${describe(controller)}`);
    await setAnimations(interfaceSettings, false);
    window.delete(global.get_current_time());
    await sleep(400);
}

/** The two-monitor pass: run with `tests/run_shell_smoke.sh --top-bar`. */
export async function runMultiMonitor(ctx) {
    const {forge, movePointer, launcher} = ctx;
    const controller = forge._panelController;
    const monitors = Main.layoutManager.monitors;
    assert(monitors.length >= 2, `Expected two virtual monitors, found ${monitors.length}`);
    const primary = Main.layoutManager.primaryMonitor;
    const other = monitors.find(monitor => monitor !== primary);
    const config = readConfig();
    config.chrome = {top_bar: {visibility: 'intelligent', position: 'top', height: 32,
        reveal_delay: 0.1, hide_delay: 0.3}};
    writeConfig(config);
    await sleep(900);
    movePointer(primary.x + primary.width / 2, primary.y + primary.height / 2);
    assert(await waitFor(shown, 1500), `Top bar hid with nothing covering it: ${describe(controller)}`);
    assert(Main.layoutManager._rightPanelBarrier, 'Shown top bar lost its stock right-edge barrier');

    // A window from the other monitor that straddles into the bar counts.
    const window = await openWindow(launcher);
    await place(window, other.x - 150, primary.y + 10, 400, 300);
    assert(await waitFor(() => hiddenAt(-32), 2000),
        `A window straddling in from the other monitor did not hide the bar: ${describe(controller)}`);
    assert(!Main.layoutManager._rightPanelBarrier,
        'A hidden bar kept the barrier that stops the pointer crossing displays');
    // Fully off-screen and not visible, so nothing leaks onto the neighbour.
    const [x, y] = box().get_transformed_position();
    assert(!box().visible && y + box().height <= primary.y,
        `The hidden bar could still draw at ${x},${y}: ${describe(controller)}`);
    // The reveal edge is the primary's top edge, not the other monitor's.
    movePointer(other.x + other.width / 2, other.y);
    await sleep(400);
    assert(hiddenAt(-32), 'The other monitor\'s top edge revealed the primary bar');
    movePointer(primary.x + primary.width / 2, primary.y);
    assert(await waitFor(shown, 800), `The primary's top edge did not reveal the bar: ${describe(controller)}`);
    assert(Main.layoutManager._rightPanelBarrier, 'The revealed bar did not restore its barrier');
    movePointer(primary.x + primary.width / 2, primary.y + primary.height / 2);
    assert(await waitFor(() => hiddenAt(-32), 1500), `Bar did not hide after the reveal: ${describe(controller)} window=${
        JSON.stringify((r => [r.x, r.y, r.width, r.height])(window.get_frame_rect()))} monitor=${
        window.get_monitor()} type=${window.get_window_type()} min=${window.minimized} showing=${
        window.showing_on_its_workspace()} onws=${window.located_on_workspace(
            global.workspace_manager.get_active_workspace())}`);
    await place(window, other.x + 100, other.y + 100, 400, 300);
    assert(await waitFor(shown, 2000), `Moving the window fully onto the other monitor did not return the bar: ${describe(controller)}`);

    // Tiling with workspaces on the primary display only (GNOME's default):
    // windows on the other display are on every workspace, and tile there
    // in one group of their own.
    const mutter = new Gio.Settings({schema_id: 'org.gnome.mutter'});
    const onlyPrimary = mutter.get_boolean('workspaces-only-on-primary');
    mutter.set_boolean('workspaces-only-on-primary', true);
    const desktopJson = `${GLib.getenv('XDG_CONFIG_HOME')}/desktop-forge/desktop.json`;
    GLib.file_set_contents(desktopJson, JSON.stringify({version: 1, tiling: {enabled: true}}));
    const tiling = () => forge._desktopSettings?.module('tiling');
    const key = `all:${other.index}`;
    const area = Main.layoutManager.getWorkAreaForMonitor(other.index);
    const filled = await waitFor(() => tiling()?.groups[key] === 1 && (r =>
        r.x === area.x && r.y === area.y && r.width === area.width && r.height === area.height)(window.get_frame_rect()),
    3000);
    assert(filled, `A window on the other display did not tile there: ${JSON.stringify(tiling()?.groups)} ${
        JSON.stringify((r => [r.x, r.y, r.width, r.height])(window.get_frame_rect()))}`);
    GLib.file_set_contents(desktopJson, JSON.stringify({version: 1}));
    mutter.set_boolean('workspaces-only-on-primary', onlyPrimary);
    await sleep(300);
    window.delete(global.get_current_time());
    await sleep(300);
}
