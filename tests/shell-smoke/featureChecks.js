import Clutter from 'gi://Clutter';
import GdkPixbuf from 'gi://GdkPixbuf';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Graphene from 'gi://Graphene';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {WindowMenu} from 'resource:///org/gnome/shell/ui/windowMenu.js';

import {assert, openWindow, place, readConfig, sleep, waitFor, writeConfig} from './topBarChecks.js';

function desktopPath() {
    return `${GLib.getenv('XDG_CONFIG_HOME')}/desktop-forge/desktop.json`;
}

function writeDesktop(document) {
    GLib.file_set_contents(desktopPath(), JSON.stringify({version: 1, ...document}));
}

async function capture(name) {
    const path = `${GLib.getenv('DF_TEST_DIR')}/${name}.png`;
    const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Shell.Screenshot().screenshot(false, stream);
    stream.close(null);
    return GdkPixbuf.Pixbuf.new_from_file(path);
}

function pixel(pixbuf, x, y) {
    const scale = pixbuf.get_width() / global.stage.width;
    const px = Math.round(x * scale);
    const py = Math.round(y * scale);
    const offset = py * pixbuf.get_rowstride() + px * pixbuf.get_n_channels();
    const bytes = pixbuf.get_pixels();
    return [bytes[offset], bytes[offset + 1], bytes[offset + 2]];
}

const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

/**
 * The desktop.json modules against real windows: effects drawn where they
 * should be, rules applied on open, the floating bar's work area, and none
 * of it running on the lock screen.
 */
export async function runFeatures({forge, launcher}) {
    const monitor = Main.layoutManager.primaryMonitor;
    const results = {};
    const settings = () => forge._desktopSettings;
    assert(settings(), 'The customization loader did not start with the session');

    // A rule sizes and centres the window as it opens.
    writeDesktop({rules: {list: [{name: 'test', enabled: true,
        match: {app: '', wm_class: '', title: 'DF effects test', title_regex: false, type: 'any'},
        actions: {mode: 'default', workspace: 0, monitor: -1, center: true, maximize: false,
            fullscreen: false, above: true, sticky: false, opacity: 1, no_effects: false,
            width: 420, height: 360}}]}});
    await waitFor(() => settings().active.includes('window-rules'), 2000);
    const window = await openWindow(launcher, 'effects');
    const area = window.get_work_area_current_monitor();
    const placed = await waitFor(() => {
        const frame = window.get_frame_rect();
        return frame.width === 420 && frame.height === 360 &&
            Math.abs(frame.x - (area.x + (area.width - 420) / 2)) <= 1;
    }, 3000);
    const frame0 = window.get_frame_rect();
    assert(placed, `The rule did not size and centre the window: ${frame0.x},${frame0.y} ${frame0.width}x${frame0.height}`);
    assert(window.is_above(), 'The rule did not keep the window on top');
    results.rules = 'sized, centred and kept on top on open';

    // Effects: nothing attached while everything is neutral.
    writeDesktop({windows: {corner_radius: 0, border_width: 0, active_opacity: 1, inactive_opacity: 1},
        rules: {list: []}});
    await sleep(400);
    window.activate(global.get_current_time());
    await sleep(300);
    const effects = settings().module('window-effects');
    assert(effects && !effects.styled.includes(window), 'A neutral setup still attached a window effect');
    const frame = window.get_frame_rect();
    const corner = [frame.x + 3, frame.y + frame.height - 3];
    const edge = [frame.x + 1, frame.y + Math.round(frame.height / 2)];
    const before = await capture('effects-off');
    const cornerBefore = pixel(before, ...corner);

    writeDesktop({windows: {corner_radius: 24, border_width: 4, border_accent: false,
        border_color: '#ff0000', active_opacity: 1, inactive_opacity: 1, dim_inactive: 0}});
    assert(await waitFor(() => effects.styled.includes(window), 2000),
        'Corners and a border did not attach the window effect');
    await sleep(400);
    const after = await capture('effects-on');
    const cornerAfter = pixel(after, ...corner);
    const edgeAfter = pixel(after, ...edge);
    assert(distance(cornerBefore, cornerAfter) > 60,
        `The bottom-left corner was not cut away: ${cornerBefore} then ${cornerAfter}`);
    assert(edgeAfter[0] > 190 && edgeAfter[1] < 90 && edgeAfter[2] < 90,
        `The focus border is not red at the window's edge: ${edgeAfter}`);
    // A gradient border runs from the first colour on the left to the
    // second on the right.
    writeDesktop({windows: {corner_radius: 24, border_width: 4, border_accent: false,
        border_color: '#ff0000', border_gradient: true, border_color2: '#0000ff',
        active_opacity: 1, inactive_opacity: 1, dim_inactive: 0}});
    await sleep(600);
    const graded = await capture('effects-gradient');
    const middle = frame.y + Math.round(frame.height / 2);
    const leftEdge = pixel(graded, frame.x + 1, middle);
    const rightEdge = pixel(graded, frame.x + frame.width - 2, middle);
    assert(leftEdge[0] > leftEdge[2] + 80 && rightEdge[2] > rightEdge[0] + 80,
        `The gradient border does not run red to blue: ${leftEdge} … ${rightEdge}`);
    results.effects = `corner ${cornerBefore} → ${cornerAfter}, edge ${edgeAfter}, gradient ${leftEdge} → ${rightEdge}`;

    // Fullscreen windows are left alone so they can be shown directly.
    window.make_fullscreen();
    assert(await waitFor(() => !effects.styled.includes(window), 2000),
        'A fullscreen window kept its effect');
    window.unmake_fullscreen();
    await waitFor(() => effects.styled.includes(window), 2000);

    // Banners, wallpaper and the floating bar.
    writeDesktop({
        windows: {corner_radius: 24, border_width: 4, border_accent: false, border_color: '#ff0000'},
        notifications: {position: 'right', timeout: 7, opacity: 0.8},
        wallpaper: {blur: 20, dim: 0.3},
        top_bar: {floating: true, margin: 8, radius: 12, blur: true, show_activities: false,
            clock_position: 'right'},
    });
    const box = Main.layoutManager.panelBox;
    assert(await waitFor(() => box.height === 40, 2000), `The floating bar's box is ${box.height}px, not 40`);
    const inset = await waitFor(() => {
        const [x, y] = Main.panel.get_transformed_position();
        return Main.panel.width === monitor.width - 16 && x === monitor.x + 8 && y === monitor.y + 8;
    }, 2000);
    assert(inset, `The floating bar is not inset: ${Main.panel.get_transformed_position()} ${Main.panel.width}`);
    const workTop = () => global.workspace_manager.get_active_workspace().get_work_area_for_monitor(
        monitor.index).y;
    assert(workTop() === monitor.y + 40, `The work area does not clear the floating bar: ${workTop()}`);
    assert(Main.messageTray.bannerAlignment === Clutter.ActorAlign.END, 'Banners did not move right');
    assert(settings().module('notifications').timeout === 7000, 'The banner time was not applied');
    const background = Main.layoutManager._bgManagers[0].backgroundActor;
    assert(background.get_effect('desktop-forge-wallpaper-blur') &&
        background.get_effect('desktop-forge-wallpaper-dim'), 'The wallpaper was not blurred and dimmed');
    assert(!Main.panel.statusArea.activities.container.visible, 'The workspace indicator is still shown');
    assert(Main.panel.statusArea.dateMenu.container.get_parent() === Main.panel._rightBox,
        'The clock did not move right');
    await capture('floating-bar');
    results.chrome = 'floating bar with its strut, banners, wallpaper, clock and indicator';

    // Blur behind the bar: with a see-through bar over the striped test
    // wallpaper, the stripes' hard edges under it turn into gradients. The
    // squared step between neighbours measures that (a plain sum of steps
    // barely changes when an edge is only spread out).
    const chrome = readConfig();
    chrome.chrome = {top_bar: {opacity: 0.25}};
    writeConfig(chrome);
    const roughness = pixbuf => {
        const y = monitor.y + 8 + 16;
        let total = 0;
        for (let x = monitor.x + 120; x < monitor.x + 700; x += 2)
            total += distance(pixel(pixbuf, x, y), pixel(pixbuf, x + 2, y)) ** 2;
        return total;
    };
    const barValues = on => ({top_bar: {floating: true, margin: 8, radius: 12, blur: on,
        show_activities: false, clock_position: 'right'}});
    writeDesktop(barValues(false));
    await sleep(700);
    const sharp = roughness(await capture('bar-sharp'));
    writeDesktop(barValues(true));
    await sleep(900);
    const soft = roughness(await capture('bar-blurred'));
    assert(soft < sharp * 0.4, `The bar blur did not soften the wallpaper behind it: ${sharp} → ${soft}`);
    chrome.chrome = {};
    writeConfig(chrome);
    results.bar_blur = `edge roughness under the bar ${Math.round(sharp)} → ${Math.round(soft)}`;

    // Animations: Shell's handlers are taken over only for custom styles,
    // and windows still open, close and end up fully visible.
    writeDesktop({animations: {speed: 1, window_open: 'pop', window_close: 'fade', minimize: 'scale',
        duration: 150, workspace: 'fast'}});
    const animations = await waitFor(() => settings().module('animations')?.takenOver, 2000);
    assert(animations, 'Custom animation styles did not take over the window effects');
    const second = await openWindow(launcher, 'animated');
    const actor = second.get_compositor_private();
    assert(await waitFor(() => actor.opacity === 255 && actor.scale_x === 1, 2000),
        `An opened window did not finish appearing: opacity ${actor.opacity}, scale ${actor.scale_x}`);
    second.minimize();
    assert(await waitFor(() => !actor.visible, 2000), 'Minimizing did not hide the window');
    second.unminimize();
    assert(await waitFor(() => actor.visible && actor.opacity === 255, 2000), 'Restoring did not show it');
    const before2 = global.get_window_actors().length;
    second.delete(global.get_current_time());
    assert(await waitFor(() => global.get_window_actors().length === before2 - 1, 3000), 'Closing failed');
    await sleep(400);
    const ghosts = global.window_group.get_children().filter(child => child.constructor.name === 'ClutterActor' &&
        child.content && !child.meta_window);
    assert(ghosts.length === 0, `Close or minimize snapshots were left behind: ${ghosts.length}`);
    writeDesktop({animations: {speed: 1, window_open: 'gnome', window_close: 'gnome', minimize: 'gnome'}});
    assert(await waitFor(() => !settings().module('animations').takenOver, 2000),
        'Going back to GNOME animations did not hand the handlers back');
    results.animations = 'open, minimize, restore and close with custom styles; handed back';

    // Tiling: windows share the work area by the layout, with gaps, and
    // close up when one goes.
    const {keptInside, layoutRects, snapRect, sameRect} = await import(GLib.filename_to_uri(
        `${GLib.getenv('DF_TEST_ROOT')}/extension/tilingLogic.js`, null));
    window.unmake_above();  // the first rule made it always-on-top, which floats it
    const tilingValues = {enabled: true, layout: 'columns', gaps_inner: 10, gaps_outer: 10,
        smart_gaps: false, master_ratio: 0.55, new_window: 'end', float_dialogs: true, float_fixed: true};
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, tiling: tilingValues, rules: {list: []}});
    const workArea = () => global.workspace_manager.get_active_workspace().get_work_area_for_monitor(monitor.index);
    const expect = count => layoutRects('columns', count, workArea(),
        {inner: 10, outer: 10, smart: false});
    const framed = (candidate, target) => sameRect(candidate.get_frame_rect(), target, 1);
    assert(await waitFor(() => framed(window, expect(1)[0]), 3000),
        `A lone window was not tiled: ${JSON.stringify(window.get_frame_rect())} vs ${JSON.stringify(expect(1)[0])}`);
    // Dragged by hand, a lone window stays where it is dropped.
    const full = expect(1)[0];
    const moved = {x: full.x + 40, y: full.y + 40, width: Math.round(full.width / 2), height: Math.round(full.height / 2)};
    window.move_resize_frame(true, moved.x, moved.y, moved.width, moved.height);
    settings().module('tiling')._grabEnded(window, Meta.GrabOp.MOVING);
    assert(await waitFor(() => framed(window, moved), 2000),
        `A lone window did not take its dragged place: ${JSON.stringify(window.get_frame_rect())}`);
    await sleep(300);
    assert(framed(window, moved) && !framed(window, full),
        `A dragged lone window snapped back: ${JSON.stringify(window.get_frame_rect())}`);
    // Tiling turns GNOME's edge snapping off, so ours runs with no snap
    // settings of its own, and takes a lone window.
    assert(settings().module('snap')._active, 'Snapping is off while tiling is on');
    window.activate(global.get_current_time());
    settings().module('snap').snapFocused('left');
    assert(await waitFor(() => framed(window, snapRect('left', workArea(), 0)), 2000),
        `A lone window did not snap to the left half: ${JSON.stringify(window.get_frame_rect())}`);
    const tiled = await openWindow(launcher, 'tiled');
    const both = await waitFor(() => framed(window, expect(2)[0]) && framed(tiled, expect(2)[1]), 4000);
    assert(both, `Two windows were not arranged in columns: ${JSON.stringify(window.get_frame_rect())} ${
        JSON.stringify(tiled.get_frame_rect())}`);
    const tilingModule = settings().module('tiling');
    // Focus View, from the window menu: the window floats above, centered,
    // and the other fills the screen; choosing it again puts it back.
    const menu = new WindowMenu(window, Main.layoutManager.dummyCursor);
    const labels = menu._getMenuItems().map(item => item.label?.text);
    menu.destroy();
    assert(labels[0] === 'Focus View', `The window menu has no Focus View first: ${labels.join(', ')}`);
    tilingModule.focusView(window);
    const centered = r => Math.abs(r.x + r.width / 2 - (full.x + full.width / 2)) <= 2 &&
        Math.abs(r.y + r.height / 2 - (full.y + full.height / 2)) <= 2;
    assert(await waitFor(() => window.is_above() && centered(window.get_frame_rect()) &&
        framed(tiled, expect(1)[0]), 3000),
    `Focus View did not float the window over the other: ${JSON.stringify(window.get_frame_rect())} ${
        JSON.stringify(tiled.get_frame_rect())}`);
    tilingModule.focusView(window);
    assert(await waitFor(() => !window.is_above() && framed(window, expect(2)[0]) && framed(tiled, expect(2)[1]), 3000),
        `Leaving Focus View did not re-tile: ${JSON.stringify(window.get_frame_rect())} ${
            JSON.stringify(tiled.get_frame_rect())}`);
    window.activate(global.get_current_time());
    await sleep(200);
    const glideActor = window.get_compositor_private();
    let glided = false;
    glideActor.connectObject('notify::translation-x', () => {
        glided ||= glideActor.translation_x !== 0;
    }, tilingModule);
    tilingModule.swap('right');
    assert(await waitFor(() => framed(window, expect(2)[1]) && framed(tiled, expect(2)[0]), 3000),
        'Swapping did not trade the two windows\' places');
    assert(await waitFor(() => glided && glideActor.translation_x === 0, 2000),
        `The swapped window did not glide into place: ${glided}, ${glideActor.translation_x}`);
    glideActor.disconnectObject(tilingModule);
    // Maximizing a tiled window covers its tile; the other stays put, and
    // unmaximizing puts it back.
    tiled.maximize();
    await sleep(600);
    const box4 = r => `${r.x},${r.y} ${r.width}x${r.height}`;
    assert(tiled.is_maximized() && framed(window, expect(2)[1]),
        `Maximizing a tiled window was undone or moved the other: ${tiled.is_maximized()} ${
            box4(window.get_frame_rect())} vs ${box4(expect(2)[1])}, tiled ${box4(tiled.get_frame_rect())}`);
    tiled.unmaximize();
    assert(await waitFor(() => framed(tiled, expect(2)[0]), 3000), 'Unmaximizing did not return the window to its tile');
    // A window floated with the shortcut, dragged by its title bar to the
    // left edge, fills the left half; floating it again re-tiles it.
    window.activate(global.get_current_time());
    await sleep(200);
    tilingModule.toggleFloating();
    assert(await waitFor(() => framed(tiled, expect(1)[0]), 3000), 'Floating a window did not free its tile');
    const dragger = Clutter.get_default_backend().get_default_seat()
        .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
    const pointTo = (x, y) => dragger.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
    const button = state => dragger.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, state);
    const grabs = [];
    global.display.connectObject('grab-op-begin', (_display, grabbed, op) => grabs.push(`${grabbed?.title}:${op}`), grabs);
    const grip = window.get_frame_rect();
    const gripX = grip.x + Math.round(grip.width / 2);
    const gripY = grip.y + 15;
    const dropY = monitor.y + Math.round(monitor.height / 2);
    pointTo(gripX, gripY);
    await sleep(100);
    button(Clutter.ButtonState.PRESSED);
    await sleep(100);
    // A headless title bar press starts no move, so begin it the way the
    // top bar does; the virtual pointer then drives and ends it.
    const backend = global.stage.get_context().get_backend();
    window.begin_grab_op(Meta.GrabOp.MOVING, backend.get_pointer_sprite(global.stage),
        global.get_current_time(), new Graphene.Point({x: gripX, y: gripY}));
    for (let step = 1; step <= 10; step++) {
        pointTo(gripX + (monitor.x + 2 - gripX) * step / 10, gripY + (dropY - gripY) * step / 10);
        await sleep(40);
    }
    const dragged = settings().module('snap')._dragging === window;
    await sleep(300);
    const previewed = !!settings().module('snap')._preview?.visible;
    button(Clutter.ButtonState.RELEASED);
    const leftHalf = snapRect('left', workArea(), 0);
    assert(await waitFor(() => framed(window, leftHalf), 3000),
        `A floated window dragged to the left edge did not fill the left half: grabs [${grabs}], drag seen ${
            dragged}, preview ${previewed}, active ${settings().module('snap')._active}, ${
            box4(window.get_frame_rect())} vs ${box4(leftHalf)}`);
    global.display.disconnectObject(grabs);
    await sleep(300);
    assert(framed(window, leftHalf), `The snapped floated window was moved again: ${JSON.stringify(window.get_frame_rect())}`);
    window.activate(global.get_current_time());
    await sleep(200);
    tilingModule.toggleFloating();
    assert(await waitFor(() => [window, tiled].every(w => expect(2).some(r => framed(w, r))), 3000),
        'Un-floating the snapped window did not re-tile it');
    // An X11 app, through Xwayland, joins the layout like any other.
    const x11 = await openWindow(launcher, 'x11', ['GDK_BACKEND=x11']);
    assert(x11.get_client_type() === Meta.WindowClientType.X11, 'The X11 test window opened as a Wayland client');
    assert(await waitFor(() => [window, tiled, x11].every(w => expect(3).some(r => framed(w, r))), 4000),
        `An X11 window did not join the layout: ${[window, tiled, x11].map(w => box4(w.get_frame_rect())).join(' ')}`);
    const openX11 = global.get_window_actors().length;
    x11.delete(global.get_current_time());
    await waitFor(() => global.get_window_actors().length === openX11 - 1, 3000);
    assert(await waitFor(() => [window, tiled].every(w => expect(2).some(r => framed(w, r))), 3000),
        'The layout did not close up after the X11 window closed');
    // An app whose minimum size is wider than its tile (KeePassXC) keeps
    // its size, but sits at its tile's corner, inside the work area.
    const wide = await openWindow(launcher, 'wide');
    const wideTile = expect(3)[2];
    const wideAt = () => keptInside(wideTile, wide.get_frame_rect(), workArea());
    assert(await waitFor(() => wide.get_frame_rect().width > wideTile.width &&
        wide.get_frame_rect().x === wideAt().x && wide.get_frame_rect().y === wideAt().y &&
        framed(tiled, expect(3)[0]) && framed(window, expect(3)[1]), 4000),
    `A window wider than its tile was not put at its tile: ${box4(wide.get_frame_rect())} vs ${
        box4(wideTile)}, others ${box4(tiled.get_frame_rect())} ${box4(window.get_frame_rect())}`);
    const openWide = global.get_window_actors().length;
    wide.delete(global.get_current_time());
    await waitFor(() => global.get_window_actors().length === openWide - 1, 3000);
    assert(await waitFor(() => framed(tiled, expect(2)[0]) && framed(window, expect(2)[1]), 3000),
        'The layout did not close up after the wide window closed');
    // The layout switcher in the top bar.
    const indicator = Main.panel.statusArea['desktop-forge-layout'];
    assert(indicator?.layout === 'columns', `The layout switcher is missing or wrong: ${indicator?.layout}`);
    indicator._items.get('centered').activate(null);
    const centred = layoutRects('centered', 2, workArea(), {inner: 10, outer: 10, smart: false, ratio: 0.55});
    assert(await waitFor(() => framed(tiled, centred[0]) && framed(window, centred[1]), 3000),
        'Choosing a layout in the top bar did not re-tile the workspace');
    assert(indicator.layout === 'centered', 'The layout switcher did not follow the change');
    indicator._items.get('columns').activate(null);
    assert(await waitFor(() => framed(window, expect(2)[1]), 3000), 'Switching back to columns failed');
    const open2 = global.get_window_actors().length;
    tiled.delete(global.get_current_time());
    await waitFor(() => global.get_window_actors().length === open2 - 1, 3000);
    assert(await waitFor(() => framed(window, expect(1)[0]), 3000), 'The layout did not close up after a window closed');
    // GNOME renumbers workspaces when it removes an empty one; windows keep
    // their layout because groups follow the workspace, not its number.
    const manager = global.workspace_manager;
    const first = await openWindow(launcher, 'first');
    window.change_workspace_by_index(1, false);
    const partner = await openWindow(launcher, 'partner');
    partner.change_workspace_by_index(1, false);
    manager.get_workspace_by_index(1).activate(global.get_current_time());
    assert(await waitFor(() => [window, partner].every(w => expect(2).some(r => framed(w, r))), 4000),
        'Two windows on the second workspace were not tiled together');
    const workspacesBefore = manager.n_workspaces;
    first.delete(global.get_current_time());
    assert(await waitFor(() => manager.n_workspaces === workspacesBefore - 1 &&
        window.get_workspace().index() === 0, 4000), 'The emptied first workspace was not removed');
    const third = await openWindow(launcher, 'third');
    const threeTiled = await waitFor(() => [window, partner, third].every(w => expect(3).some(r => framed(w, r))), 4000);
    assert(threeTiled, `After renumbering, the new window did not join the others: ${
        [window, partner, third].map(w => JSON.stringify(w.get_frame_rect())).join(' ')}`);
    // Scrolling columns: two on screen, the third waits unseen under the
    // edge, and focusing it scrolls it into view. Nothing leaves the display.
    third.activate(global.get_current_time());
    await sleep(200);
    Main.panel.statusArea['desktop-forge-layout']._items.get('scrolling').activate(null);
    const hiddenOnes = () => [window, partner, third].filter(w => w.get_compositor_private().opacity === 0);
    assert(await waitFor(() => hiddenOnes().length === 1 && third.get_compositor_private().opacity === 255, 3000),
        `Scrolling did not hide exactly one column: ${hiddenOnes().length}`);
    const waiting = hiddenOnes()[0];
    waiting.activate(global.get_current_time());
    assert(await waitFor(() => waiting.get_compositor_private().opacity === 255 && hiddenOnes().length === 1, 3000),
        'Focusing a hidden column did not scroll it into view');
    assert([window, partner, third].every(w => w.get_monitor() === monitor.index),
        'A scrolled window left its display');
    Main.panel.statusArea['desktop-forge-layout']._items.get('columns').activate(null);
    assert(await waitFor(() => hiddenOnes().length === 0 &&
        [window, partner, third].every(w => expect(3).some(r => framed(w, r))), 3000),
    'Leaving scrolling did not show every window again');
    // Dwindle: a new window splits the one it opened from; a minimized
    // window gives its room to its sibling and gets it back.
    partner.activate(global.get_current_time());
    await sleep(200);
    Main.panel.statusArea['desktop-forge-layout']._items.get('dwindle').activate(null);
    await sleep(800);
    const rectOf = w => {
        const r = w.get_frame_rect();
        return {x: r.x, y: r.y, width: r.width, height: r.height};
    };
    const within = (inner, outer) => inner.x >= outer.x - 1 && inner.y >= outer.y - 1 &&
        inner.x + inner.width <= outer.x + outer.width + 1 && inner.y + inner.height <= outer.y + outer.height + 1;
    const partnerTile = rectOf(partner);
    const fourth = await openWindow(launcher, 'fourth');
    const split = await waitFor(() => within(rectOf(fourth), partnerTile) && within(rectOf(partner), partnerTile) &&
        rectOf(partner).width * rectOf(partner).height < partnerTile.width * partnerTile.height * 0.6, 4000);
    assert(split, `The new window did not split the one it opened from: ${JSON.stringify(partnerTile)} → ${
        JSON.stringify(rectOf(partner))} + ${JSON.stringify(rectOf(fourth))}`);
    const halves = [rectOf(partner), rectOf(fourth)];
    fourth.minimize();
    assert(await waitFor(() => framed(partner, partnerTile), 3000), 'A minimized window did not give its room back');
    fourth.unminimize();
    assert(await waitFor(() => framed(partner, halves[0]) && framed(fourth, halves[1]), 3000),
        'An unminimized window did not return to its place in the tree');
    fourth.activate(global.get_current_time());
    await sleep(200);
    const before4 = rectOf(fourth);
    tilingModule.resizeMain(0.1);
    assert(await waitFor(() => rectOf(fourth).width * rectOf(fourth).height > before4.width * before4.height * 1.1, 3000),
        'Growing the focused window in dwindle did not grow it');
    const count4 = global.get_window_actors().length;
    fourth.delete(global.get_current_time());
    await waitFor(() => global.get_window_actors().length === count4 - 1, 3000);
    assert(await waitFor(() => framed(partner, partnerTile), 3000), 'Closing a window did not give its room to its sibling');
    Main.panel.statusArea['desktop-forge-layout']._items.get('columns').activate(null);
    assert(await waitFor(() => [window, partner, third].every(w => expect(3).some(r => framed(w, r))), 3000),
        'Leaving dwindle did not re-tile in columns');
    for (const extra of [partner, third]) {
        const count = global.get_window_actors().length;
        extra.delete(global.get_current_time());
        await waitFor(() => global.get_window_actors().length === count - 1, 3000);
    }
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, tiling: {...tilingValues, enabled: false}});
    await sleep(400);
    assert(tilingModule._watched.size === 0 && !Main.panel.statusArea['desktop-forge-layout'],
        'Tiling kept watching windows or its top bar switcher while off');
    results.tiling = 'lone window, dragged lone window stays and snaps, focus view, two columns with gaps, ' +
        'swap with a glide, maximize, top bar layouts, ' +
        'close-up, survives workspace renumbering, scrolling columns, dwindle tree';

    // The scratchpad hides a window, drops it down over everything, and
    // gives it back.
    const scratchpad = settings().module('scratchpad');
    window.activate(global.get_current_time());
    await sleep(200);
    scratchpad.send();
    assert(await waitFor(() => window.minimized, 2000) && window.is_above() && window.is_on_all_workspaces(),
        'Sending a window to the scratchpad did not hide it above everything');
    scratchpad.toggle();
    const padArea = workArea();
    assert(await waitFor(() => !window.minimized && global.display.focus_window === window, 2000),
        'The scratchpad did not drop down');
    const padFrame = window.get_frame_rect();
    assert(Math.abs(padFrame.x + padFrame.width / 2 - (padArea.x + padArea.width / 2)) <= 2 &&
        padFrame.y < padArea.y + padArea.height * 0.1, `The scratchpad is not at the top centre: ${JSON.stringify(padFrame)}`);
    scratchpad.toggle();
    assert(await waitFor(() => window.minimized, 2000), 'The scratchpad did not hide again');
    scratchpad.toggle();
    await waitFor(() => global.display.focus_window === window, 2000);
    scratchpad.send();
    assert(await waitFor(() => !window.minimized && !window.is_above() && !window.is_on_all_workspaces(), 2000),
        'Taking the window out of the scratchpad did not restore it');
    results.scratchpad = 'send, drop down at the top centre, hide, take back';

    // Snapping with gaps, by shortcut (the drag path shares the geometry).
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, snap: {quarters: true, gaps: 10}});
    await sleep(400);
    window.activate(global.get_current_time());
    await sleep(200);
    settings().module('snap').snapFocused('top-left');
    assert(await waitFor(() => framed(window, snapRect('top-left', workArea(), 10)), 3000),
        `Snapping to a quarter with gaps failed: ${JSON.stringify(window.get_frame_rect())}`);
    results.snap = 'top-left quarter with 10px gaps';

    const bindings = settings().module('keybindings');
    assert(bindings?.added.length === 24, `Shortcuts were not registered: ${bindings?.added.length}`);
    const gestures = settings().module('gestures');
    gestures.run('overview');
    assert(await waitFor(() => Main.overview.visible, 3000), 'The overview gesture action did nothing');
    gestures.run('overview');
    assert(await waitFor(() => !Main.overview.visible && !Main.overview.animationInProgress, 3000),
        'The overview did not close again');
    // The touchpad path itself, with events shaped like Clutter's.
    const {Clutter: C} = {Clutter};
    const event = (type, phase, fingers, dx = 0, dy = 0, scale = 1) => ({
        type: () => type, get_gesture_phase: () => phase,
        get_touchpad_gesture_finger_count: () => fingers,
        get_gesture_motion_delta_unaccelerated: () => [dx, dy], get_gesture_pinch_scale: () => scale,
    });
    const SWIPE = C.EventType.TOUCHPAD_SWIPE;
    const PINCH = C.EventType.TOUCHPAD_PINCH;
    const P = C.TouchpadGesturePhase;
    const swipe = (fingers, dx, dy) => [P.BEGIN, P.UPDATE, P.END].map(phase =>
        gestures._event(event(SWIPE, phase, fingers, phase === P.UPDATE ? dx : 0, phase === P.UPDATE ? dy : 0)));
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12},
        gestures: {three_up: 'show_desktop', pinch_in: 'overview'}});
    await sleep(400);
    const startIndex = global.workspace_manager.get_active_workspace_index();
    const taken = swipe(3, -120, 0);
    assert(taken.every(r => r === C.EVENT_STOP), `A taken three-finger swipe was not stopped: ${taken}`);
    assert(await waitFor(() => global.workspace_manager.get_active_workspace_index() === startIndex + 1, 2000),
        'A three-finger swipe left on GNOME default did not go to the next workspace');
    swipe(3, 120, 0);
    assert(await waitFor(() => global.workspace_manager.get_active_workspace_index() === startIndex, 2000),
        'A three-finger swipe right did not come back');
    const passed = swipe(2, -120, 0);
    assert(passed.every(r => r === C.EVENT_PROPAGATE), 'Two-finger swipes must stay with apps');
    assert(swipe(4, -120, 0).every(r => r === C.EVENT_PROPAGATE),
        'Four-finger swipes with no changes must stay with GNOME');
    assert(gestures._event(event(PINCH, P.BEGIN, 2)) === C.EVENT_PROPAGATE, 'Two-finger pinches must stay with apps');
    gestures._event(event(PINCH, P.BEGIN, 3));
    gestures._event(event(PINCH, P.UPDATE, 3, 0, 0, 0.5));
    assert(gestures._event(event(PINCH, P.END, 3, 0, 0, 0.5)) === C.EVENT_STOP, 'A three-finger pinch was not taken');
    assert(await waitFor(() => Main.overview.visible, 3000), 'Pinching in did not open the overview');
    Main.overview.hide();
    await waitFor(() => !Main.overview.visible && !Main.overview.animationInProgress, 3000);
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, gestures: {}});
    results.input = `${bindings.added.length} shortcuts registered; swipes and pinches routed as mapped`;

    // The widget grid follows desktop.json (the running module's own copy of
    // layoutLogic.js), and workspaces wrap at the ends.
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, desktop: {grid: 16, snap_distance: 4},
        workspaces: {wrap: true}});
    const layout = await import(`${forge.dir.get_uri()}/layoutLogic.js`);
    assert(await waitFor(() => layout.GRID_SIZE === 16 && layout.SNAP_DISTANCE === 4, 2000),
        `The widget grid did not follow: ${layout.GRID_SIZE}, ${layout.SNAP_DISTANCE}`);
    manager.get_workspace_by_index(0).activate(global.get_current_time());
    await sleep(300);
    const count = manager.n_workspaces;
    assert(count >= 2, `Expected a spare workspace, found ${count}`);
    const binding = name => ({get_name: () => name});
    const wrap = settings().module('workspaces');
    wrap._switch(global.display, null, null, binding('switch-to-workspace-left'));
    assert(await waitFor(() => manager.get_active_workspace_index() === manager.n_workspaces - 1, 2000),
        'Going left from the first workspace did not wrap to the last');
    wrap._switch(global.display, null, null, binding('switch-to-workspace-right'));
    assert(await waitFor(() => manager.get_active_workspace_index() === 0, 2000),
        'Going right from the last workspace did not wrap to the first');
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, desktop: {}, workspaces: {}});
    assert(await waitFor(() => layout.GRID_SIZE === 8, 2000), 'The widget grid did not reset');
    results.workspaces = 'grid 16/4 then reset; wrap both ways';

    // Lock: no module runs, and the floating bar's strut does not move.
    writeDesktop({top_bar: {floating: true, margin: 8, radius: 12}, windows: {corner_radius: 24}});
    await sleep(400);
    const beforeLock = workTop();
    Main.sessionMode.pushMode('unlock-dialog');
    await sleep(600);
    assert(!forge._desktopSettings, 'Customization modules kept running on the lock screen');
    assert(!effects.styled.length, 'Window effects stayed attached while locked');
    assert(box.height === 40 && workTop() === beforeLock,
        `Locking changed the work area: ${beforeLock} then ${workTop()}`);
    Main.sessionMode.popMode('unlock-dialog');
    assert(await waitFor(() => forge._desktopSettings?.active.includes('window-effects'), 3000),
        'Customization did not come back after unlocking');
    assert(workTop() === beforeLock, 'Unlocking changed the work area');
    results.lock = 'nothing ran while locked; the work area never moved';

    // Neutral again: the bar returns to the edge and windows to normal.
    writeDesktop({top_bar: {floating: false}, windows: {}, wallpaper: {}, notifications: {}});
    assert(await waitFor(() => box.height === 32, 2000), 'The bar did not return to its edge');
    const current = Main.layoutManager._bgManagers[0].backgroundActor;
    assert(!current.get_effect('desktop-forge-wallpaper-blur'), 'The wallpaper blur was not removed');
    await place(window, monitor.x + 100, monitor.y + 100, 420, 360);
    // Close the typing window and let input methods settle before Shell
    // shuts down; IBus signals arriving mid-shutdown log criticals of its own.
    const open = global.get_window_actors().length;
    window.delete(global.get_current_time());
    await waitFor(() => global.get_window_actors().length === open - 1, 3000);
    await sleep(800);
    results.failed = settings()?.failed ?? [];
    assert(!results.failed.length, `Modules failed: ${results.failed}`);
    return results;
}
