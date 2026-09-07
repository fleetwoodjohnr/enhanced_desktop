import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

async function screenshot(name) {
    const stream = Gio.File.new_for_path(`${GLib.getenv('DF_TEST_DIR')}/${name}.png`)
        .replace(null, false, Gio.FileCreateFlags.NONE, null);
    await new Shell.Screenshot().screenshot(false, stream);
    stream.close(null);
}

const newsData = {filter_signature: '[[],[]]', headlines: [
    {title: 'This long headline must scroll every word through the viewport and reach THE END', source: 'Test', url: 'https://example.com/1'},
    {title: 'Short title', source: 'Test', url: 'https://example.com/2'},
]};
const systemData = {hostname: 'fedora', cpu_percent: 25, memory: {percent: 45}, disk: {percent: 60}, battery: {percent: 80, status: 'Discharging'}, network: {rx_rate: 20, tx_rate: 10}, uptime_seconds: 7200};
const stocksData = {quotes: Array.from({length: 20}, (_, i) => ({symbol: `TEST${i}`, price: 10 + i, change_percent: i - 5}))};
const todosData = {items: Array.from({length: 20}, (_, i) => ({id: `task${i}`, text: `Task ${i}`, status: 'todo'}))};

function descendants(actor) {
    return actor.get_children().flatMap(child => [child, ...descendants(child)]);
}

export default class SmokeTest extends Extension {
    enable() {
        this._run().catch(error => this._finish({ok: false, error: `${error}`, stack: error.stack}));
    }

    disable() {}

    _finish(result) {
        GLib.file_set_contents(`${GLib.getenv('DF_TEST_DIR')}/result.json`, JSON.stringify(result));
        global.context.terminate();
    }

    async _run() {
        const {newsRequestSignature} = await import(GLib.filename_to_uri(
            `${GLib.getenv('DF_TEST_ROOT')}/extension/widgetLogic.js`, null));
        newsData.request_signature = newsRequestSignature();
        await sleep(2500);
        for (let i = 0; i < 100 && Main.layoutManager._startingUp; i++)
            await sleep(100);
        Main.overview.hide();
        await sleep(500);
        assert(!Main.overview.visible, 'Startup overview did not close');
        const extension = Main.extensionManager.lookup('desktop-forge@jrf.local');
        const forge = extension?.stateObj;
        if (!forge?._widgets?.length)
            throw new Error(`Production extension did not start: ${extension?.error}`);
        const news = forge._widgets.find(r => r.entry.type === 'news').widget;
        const system = forge._widgets.find(r => r.entry.type === 'system').widget;
        const stocks = forge._widgets.find(r => r.entry.type === 'stocks').widget;
        const todos = forge._widgets.find(r => r.entry.type === 'todos').widget;
        news.update({ok: true, data: newsData});
        system.update({ok: true, data: systemData});
        stocks.update({ok: true, data: stocksData});
        todos.update({ok: true, data: todosData});
        await sleep(1000);
        const ticker = news._headlineTickers[0];
        const pointer = Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        const movePointer = (x, y) => pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
        const [rowX, rowY] = news._feedEntries[0].row.get_transformed_position();
        movePointer(rowX + 60, rowY + 15);
        await sleep(700);
        const metrics = {
            width: ticker.width, labelWidth: ticker._label.width,
            cycle: ticker._cycle, translation: ticker._track.translation_x,
            timeline: !!ticker._timeline,
            background: !!news._wallpaper?._manager,
            wallpaperVisible: news._wallpaper?.visible,
            geometry: news._wallpaper?._wallpaperGeometry,
            margin: news._wallpaper?._margin,
            sampleSize: news._wallpaper?._sample.get_size(),
            samplePosition: news._wallpaper?._sample.get_position(),
            wallpaper: news._wallpaper?._sample.get_children().map(a => ({position: a.get_position(), size: a.get_size()})),
        };
        GLib.file_set_contents(`${GLib.getenv('DF_TEST_DIR')}/metrics.json`, JSON.stringify(metrics));
        await screenshot('widgets');
        if (!metrics.timeline || metrics.translation >= 0 || !metrics.background || !metrics.wallpaperVisible)
            throw new Error(`Rendering smoke failed: ${JSON.stringify(metrics)}`);
        assert(news.hover && ticker._hovered, 'Real pointer hover did not activate the ticker');
        const timeline = ticker._timeline;
        const start = ticker._track.translation_x;
        for (let i = 0; i < 10; i++) {
            ticker.queue_relayout();
            await sleep(30);
        }
        assert(ticker._timeline === timeline && ticker._track.translation_x < start,
            'Repeated allocation restarted scrolling');
        assert(ticker._tail.x - ticker._label.x === ticker._cycle, 'Repeat point does not match the allocated copy');
        let cycles = 0;
        timeline.connect('completed', () => cycles++);
        await sleep(timeline.duration * 2 + 100);
        assert(cycles >= 2 && ticker._timeline === timeline, 'Ticker did not complete two uninterrupted cycles');
        news.renderData({...newsData, headlines: [...newsData.headlines, {title: 'Refreshed story'}]});
        assert(news._headlines.length === 2 && !!news._pendingData, 'News refresh interrupted hover reading');
        movePointer(1100, 800);
        await sleep(300);
        assert(news._headlines.length === 3 && !news._pendingData, 'Deferred news did not apply after pointer exit');

        const longFeed = {...newsData, headlines: Array.from({length: 20}, (_, i) => ({
            ...newsData.headlines[0], title: `${i}: ${newsData.headlines[0].title}`, url: `https://example.com/feed/${i}`,
        }))};
        news.update({ok: true, data: longFeed});
        await sleep(400);
        const checkScrollbar = async widget => {
            const scroll = widget._scroll;
            const a = scroll.get_vadjustment();
            const handle = descendants(scroll).find(child => child.name === 'vhandle' ||
                child.has_style_class_name?.('vhandle'));
            assert(handle, 'Native vertical scrollbar handle is missing');
            let bar = handle.get_parent();
            while (!(bar instanceof St.ScrollBar))
                bar = bar.get_parent();
            movePointer(1150, 800);
            await sleep(250);
            assert(bar.opacity === 0, 'Scrollbar is painted without pointer hover');
            const width = widget._rows.width;
            const [x, y] = scroll.get_transformed_position();
            movePointer(x + 40, y + 20);
            await sleep(250);
            assert(bar.opacity > 0,
                'Hover over content did not reveal the scrollbar');
            assert(widget._rows.width === width && scroll.overlay_scrollbars,
                'Revealing the scrollbar shifted content');
            assert(bar.width <= 10, `Scrollbar is too wide: ${bar.width}`);
            const thumb = descendants(handle).find(actor => actor.has_style_class_name?.('df-scroll-thumb'));
            assert(thumb?.width === 4, `Scrollbar thumb is too thick: ${thumb?.width}`);
            assert(handle.get_theme_node().get_background_color().alpha === 0,
                'Native drag target paints behind the thin thumb');
            if (widget === news || widget._config.type === 'news')
                await screenshot(widget._style.dark ? 'scrollbar-dark' : 'scrollbar-light');
            a.value = 0;
            await sleep(100);
            const [hx, hy] = handle.get_transformed_position();
            movePointer(hx + handle.width / 2, hy + handle.height / 2);
            await sleep(100);
            const picked = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE,
                hx + handle.width / 2, hy + handle.height / 2);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            await sleep(100);
            for (let step = 1; step <= 10; step++) {
                movePointer(hx + handle.width / 2, hy + handle.height / 2 + step * 5);
                await sleep(25);
            }
            assert(a.value > 0, `Scrollbar thumb did not drag: ${JSON.stringify({
                name: widget.constructor.name, dragging: widget._scrollbarDragging,
                picked: picked?.toString(), isHandle: picked === handle,
                handleRect: [hx, hy, handle.width, handle.height],
            })}`);
            movePointer(1150, 800);
            await sleep(150);
            assert(bar.opacity > 0,
                'Dragging outside the card hid the scrollbar');
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
            await sleep(250);
            assert(bar.opacity === 0,
                'Scrollbar did not hide after drag release outside the card');
            a.value = 0;
        };
        for (const widget of [news, stocks, todos])
            await checkScrollbar(widget);
        for (const widget of [news, stocks]) {
            const name = widget === news ? 'News' : 'Markets';
            const a = widget._adjustment;
            assert(widget._feedEntries.length === 20 && a.upper > a.page_size,
                `${name} did not expose the full overflowing feed`);
            const [x, y] = widget._scroll.get_transformed_position();
            movePointer(x + 60, y + 30);
            await sleep(200);
            assert(!widget._rotationId, `${name} did not pause on hover`);
            pointer.notify_discrete_scroll(GLib.get_monotonic_time(), Clutter.ScrollDirection.DOWN, Clutter.ScrollSource.WHEEL);
            await sleep(300);
            assert(a.value > 0, `${name} ignored wheel down`);
            const afterWheel = a.value;
            pointer.notify_scroll_continuous(GLib.get_monotonic_time(), 0, 12, Clutter.ScrollSource.FINGER, Clutter.ScrollFinishFlags.NONE);
            await sleep(300);
            assert(a.value > afterWheel, `${name} ignored touchpad scrolling`);
            if (widget === news) {
                assert(!news._headlineTickers[0]._hovered, 'Scrolling left the offscreen headline active');
                assert(news._headlineTickers.filter(t => t._hovered).length === 1,
                    'Stationary pointer did not follow the newly visible headline');
            }
            movePointer(x + 60, widget.y + 25);
            await sleep(100);
            a.value = a.upper;
            pointer.notify_discrete_scroll(GLib.get_monotonic_time(), Clutter.ScrollDirection.UP, Clutter.ScrollSource.WHEEL);
            await sleep(250);
            assert(a.value < a.upper - a.page_size, `${name} header ignored wheel up`);
            a.value = a.upper;
            await sleep(200);
            assert(widget._range.text.endsWith('20 of 20'), `${name} final visible range is wrong: ${widget._range.text}`);
            widget._advanceFeed();
            assert(a.value === 0, `${name} automatic advance did not wrap`);
            widget._advanceFeed();
            assert(a.value > 0, `${name} automatic advance did not move forward`);
            a.value = widget._feedEntries[6].row.y + 3;
            const anchor = widget._captureAnchor();
            const refreshed = widget === news ? {...longFeed, headlines: [{title: 'New arrival', url: 'https://example.com/new'}, ...longFeed.headlines]} :
                {...stocksData, quotes: stocksData.quotes.map(q => ({...q, price: q.price + 1}))};
            widget.update({ok: true, data: refreshed});
            assert(widget._pendingData, `${name} refresh was not deferred on hover`);
            movePointer(1100, 800);
            await sleep(400);
            const restored = widget._captureAnchor();
            assert(restored.key === anchor.key && Math.abs(restored.offset - anchor.offset) < 1,
                `${name} refresh lost reading position: ${JSON.stringify({anchor, restored})}`);
            assert(widget._rotationId, `${name} did not resume rotation after hover`);
            widget._scroll.grab_key_focus();
            await sleep(100);
            assert(!widget._rotationId, `${name} did not pause for keyboard focus`);
            global.stage.set_key_focus(null);
            await sleep(100);
            assert(widget._rotationId, `${name} did not resume after keyboard focus`);
        }
        const beforeRotation = stocks._adjustment.value;
        await sleep(13500);
        assert(stocks._adjustment.value !== beforeRotation, 'Markets did not actually advance after 12 seconds');
        const [newsX, newsY] = news._scroll.get_transformed_position();
        movePointer(newsX + 30, newsY + 30);
        await sleep(150);
        news.update({ok: true, data: {...longFeed, headlines: [...longFeed.headlines, {title: 'Waiting', url: 'https://example.com/wait'}]}});
        assert(news._pendingData, 'Hover did not defer the previous request');
        news.setNewsFilter({topic_presets: ['ai']});
        news.update({ok: false, stale: true, error: 'offline', data: longFeed});
        await sleep(200);
        assert(!news._headlines.length && !news._pendingData && news._adjustment.value === 0,
            'Changing filters retained incompatible cached headlines');
        const filtered = {...newsData, filter_signature: '[["ai"],[]]',
            request_signature: newsRequestSignature({topic_presets: ['ai']})};
        news.update({ok: true, data: filtered});
        assert(news._headlines.length === 2, 'Matching filter payload was rejected');
        news.update({ok: false, stale: true, error: 'offline', data: filtered});
        assert(news._headlines.length === 2 && news._status.visible, 'Matching stale data was lost');
        movePointer(1150, 820);
        await sleep(150);
        news.update({ok: true, data: {...filtered, headlines: [], topics: ['Artificial intelligence']}});
        assert(news._rows.get_first_child().text.includes('No headlines match'), 'Empty filtered feed is not explained');
        news.update({ok: true, data: {...filtered, headlines: [], topics: ['Artificial intelligence'],
            sources: ['Available'], problems: ['example.com: HTTP 503']}});
        assert(news._problem.visible && news._problem.text.includes('example.com'),
            'Partial failure was hidden when no headlines matched');
        news.setNewsFilter({topic_presets: ['ai'], feeds: ['https://example.com/new']});
        news.update({ok: true, data: filtered});
        assert(!news._headlines.length, 'Changing sources accepted an older request');
        news.setNewsFilter({topic_presets: []});
        news.update({ok: true, data: {...newsData, topics_disabled: true, headlines: [],
            request_signature: newsRequestSignature({topic_presets: []})}});
        assert(news._rows.get_first_child().text === 'No topics selected', 'All-off news is not explained');
        news.setNewsFilter({});
        news.update({ok: true, data: {...newsData, request_signature: undefined}});
        assert(!news._headlines.length, 'Unversioned data from older filtering rules was accepted');
        news.setNewsFilter({});
        stocks.update({ok: true, data: {quotes: stocksData.quotes.slice(0, 2)}});
        todos.update({ok: true, data: {items: []}});
        await sleep(250);
        assert(!stocks._scroll.vscrollbar_visible && !todos._scroll.vscrollbar_visible,
            'Fitting or empty content showed a scrollbar');

        // Exercise fitting and exact-fit titles in a real allocated St actor.
        const probe = new ticker.constructor('Fit exactly');
        Main.layoutManager.addChrome(probe);
        probe.set_position(800, 100);
        probe.set_width(200);
        await sleep(150);
        probe.setHovered(true);
        await sleep(300);
        assert(!probe._timeline && probe._tail.opacity === 0, 'Short title animated or exposed its duplicate');
        probe.width = probe._label.width;
        await sleep(150);
        assert(!probe._cycle, 'Exactly fitting title was treated as overflowing');
        probe.width = 30;
        await sleep(500);
        assert(probe._timeline && probe._track.translation_x < 0, 'Resizing did not reveal the entire title');
        const originalTextWidth = probe._label.width;
        probe.set_style('font-size: 24px');
        await sleep(400);
        assert(probe._label.width > originalTextWidth && probe._timeline,
            'Font change left the ticker using its old text width');
        probe.setHovered(false);
        assert(!probe._timeline && !probe._delayId && probe._track.translation_x === 0, 'Hover exit did not reset the ticker');
        probe.setHovered(true);
        Main.layoutManager.removeChrome(probe);
        probe.destroy();
        await sleep(300);

        news.update({ok: true, data: newsData});
        await sleep(300);
        await screenshot('before-window');
        news.hide();
        system.hide();
        await sleep(100);
        await screenshot('wallpaper-only');
        news.show();
        system.show();
        await sleep(200);
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        launcher.setenv('WAYLAND_DISPLAY', 'df-smoke', true);
        this._windowProcess = launcher.spawnv(['python3', `${GLib.getenv('DF_TEST_ROOT')}/tests/shell-smoke/window.py`]);
        let window;
        for (let i = 0; i < 40 && !window; i++) {
            await sleep(100);
            window = global.get_window_actors().map(a => a.meta_window)
                .find(w => w?.title === 'DF repaint test');
        }
        assert(window, 'Test application window did not open');
        window.move_resize_frame(false, 300, 150, 320, 300);
        await sleep(500);
        assert(forge._widgets.find(r => r.widget === news).layer === 'background', 'Covered news stayed above the application');
        await screenshot('covered-window');
        await sleep(1200);
        await screenshot('typed-window');
        for (let i = 0; i < 10; i++) {
            window.move_frame(false, i % 2 ? 780 : 300, 150);
            await sleep(100);
        }
        window.move_frame(false, 780, 150);
        await sleep(300);
        assert(forge._widgets.find(r => r.widget === news).layer === 'chrome', 'Uncovered news did not regain desktop input');
        window.minimize();
        await sleep(150);
        window.unminimize();
        await sleep(150);
        window.delete(global.get_current_time());
        await sleep(600);
        assert(forge._windowActors.size === global.get_window_actors().length, 'Closed window left a stale tracking record');
        Main.overview.show();
        await sleep(200);
        assert(forge._widgets.find(r => r.widget === news).layer === 'background', 'Overview did not demote the news widget');
        Main.overview.hide();
        await sleep(300);
        await screenshot('after-window');
        const battery = system._footerParts.battery.label;
        const [bx, by] = battery.get_transformed_position();
        const [bw, bh] = battery.get_size();
        let batteryUpdates = 0;
        battery.connect('notify::text', () => batteryUpdates++);
        system.renderData({...systemData, network: {rx_rate: 200, tx_rate: 100}});
        await sleep(200);
        assert(!batteryUpdates, 'Network refresh rewrote the battery text');
        await screenshot('network-refresh');
        system.renderData({...systemData, battery: {percent: 79, status: 'Discharging'}});
        await sleep(200);
        assert(batteryUpdates === 1 && battery.text.includes('79%'), 'Battery refresh did not update its persistent actor');
        await screenshot('battery-refresh');
        news.setBlurEnabled(false);
        await sleep(100);
        assert(!news._wallpaper.visible, 'Edit-mode blur disable did not hide the wallpaper');
        news.setBlurEnabled(true);
        news.set_size(340, 330);
        news.set_position(90, 100);
        await sleep(300);
        assert(news._wallpaper._wallpaperGeometry.x === 90, 'Wallpaper crop did not follow widget movement');
        await screenshot('resized');
        const interfaceSettings = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        interfaceSettings.set_string('color-scheme', 'prefer-dark');
        await sleep(1200);
        for (const {widget, entry} of forge._widgets)
            widget.update({ok: true, data: entry.type === 'news' ? newsData : entry.type === 'stocks' ? stocksData :
                entry.type === 'todos' ? todosData : systemData});
        await sleep(500);
        assert(forge._widgets.every(r => r.widget._style.dark), 'Dark theme did not rebuild widgets');
        await screenshot('dark');
        for (const {widget, entry} of forge._widgets) {
            if (entry.type === 'news')
                widget.update({ok: true, data: longFeed});
        }
        await sleep(300);
        for (const {widget, entry} of forge._widgets) {
            if (['news', 'stocks', 'todos'].includes(entry.type))
                await checkScrollbar(widget);
        }
        // The settings UI and its real GVfs metadata writes use this same
        // isolated session bus and compositor, never the user's folders.
        const uiLauncher = new Gio.SubprocessLauncher({
            flags: Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE,
        });
        uiLauncher.setenv('WAYLAND_DISPLAY', 'df-smoke', true);
        uiLauncher.setenv('GDK_BACKEND', 'wayland', true);
        uiLauncher.setenv('PYTHONDONTWRITEBYTECODE', '1', true);
        const newsUi = uiLauncher.spawnv(['python3', `${GLib.getenv('DF_TEST_ROOT')}/tests/news_settings_ui_smoke.py`]);
        const newsUiOutput = await new Promise((resolve, reject) => {
            newsUi.communicate_utf8_async(null, null, (process, result) => {
                try { resolve(process.communicate_utf8_finish(result)[1]); } catch (error) { reject(error); }
            });
        });
        GLib.file_set_contents(`${GLib.getenv('DF_TEST_DIR')}/news-ui.log`, newsUiOutput);
        assert(newsUi.get_successful() && newsUiOutput.includes('News GTK dialogs passed'),
            `News settings UI failed: ${newsUiOutput}`);

        // Real atomic config/state writes must reach an existing news actor
        // even while layout editing postpones actor reconstruction.
        const configPath = `${GLib.getenv('XDG_CONFIG_HOME')}/desktop-forge/config.json`;
        const [, configBytes] = Gio.File.new_for_path(configPath).load_contents(null);
        const originalConfig = JSON.parse(new TextDecoder().decode(configBytes));
        const editingConfig = {...originalConfig, version: 3, edit_layout: true};
        GLib.file_set_contents(configPath, JSON.stringify(editingConfig));
        await sleep(500);
        assert(forge._editMode, 'Atomic config save did not start layout editing');
        const editingNews = forge._widgets.find(r => r.entry.type === 'news').widget;
        editingNews.update({ok: true, data: newsData});
        editingConfig.providers = {...editingConfig.providers, news: {topic_presets: ['technology']}};
        GLib.file_set_contents(configPath, JSON.stringify(editingConfig));
        await sleep(300);
        assert(forge._widgets.some(r => r.widget === editingNews), 'Topic save rebuilt an actor held by layout editing');
        assert(!editingNews._headlines.length && !editingNews._pendingData, 'Layout editing retained old topics');
        editingNews.update({ok: true, data: newsData});
        assert(!editingNews._headlines.length, 'Layout editing accepted the previous request');
        const statePath = `${GLib.getenv('XDG_DATA_HOME')}/desktop-forge/state`;
        GLib.mkdir_with_parents(statePath, 0o755);
        GLib.file_set_contents(`${statePath}/news.json`, JSON.stringify({ok: true, data: {
            ...newsData, request_signature: newsRequestSignature(editingConfig.providers.news),
        }}));
        await sleep(300);
        assert(editingNews._headlines.length === 2, 'Atomic state update was not rendered during layout editing');
        GLib.file_set_contents(configPath, JSON.stringify(originalConfig));
        await sleep(600);
        assert(!forge._editMode, 'Layout editing did not close');

        if (GLib.getenv('DF_TEST_NEWS_ONLY') === '1') {
            this._finish({ok: true, scope: 'news', batteryRect: [bx, by, bw, bh]});
            return;
        }
        const uiTest = uiLauncher.spawnv(['python3', `${GLib.getenv('DF_TEST_ROOT')}/tests/folder_colors_ui_smoke.py`]);
        const uiOutput = await new Promise((resolve, reject) => {
            uiTest.communicate_utf8_async(null, null, (process, result) => {
                try {
                    const [, output] = process.communicate_utf8_finish(result);
                    resolve(output);
                } catch (error) {
                    reject(error);
                }
            });
        });
        GLib.file_set_contents(`${GLib.getenv('DF_TEST_DIR')}/folder-ui.log`, uiOutput);
        assert(uiTest.get_successful(), `Folder color UI failed: ${uiOutput}`);
        const cliveUi = uiLauncher.spawnv(['python3', `${GLib.getenv('DF_TEST_ROOT')}/tests/clive_ui_smoke.py`]);
        const cliveUiOutput = await new Promise((resolve, reject) => {
            cliveUi.communicate_utf8_async(null, null, (process, result) => {
                try { resolve(process.communicate_utf8_finish(result)[1]); } catch (error) { reject(error); }
            });
        });
        GLib.file_set_contents(`${GLib.getenv('DF_TEST_DIR')}/clive-ui.log`, cliveUiOutput);
        assert(cliveUi.get_successful() && cliveUiOutput.includes('CLIVE GTK'), `CLIVE UI failed: ${cliveUiOutput}`);

        for (const record of forge._widgets)
            record.widget.hide();
        const {CliveWidget} = await import(`${forge.dir.get_uri()}/widgets/clive.js`);
        const cliveCard = new CliveWidget({type: 'clive', x: 50, y: 60, width: 420, height: 480, monitor: 0},
            {...forge._widgets[0].widget._style, accent: '#32ade6', blur_radius: 0});
        let calls = [];
        let unsubscribe = false;
        let picked = [];
        let notices = [];
        // The real client runs the callback only when the service accepted the
        // request, which is what clears a staged list. Answering here lets the
        // card's success path run; `answer` turns it off to prove the other one.
        let answer = true;
        const cliveState = {id: 'test', conversation: 'chat', version: 1, status: 'awaiting_approval',
            preview: 'Find a file in Documents.', messages: [{role: 'user', content: 'Find my file'}], mode: 'local'};
        cliveCard.setClient({draft: '', attachments: [],
            subscribe: fn => { fn(cliveState); return () => { unsubscribe = true; }; },
            call: (...args) => { calls.push(args); if (answer) args[2]?.({}); },
            pickFiles: fn => { if (picked.length) fn(picked); },
            notify: message => notices.push(message), open: () => {}});
        Main.layoutManager.addChrome(cliveCard);
        cliveCard.set_position(50, 60);
        await sleep(200);
        assert(cliveCard._approve.visible && !cliveCard._sendButton.reactive, 'CLIVE approval state incorrect');
        assert(!cliveCard._attach.reactive, 'CLIVE offered to attach files while a task was waiting');
        await screenshot('clive-approval');
        cliveCard._approve.emit('clicked', Clutter.BUTTON_PRIMARY);
        assert(calls[0][0] === 'approve' && calls[0][1].version === 1, 'CLIVE approval did not bind the preview version');
        cliveCard.render({...cliveState, status: 'complete'});
        await sleep(200);
        assert(cliveCard._attach.reactive, 'CLIVE would not attach files once the task finished');

        // Starting a new chat is the one header button that can do nothing, and
        // it says so rather than swallowing the click.
        assert(cliveCard._fresh.reactive, 'New chat was dead on a conversation with messages');
        cliveCard.render({status: 'idle', conversation: '', messages: [], mode: 'local'});
        await sleep(100);
        assert(!cliveCard._fresh.reactive && cliveCard._fresh.opacity < 255,
            'New chat stayed live on an empty conversation, where it does nothing');
        cliveCard.render({...cliveState, status: 'complete'});
        await sleep(100);

        // Attaching files. The picker is the portal in the real card; here the
        // fake answers with paths, which is all the card ever receives.
        assert(!cliveCard._attachments.visible, 'The attachment list showed before anything was staged');
        picked = ['/home/someone/notes.md', '/home/someone/shot.png'];
        cliveCard._attach.emit('clicked', Clutter.BUTTON_PRIMARY);
        await sleep(200);
        assert(cliveCard._attachments.visible && cliveCard._attachmentRows.get_n_children() === 2,
            `Staged files did not appear: ${cliveCard._attachmentRows.get_n_children()} rows`);
        await screenshot('clive-attachments');

        // Picking the same file again must not stage it twice.
        cliveCard._attach.emit('clicked', Clutter.BUTTON_PRIMARY);
        await sleep(200);
        assert(cliveCard._attachmentRows.get_n_children() === 2, 'A re-picked file was staged twice');

        // Past the ceiling the card says so instead of dropping files quietly.
        notices = [];
        picked = Array.from({length: 8}, (_, index) => `/home/someone/extra-${index}.md`);
        cliveCard._attach.emit('clicked', Clutter.BUTTON_PRIMARY);
        await sleep(200);
        assert(cliveCard._attachmentRows.get_n_children() === 8, 'Staging went past the eight-file ceiling');
        assert(notices.some(one => one.includes('8 files')), `Overflow went unreported: ${notices}`);

        // Each row drops its own file.
        cliveCard._attachmentRows.get_child_at_index(0).get_children().at(-1)
            .emit('clicked', Clutter.BUTTON_PRIMARY);
        await sleep(200);
        assert(cliveCard._attachmentRows.get_n_children() === 7, 'Removing a staged file did not drop a row');
        assert(!cliveCard._client.attachments.includes('/home/someone/notes.md'),
            'Removing a row left its path staged');

        // A rejected submit keeps the list, so the named file can be dropped.
        answer = false;
        calls = [];
        cliveCard._entry.set_text('look at these');
        cliveCard._sendButton.emit('clicked', Clutter.BUTTON_PRIMARY);
        assert(calls[0][1].attachments.length === 7, 'Send did not carry the staged files');
        assert(cliveCard._client.attachments.length === 7, 'A refused send discarded the staged files');
        answer = true;
        picked = [];
        cliveCard._entry.set_text('');

        // Typing on the desktop, through the real input stack. Shell routes key
        // events to the focused window unless something takes a modal grab, so
        // a card with a bare St.Entry cannot pass from here on.
        const keyboard = Clutter.get_default_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        const press = keyval => {
            keyboard.notify_keyval(GLib.get_monotonic_time(), keyval, Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(GLib.get_monotonic_time(), keyval, Clutter.KeyState.RELEASED);
        };
        // Motion has to be delivered and picked up before the button lands,
        // exactly as with the news-widget hover above.
        const click = async (x, y) => {
            movePointer(x, y);
            await sleep(250);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.PRESSED);
            await sleep(50);
            pointer.notify_button(GLib.get_monotonic_time(), Clutter.BUTTON_PRIMARY, Clutter.ButtonState.RELEASED);
        };
        assert(!cliveCard.isCapturingKeys && Main.modalCount === 0,
            'CLIVE grabbed the keyboard before it was asked to');
        const [entryX, entryY] = cliveCard._entry.get_transformed_position();
        await click(entryX + cliveCard._entry.width / 2, entryY + cliveCard._entry.height / 2);
        await sleep(300);
        assert(cliveCard.isCapturingKeys, 'Clicking the CLIVE entry did not take a keyboard grab');
        assert(global.stage.get_key_focus() === cliveCard._entry.clutter_text,
            'The CLIVE keyboard grab did not focus the entry');
        cliveCard._entry.set_text('');
        press(Clutter.KEY_h);
        press(Clutter.KEY_i);
        await sleep(300);
        assert(cliveCard._entry.get_text() === 'hi',
            `Typing on the desktop did not reach CLIVE: "${cliveCard._entry.get_text()}"`);
        // The card's own controls have to stay usable inside its own grab.
        calls = [];
        cliveCard._sendButton.emit('clicked', Clutter.BUTTON_PRIMARY);
        assert(calls[0]?.[0] === 'submit' && calls[0][1].message === 'hi',
            'Send stopped working while the card held the keyboard');
        assert(calls[0][1].attachments.length === 7, 'Send dropped the staged files');
        assert(cliveCard._client.attachments.length === 0 && !cliveCard._attachments.visible,
            'An accepted send left the staged files behind');
        await screenshot('clive-typing');

        press(Clutter.KEY_Escape);
        await sleep(300);
        assert(!cliveCard.isCapturingKeys && Main.modalCount === 0,
            'Escape did not release the CLIVE keyboard grab');

        cliveCard.beginTyping();
        assert(cliveCard.isCapturingKeys, 'CLIVE did not retake the keyboard');
        await click(900, 760);
        await sleep(300);
        assert(!cliveCard.isCapturingKeys && Main.modalCount === 0,
            'Clicking away did not release the CLIVE keyboard grab');

        // Being covered by a window is the path the extension drives itself.
        cliveCard.beginTyping();
        cliveCard.clearDesktopInteraction();
        await sleep(300);
        assert(!cliveCard.isCapturingKeys && Main.modalCount === 0,
            'Demotion behind a window did not release the CLIVE keyboard grab');
        assert(!global.stage.get_key_focus() || !cliveCard.contains(global.stage.get_key_focus()), 'CLIVE retained focus behind a window');
        cliveCard.set_size(300, 320);
        await sleep(200);
        await screenshot('clive-widget');
        cliveCard.beginTyping();
        Main.layoutManager.removeChrome(cliveCard);
        cliveCard.destroy();
        assert(Main.modalCount === 0 && Main.modalActorFocusStack.length === 0,
            'Destroying CLIVE left a modal grab behind');
        assert(unsubscribe, 'CLIVE left a subscription after destruction');
        forge.disable();
        assert(forge._windowActors.size === 0 && forge._widgets.length === 0 && !forge._interactionId,
            'Extension teardown retained actors or queued work');
        forge.enable();
        await sleep(500);
        this._finish({ok: true, metrics, cycles, batteryRect: [bx, by, bw, bh]});
    }
}
