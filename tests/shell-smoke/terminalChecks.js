/** Real Wayland terminals following their cards, including the modal edit layer. */
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {assert, readConfig, sleep, waitFor, writeConfig} from './topBarChecks.js';

function rect(actor) {
    const [x, y] = actor.get_transformed_position();
    const [width, height] = actor.get_transformed_size();
    return {x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height)};
}

function same(a, b) {
    return ['x', 'y', 'width', 'height'].every(key => a[key] === b[key]);
}

function describe(rectangle) {
    return JSON.stringify(Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, rectangle[key]])));
}

function descendants(actor) {
    return actor.get_children().flatMap(child => [child, ...descendants(child)]);
}

function grid(window) {
    const [ok, bytes] = GLib.file_get_contents(`${GLib.getenv('DF_TEST_DIR')}/terminal-${window.get_pid()}.json`);
    return ok ? JSON.parse(new TextDecoder().decode(bytes)) : null;
}

export async function runTerminals({forge, screenshot}) {
    const cardRecord = () => forge._widgets.find(item => item.entry.id === 'terminal-test');
    const terminalRecord = () => forge._terminals._terminals.get('terminal-test');
    assert(await waitFor(() => terminalRecord()?.window && !terminalRecord().cancelWait, 5000),
        'Terminal window did not start');
    let window = terminalRecord().window;
    let process = terminalRecord().client.get_subprocess();
    const peer = forge._terminals._terminals.get('terminal-peer');
    assert(await waitFor(() => peer.window && !peer.cancelWait, 5000), 'Peer terminal did not start');
    const peerRect = peer.window.get_frame_rect();
    let actor = window.get_compositor_private();
    const checkFrame = async () => {
        assert(await waitFor(() => same(rect(cardRecord().widget), window.get_frame_rect()), 2000),
            `Terminal detached: card=${describe(rect(cardRecord().widget))} window=${describe(window.get_frame_rect())}`);
        assert(terminalRecord().window === window && terminalRecord().client.get_subprocess() === process,
            'Layout change restarted the terminal session');
    };
    const checkPreview = () => {
        const card = cardRecord().widget;
        const preview = descendants(card).find(child => child.has_style_class_name?.('df-terminal-preview'));
        const clone = preview && descendants(preview).find(child => child instanceof Clutter.Clone);
        assert(preview?.mapped && same(rect(preview), rect(card)) && clone?.mapped,
            'Terminal preview did not follow the card in edit mode');
        assert(!preview.reactive && !clone.reactive && actor.opacity === 0,
            'Preview intercepted editing or left the original terminal visible');
        assert(clone.source === actor, 'Preview was detached from the live terminal');
        assert(Math.abs(clone.width - actor.width) < 1 && Math.abs(clone.height - actor.height) < 1,
            'Resizing scaled terminal text instead of reflowing it');
    };
    await checkFrame();
    assert(forge._editMode, 'Startup fixture did not enter editing');
    checkPreview();
    await screenshot('terminal-starting-in-edit');
    forge._editMode.close();
    await checkFrame();
    assert(await waitFor(() => actor.opacity === 255), 'Terminal startup left its window hidden');
    await screenshot('terminal-compact');
    const compactGrid = grid(window);
    assert(compactGrid?.columns > 0 && compactGrid.rows > 0, 'Terminal grid was not reported');

    // The actual gesture callbacks must keep the window current BEFORE release.
    forge._enterEditMode();
    await sleep(150);
    checkPreview();
    const {widget, entry} = cardRecord();
    const editing = forge._editMode;
    const pointer = Clutter.get_default_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const movePointer = (x, y) => pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
    const start = rect(widget);
    movePointer(start.x + 80, start.y + 80);
    await sleep(100);
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
    await sleep(100);
    for (let step = 1; step <= 5; step++) {
        movePointer(start.x + 80 + step * 8, start.y + 80 + step * 8);
        await sleep(40);
    }
    assert(rect(widget).x > start.x && rect(widget).y > start.y, 'Pointer drag did not move the terminal card');
    await checkFrame();
    checkPreview();
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
    await sleep(100);
    for (let step = 0; step < 3; step++) {
        editing._move(widget, entry, 48, 24);
        await checkFrame();
        checkPreview();
    }
    await screenshot('terminal-moving');
    editing._settleMove(widget, entry);
    const direction = {horizontal: 1, vertical: 1};
    editing._resize(widget, entry, direction, 264, 120);
    await checkFrame();
    checkPreview();
    assert(await waitFor(() => {
        const sized = grid(window);
        return sized.columns > compactGrid.columns && sized.rows > compactGrid.rows;
    }), 'Terminal rows and columns did not follow the resize');
    editing._settleResize(widget, entry, direction);
    await screenshot('terminal-resizing');

    // Done hands the latest geometry back to the real window.
    editing.close();
    await checkFrame();
    assert(await waitFor(() => actor.opacity === 255), 'Done left the terminal hidden');
    assert(!descendants(widget).some(child => child instanceof Clutter.Clone), 'Done leaked a terminal preview');
    await screenshot('terminal-done');
    assert(same(peerRect, peer.window.get_frame_rect()), 'Moving one terminal moved another');

    // Escape follows the existing edit-mode save-and-close semantics.
    forge._enterEditMode();
    await sleep(100);
    forge._editMode._resize(widget, entry, {horizontal: -1, vertical: -1}, -24, -16);
    await checkFrame();
    checkPreview();
    forge._editMode._settleResize(widget, entry, {horizontal: -1, vertical: -1});
    const keyboard = Clutter.get_default_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(GLib.get_monotonic_time(), Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
    assert(await waitFor(() => !forge._editMode), 'Escape did not finish editing');
    await checkFrame();
    assert(await waitFor(() => actor.opacity === 255), 'Escape left the terminal hidden');
    await sleep(600);  // allow the debounced geometry write to finish

    // Direct card placement and saved layouts also update the same window.
    widget.set_position(120, 144);
    await checkFrame();
    const saved = readConfig();
    const target = saved.widgets.find(item => item.id === entry.id);
    Object.assign(target, {x: 320, y: 160, width: 560, height: 360});
    writeConfig(saved);
    assert(await waitFor(() => same(rect(widget), {x: 320, y: 160, width: 560, height: 360})),
        'Saved layout did not move the existing terminal card');
    await checkFrame();
    await screenshot('terminal-layout');

    // A styling rebuild replaces only the card, retaining the terminal process.
    saved.style.opacity = 0.62;
    writeConfig(saved);
    assert(await waitFor(() => cardRecord().widget !== widget), 'Appearance change did not rebuild the card');
    await checkFrame();
    let rebuilt = cardRecord().widget;
    rebuilt.set_size(160, 90);
    await checkFrame();
    rebuilt.set_size(296, 240);
    await checkFrame();

    // Text size changes increase density without resizing or replacing the shell.
    const fontLayout = readConfig();
    Object.assign(fontLayout.widgets.find(item => item.id === entry.id), {width: 296, height: 240});
    writeConfig(fontLayout);
    assert(await waitFor(() => window.get_frame_rect().width === 296 && grid(window).columns < compactGrid.columns + 2),
        'Compact font fixture did not settle');
    await sleep(150);
    const fontRect = rect(cardRecord().widget);
    const normalGrid = grid(window);
    window.activate(global.get_current_time());
    await sleep(150);
    const key = (symbol, state) => keyboard.notify_keyval(GLib.get_monotonic_time(), symbol, state);
    const press = symbol => {
        key(symbol, Clutter.KeyState.PRESSED);
        key(symbol, Clutter.KeyState.RELEASED);
    };
    key(Clutter.KEY_Control_L, Clutter.KeyState.PRESSED);
    press(Clutter.KEY_minus);
    press(Clutter.KEY_minus);
    key(Clutter.KEY_Control_L, Clutter.KeyState.RELEASED);
    assert(await waitFor(() => grid(window).font_scale === 0.8 &&
        grid(window).columns > normalGrid.columns && grid(window).rows > normalGrid.rows),
    'Smaller text shortcut did not show more rows and columns');
    await checkFrame();
    assert(same(fontRect, rect(cardRecord().widget)), 'Text size changed the card size');
    assert(readConfig().widgets.find(item => item.id === entry.id).options.font_scale === 0.8,
        'Terminal text size was not saved');
    assert(grid(peer.window).font_scale === 1, 'Text size changed a different terminal');
    const controlPress = symbol => {
        key(Clutter.KEY_Control_L, Clutter.KeyState.PRESSED);
        press(symbol);
        key(Clutter.KEY_Control_L, Clutter.KeyState.RELEASED);
    };
    controlPress(Clutter.KEY_0);
    assert(await waitFor(() => grid(window).font_scale === 1), 'Reset text shortcut failed');
    controlPress(Clutter.KEY_equal);
    assert(await waitFor(() => grid(window).font_scale === 1.1), 'Larger text shortcut failed');
    controlPress(Clutter.KEY_0);
    assert(await waitFor(() => grid(window).font_scale === 1), 'Text reset after enlargement failed');
    controlPress(Clutter.KEY_minus);
    controlPress(Clutter.KEY_minus);
    assert(await waitFor(() => grid(window).font_scale === 0.8), 'Smaller text after reset failed');

    // Scrolling is real VTE history; new output must leave the reading position alone.
    const feed = text => GLib.file_set_contents(
        `${GLib.getenv('DF_TEST_DIR')}/terminal-${window.get_pid()}.json.feed`, text);
    feed(Array.from({length: 200}, (_, i) => `History line ${i + 1}\r\n`).join(''));
    assert(await waitFor(() => grid(window).bottom > 180), 'Terminal history was not generated');
    const beforeScroll = grid(window).scroll;
    key(Clutter.KEY_Shift_L, Clutter.KeyState.PRESSED);
    press(Clutter.KEY_Page_Up);
    key(Clutter.KEY_Shift_L, Clutter.KeyState.RELEASED);
    assert(await waitFor(() => grid(window).scroll < beforeScroll), 'Page Up did not scroll terminal history');
    const held = grid(window).scroll;
    feed('Output while reading history\r\n');
    await sleep(250);
    assert(grid(window).scroll === held, 'New output interrupted terminal scrollback');
    movePointer(fontRect.x + 100, fontRect.y + 120);
    pointer.notify_discrete_scroll(GLib.get_monotonic_time(), Clutter.ScrollDirection.UP, Clutter.ScrollSource.WHEEL);
    assert(await waitFor(() => grid(window).scroll < held), 'Mouse wheel did not scroll terminal history');
    await screenshot('terminal-smaller-text-scrollback');
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_SECONDARY, Clutter.ButtonState.PRESSED);
    pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_SECONDARY, Clutter.ButtonState.RELEASED);
    await sleep(200);
    assert(terminalRecord().window === window, 'Terminal menu replaced the hosted window');
    await screenshot('terminal-menu');
    press(Clutter.KEY_Escape);
    await sleep(150);
    rebuilt = cardRecord().widget;

    // Overview and workspace changes must leave the terminals desktop-only.
    assert(window.is_skip_taskbar() && window.is_on_all_workspaces(), 'Terminal became an ordinary app window');
    Main.overview.show();
    await sleep(150);
    Main.overview.hide();
    await sleep(200);
    await checkFrame();

    // A crashed client must release its preview and rebind the new window.
    forge._enterEditMode();
    await sleep(100);
    checkPreview();
    const oldWindow = window;
    const oldProcess = process;
    oldProcess.force_exit();
    assert(await waitFor(() => terminalRecord().window && terminalRecord().window !== oldWindow &&
        !terminalRecord().cancelWait, 6000), 'Terminal did not restart after its client exited');
    window = terminalRecord().window;
    process = terminalRecord().client.get_subprocess();
    actor = window.get_compositor_private();
    assert(process !== oldProcess && cardRecord().widget === rebuilt, 'Restart replaced the wrong component');
    await checkFrame();
    checkPreview();
    forge._editMode.close();
    await checkFrame();
    assert(await waitFor(() => actor.opacity === 255), 'Restarted terminal stayed hidden after editing');
    assert(await waitFor(() => grid(window).font_scale === 0.8), 'Restart lost the saved terminal font size');

    // Stop clients and let IBus settle BEFORE tearing down the compositor.
    const stopping = [...forge._terminals._terminals.values()].map(item => item.client.get_subprocess());
    const stoppedPids = stopping.map(item => item.get_identifier());
    forge._terminals.sync([]);
    assert(await waitFor(() => !global.get_window_actors().some(a => a.meta_window?.get_title() === 'Terminal')),
        `Removing terminal widgets left orphan windows: ${JSON.stringify({
            records: [...forge._terminals._terminals.keys()],
            stoppedPids,
            processes: stopping.map(item => item.get_identifier()),
            windows: global.get_window_actors().map(a => ({title: a.meta_window?.get_title(), pid: a.meta_window?.get_pid(),
                visible: a.visible, opacity: a.opacity, clones: a.has_mapped_clones(),
                compositor: a.meta_window?.get_compositor_private() === a})),
        })}`);
    await sleep(500);
    return ['startup in edit mode', 'compact sizing', 'pointer drag', 'live move', 'live resize and reflow',
        'Done', 'Escape', 'independent terminals', 'direct placement', 'saved layout', 'card rebuild',
        'minimum size', 'overview', 'font shortcuts and density', 'per-widget font persistence',
        'history scrolling', 'output preserves scroll position', 'terminal menu', 'restart during editing', 'client cleanup'];
}
