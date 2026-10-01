import {systemIsDark} from '../extension/themeLogic.js';
import {
    CHROME_DEFAULTS, SettleTimer, automaticForeground, barOverlap, barShouldShow,
    chromeStylesheet, edgeActivation, panelTranslation, pointerAtEdge,
    pointerInPanelCorridor, resolveChrome, revealAllowed,
} from '../extension/chromeLogic.js';
import {
    displayHostname, headlinePanDistance, headlineTickerDuration,
    rectanglesOverlap, widgetLayer, newsFilterSignature, newsOptions, NEWS_TOPIC_IDS,
    describeConnection, formatRate, formatTemperature, thermalLevel,
    visibleSensors,
} from '../extension/widgetLogic.js';
import {
    GRID_SIZE, SNAP_DISTANCE, WIDGET_GAP, meterFillWidth, pageSizeForHeight,
    settlePosition, settleResize,
} from '../extension/layoutLogic.js';
import {
    PAGE_SIZE, ROTATION_SECONDS, nextPage, normalizePage, pageCount, pageItems,
} from '../extension/widgets/pager.js';

function assert(condition, message) {
    if (!condition)
        throw new Error(message);
}

assert(automaticForeground('#ffffff') === '#000000',
    'automatic contrast must use dark foreground on light surfaces');
assert(automaticForeground('#000000') === '#ffffff',
    'automatic contrast must use light foreground on dark surfaces');
const chrome = resolveChrome({
    top_bar: {position: 'bottom', height: 200, opacity: -1, background: '#eeeeee'},
    dock: {foreground_mode: 'custom', foreground: '#abcdef', background: 'bad css'},
}, false);
assert(chrome.top_bar.position === 'bottom' && chrome.top_bar.height === 64 &&
    chrome.top_bar.opacity === 0 && chrome.top_bar.foreground === '#000000',
    'top-bar settings were not normalized and clamped');
assert(chrome.dock.background === '#f8fbff' && chrome.dock.foreground === '#abcdef',
    'dock colors did not fall back safely or preserve a valid manual foreground');
const chromeCss = chromeStylesheet(chrome);
assert(chromeCss.includes('rgba(238,238,238,0)') && !chromeCss.includes('bad css'),
    'generated chrome CSS did not use sanitized values');
const base = {mode: 'intelligent', locked: false, overview: false, fullscreen: false,
    revealInFullscreen: false, held: false, menuOpen: false, keyFocus: false, overlap: false};
assert(barShouldShow(base) && !barShouldShow({...base, overlap: true}),
    'intelligent auto-hide must follow window overlap');
assert(!barShouldShow({...base, mode: 'auto'}) && barShouldShow({...base, mode: 'auto', held: true}),
    'always-hidden must stay hidden until the user reveals it');
assert(barShouldShow({...base, mode: 'always', overlap: true, fullscreen: true}),
    'always-visible must ignore overlap (Shell handles fullscreen for it)');
assert(barShouldShow({...base, overlap: true, overview: true}) &&
    barShouldShow({...base, mode: 'auto', locked: true}),
    'the overview and the lock screen must always show the bar');
assert(!barShouldShow({...base, fullscreen: true, held: true}) &&
    barShouldShow({...base, fullscreen: true, held: true, revealInFullscreen: true}),
    'a fullscreen app keeps the bar away unless reveal-in-fullscreen is on');
assert(barShouldShow({...base, fullscreen: true, menuOpen: true}) &&
    barShouldShow({...base, mode: 'auto', keyFocus: true}),
    'an open menu or keyboard focus must hold the bar, even over fullscreen');
assert(revealAllowed(base) && !revealAllowed({...base, mode: 'always'}) &&
    !revealAllowed({...base, fullscreen: true}) &&
    revealAllowed({...base, fullscreen: true, revealInFullscreen: true}) &&
    !revealAllowed({...base, locked: true}),
    'the edge may only reveal a bar that is allowed to come back');

const bar = {x: 0, y: 0, width: 1200, height: 32};
const covering = {rect: {x: 100, y: 0, width: 400, height: 300}, focusedApp: false, maximized: false};
const flush = {rect: {x: 100, y: 32, width: 400, height: 300}, focusedApp: true, maximized: true};
const straddling = {rect: {x: 1100, y: 10, width: 600, height: 300}, focusedApp: false, maximized: false};
assert(barOverlap([covering], bar, 'any') && !barOverlap([flush], bar, 'any'),
    'overlap must use exclusive edges so a flush window does not hide the bar');
assert(barOverlap([straddling], bar, 'any'),
    'a window straddling in from another monitor must count as covering the bar');
assert(!barOverlap([covering], bar, 'focused') &&
    barOverlap([{...covering, focusedApp: true}], bar, 'focused'),
    'focused mode must only count the focused application');
assert(!barOverlap([covering], bar, 'maximized') &&
    barOverlap([{...covering, maximized: true}], bar, 'maximized'),
    'maximized mode must only count maximized or tiled windows');

assert(panelTranslation('top', 32, true) === -32 &&
    panelTranslation('bottom', 32, true) === 32 && panelTranslation('top', 32, false) === 0,
    'a hidden bar must leave its monitor entirely');
const monitorRect = {x: 0, y: 576, width: 1536, height: 864};
assert(pointerAtEdge('top', monitorRect, 2, 700, 576) && pointerAtEdge('top', monitorRect, 2, 700, 577) &&
    !pointerAtEdge('top', monitorRect, 2, 700, 578) && !pointerAtEdge('top', monitorRect, 2, 1600, 576),
    'the top edge band must be anchored to the monitor, not the screen origin');
assert(pointerAtEdge('bottom', monitorRect, 1, 10, 1439) && !pointerAtEdge('bottom', monitorRect, 1, 10, 1438),
    'the bottom edge band must end at the monitor edge');
assert(edgeActivation('high').band > edgeActivation('low').band &&
    edgeActivation('high').pressure < edgeActivation('low').pressure &&
    edgeActivation('nonsense').band === edgeActivation('medium').band,
    'higher sensitivity must react closer to the edge and to a lighter push');

const timer = new SettleTimer(150, 600);
assert(timer.delayFor(0) === 150 && timer.delayFor(100) === 150 && timer.delayFor(500) === 100 &&
    timer.delayFor(700) === 0, 'a burst of changes must settle, but never later than maxWait');
timer.settled();
assert(timer.delayFor(1000) === 150, 'a new burst must start a fresh quiet period');

const resolvedTop = resolveChrome({top_bar: {reveal_delay: -1, hide_delay: 99, animation_time: 'x',
    reveal_method: 'teleport', sensitivity: 'high', hide_when: 'focused', reveal_in_fullscreen: 'yes'}},
false).top_bar;
assert(resolvedTop.reveal_delay === 0 && resolvedTop.hide_delay === 5 &&
    resolvedTop.animation_time === CHROME_DEFAULTS.top_bar.animation_time &&
    resolvedTop.reveal_method === 'hover' && resolvedTop.sensitivity === 'high' &&
    resolvedTop.hide_when === 'focused' && resolvedTop.reveal_in_fullscreen === false,
    'top-bar behavior settings were not normalized');
const screen = {x: 0, y: 0, width: 1200, height: 850};
assert(pointerInPanelCorridor('top', screen, {x: 0, y: 0, width: 1200, height: 32}, 600, 0) &&
    pointerInPanelCorridor('top', screen, {x: 0, y: 0, width: 1200, height: 32}, 600, 31) &&
    !pointerInPanelCorridor('top', screen, {x: 0, y: 0, width: 1200, height: 32}, 600, 32),
    'top reveal corridor must remain stable while the actor moves');
assert(pointerInPanelCorridor('bottom', screen,
    {x: 0, y: 810, width: 1200, height: 40}, 600, 849) &&
    !pointerInPanelCorridor('bottom', screen,
        {x: 0, y: 810, width: 1200, height: 40}, 600, 809),
    'bottom reveal corridor must include the screen edge and shown panel');

const stories = Array.from({length: 8}, (_, index) => `story-${index + 1}`);

assert(newsFilterSignature() === '[[],[]]', 'Missing news filters must mean all topics');
assert(newsFilterSignature({topics: null}) === '[[],[]]', 'Null news filters must match provider defaults');
assert(newsFilterSignature({topic_presets: ['ai'], topics: ['café', 'a "quote"']}) ===
    '[["ai"],["café","a \\"quote\\""]]', 'Filter signature must agree with the Python provider');
assert(newsOptions({version: 2, providers: {news: {topic_presets: []}}}).topic_presets === NEWS_TOPIC_IDS,
    'Legacy unrestricted news must migrate to all topics');
assert(newsOptions({version: 3, providers: {news: {topic_presets: []}}}).topic_presets.length === 0,
    'A current empty selection must remain off');

// Files staged on the CLIVE card, before any of it reaches the service.

assert(PAGE_SIZE === 3, 'paged widgets must begin with a three-row fallback');
assert(ROTATION_SECONDS === 12, 'paged widgets must rotate every 12 seconds');
assert(pageCount([], 3) === 1, 'empty news should retain one stable page');
assert(pageCount(stories, 3) === 3, 'eight stories should make three pages');

const first = pageItems(stories, 0);
assert(first.items.join(',') === 'story-1,story-2,story-3',
    'first page does not contain the newest three stories');
assert(first.start === 0 && first.end === 3 && first.total === 8,
    'first page range is incorrect');

const last = pageItems(stories, 2);
assert(last.items.join(',') === 'story-7,story-8',
    'partial final page is incorrect');
assert(last.start === 6 && last.end === 8, 'partial page range is incorrect');
assert(nextPage(2, stories) === 0, 'news pages do not wrap');
assert(normalizePage(-1, stories) === 2, 'negative pages do not normalize');

const quotes = ['AAPL', 'MSFT', 'NVDA', 'AMD'];
const firstQuotes = pageItems(quotes, 0);
const lastQuotes = pageItems(quotes, 1);
assert(firstQuotes.items.join(',') === 'AAPL,MSFT,NVDA',
    'market pages did not preserve configured symbol order');
assert(lastQuotes.items.join(',') === 'AMD' && lastQuotes.start === 3 && lastQuotes.end === 4,
    'market partial page is incorrect');
assert(nextPage(1, quotes) === 0, 'market pages do not wrap');

assert(GRID_SIZE === 8 && WIDGET_GAP === 8 && SNAP_DISTANCE === 12,
    'layout density constants changed unexpectedly');
const monitor = {x: 0, y: 0, width: 1000, height: 800};
const peer = {x: 200, y: 120, width: 200, height: 160};
const workArea = {x: 0, y: 32, width: 920, height: 720};

let clamped = settlePosition(workArea, 40, 0, 200, 100);
assert(clamped[0] === 40 && clamped[1] === 32,
    'widgets must remain below the top-panel work-area inset');
clamped = settlePosition(workArea, 900, 740, 200, 100);
assert(clamped[0] === 720 && clamped[1] === 652,
    'widgets must remain inside right and bottom work-area insets');

let position = settlePosition(monitor, 73, 75, 160, 100);
assert(position[0] === 72 && position[1] === 72,
    'free position did not settle onto the monitor grid');
position = settlePosition(monitor, 5, 693, 160, 100);
assert(position[0] === 0 && position[1] === 700,
    'card did not magnetize to monitor edges');
position = settlePosition(monitor, 407, 123, 160, 100, [peer]);
assert(position[0] === 408 && position[1] === 120,
    'card did not use the compact neighbor gutter and aligned top edge');
position = settlePosition(monitor, 226, 500, 160, 100, [peer]);
assert(position[0] === 220,
    'card did not magnetize to a neighboring horizontal centre');
position = settlePosition(monitor, 204, 124, 160, 100, [peer]);
assert(position[0] === 200 && position[1] === 120,
    'overlap should remain available when the user chooses it');

let resized = settleResize(monitor, 20, 20, 197, 100, 1, 0, [peer]);
assert(resized[0] === 20 && resized[2] === 200,
    'resize did not magnetize to a neighboring width');
resized = settleResize(monitor, 20, 20, 175, 100, 1, 0, [peer]);
assert(resized[0] === 20 && resized[2] === 172,
    'resize did not stop at the neighbor gutter');
resized = settleResize(monitor, 20, 10, 200, 157, 0, 1, [peer]);
assert(resized[1] === 10 && resized[3] === 160,
    'resize did not magnetize to a neighboring height');
resized = settleResize(workArea, 20, 40, 200, 100, 0, -1);
assert(resized[1] === 32 && resized[3] === 108,
    'north resize crossed into the top panel');

assert(pageSizeForHeight(100, 28, 4, 3) === 3,
    'responsive paging did not count complete rows');
assert(pageSizeForHeight(50, 28, 4, 3) === 1,
    'short widgets must retain one complete data row');
assert(pageSizeForHeight(0, 28, 4, 3) === 3,
    'unallocated lists must retain their current fallback page size');

assert(meterFillWidth(200, null) === 0, 'missing meter data must be empty');
assert(meterFillWidth(200, -10) === 0, 'meter values must clamp below zero');
assert(meterFillWidth(200, 0) === 0, 'zero percent meter must be empty');
assert(meterFillWidth(200, 50) === 100, 'half-full meter width is incorrect');
assert(meterFillWidth(200, 100) === 200, 'full meter width is incorrect');
assert(meterFillWidth(200, 150) === 200, 'meter values must clamp above 100');

const widget = {x: 100, y: 100, width: 200, height: 100};
assert(rectanglesOverlap(widget, {x: 250, y: 50, width: 100, height: 100}),
    'partial overlap was not detected');
assert(rectanglesOverlap(widget, {x: 120, y: 120, width: 20, height: 20}),
    'contained rectangle was not detected');
assert(!rectanglesOverlap(widget, {x: 300, y: 100, width: 100, height: 100}),
    'touching edges should not count as overlap');
assert(!rectanglesOverlap(widget, {x: 0, y: 0, width: 50, height: 50}),
    'separate rectangles were reported as overlapping');

assert(widgetLayer(false, false) === 'background',
    'noninteractive widgets must remain in the background');
assert(widgetLayer(true, false) === 'chrome',
    'uncovered interactive widgets must receive input in chrome');
assert(widgetLayer(true, true) === 'background',
    'covered interactive widgets must move behind applications');

assert(!systemIsDark('default', 'dark'),
    'GNOME default appearance must resolve to light');
assert(!systemIsDark('prefer-light', 'dark'),
    'explicit light appearance must resolve to light');
assert(systemIsDark('prefer-dark', 'light'),
    'explicit dark appearance must resolve to dark');
assert(systemIsDark('unknown', 'dark'),
    'unknown appearance must fall back to the Shell variant');

assert(displayHostname('fedora') === 'Fedora',
    'Fedora default hostname was not capitalized for display');
assert(displayHostname('media-server') === 'media-server',
    'custom hostnames must not be rewritten');
assert(displayHostname(null) === 'System',
    'missing hostnames need a stable fallback');

assert(headlinePanDistance(200, 190) === 0,
    'a fitting headline must not ticker');
assert(headlinePanDistance(200, 296) === 96,
    'the overflow test must measure what the viewport clips');

assert(headlinePanDistance(200, 200) === 0,
    'an exactly fitting headline must stay still');
assert(headlinePanDistance(200, NaN) === 0,
    'an unmeasured headline must not animate');

assert(headlineTickerDuration(0) === 0,
    'a headline with no cycle must not animate');
assert(headlineTickerDuration(35) === 1200,
    'a short cycle must retain a readable minimum duration');
assert(headlineTickerDuration(700) === 10000,
    'the ticker must hold a steady reading pace on long headlines');
assert(headlineTickerDuration(1400) === 2 * headlineTickerDuration(700),
    'ticker speed must not vary with headline length');

assert(formatTemperature(51.4) === '51°C' && formatTemperature(100, 'fahrenheit') === '212°F' &&
    formatTemperature(undefined) === '--', 'temperatures must format in the chosen unit');
assert(thermalLevel({kind: 'disk', celsius: 41.9, high: 82.8}) === 'normal' &&
    thermalLevel({kind: 'disk', celsius: 75, high: 82.8}) === 'warm' &&
    thermalLevel({kind: 'disk', celsius: 83, high: 82.8}) === 'hot',
    'a reported sensor limit must decide warm and hot');
assert(thermalLevel({kind: 'cpu', celsius: 85, high: null}) === 'warm' &&
    thermalLevel({kind: 'cpu', celsius: 91}) === 'hot' &&
    thermalLevel({kind: 'cpu', celsius: 51}) === 'normal',
    'a sensor without a limit must use its kind\'s defaults');
const thermals = {sensors: [{kind: 'cpu'}, {kind: 'gpu'}, {kind: 'disk'}, {kind: 'wifi'}]};
assert(visibleSensors(thermals).length === 3 && visibleSensors(thermals, 'all').length === 4 &&
    visibleSensors(null).length === 0, 'the main sensors are CPU, GPU and drive');
assert(formatRate(762612) === '745K/s' && formatRate(512) === '512B/s' && formatRate(null) === '--',
    'rates must format with binary units');
const wifi = describeConnection({state: 'connected', kind: 'wifi', name: 'Home', ssid: 'Home',
    signal: 65, ipv4: '10.0.0.4', vpn: ['ProtonVPN']});
assert(wifi.title === 'Home · 65%' && wifi.detail === '10.0.0.4 · VPN ProtonVPN' &&
    wifi.icon === 'network-vpn-symbolic', `Wi-Fi with a VPN was not described: ${JSON.stringify(wifi)}`);
assert(describeConnection({state: 'connected', kind: 'ethernet', name: 'Wired 1', ipv4: '10.0.0.5'},
    {showIp: false}).detail === '', 'the IP address must stay hidden when turned off');
assert(describeConnection({state: 'disconnected'}).title === 'Offline' &&
    describeConnection(undefined).title === 'Network unavailable',
    'a missing or disconnected network must say so');

const {ACTIVE_STATUSES, attachmentDetails, composerKey, confirmationRows, modelLabel, modelMenuSections,
    pathsFromUriList} = await import('../extension/cliveLogic.js');
const selection = {endpoint: 'cloud', cloud_model: 'gemma4:31b', local_model: 'qwen3.5:4b',
    cloud_models: ['gemma4:31b', 'gpt-oss:120b'], local_models: ['qwen3.5:4b'], cloud_ready: true};
assert(modelLabel({status: 'idle', selection}).text === 'Cloud · gemma4:31b' &&
    modelLabel({status: 'idle', selection}).cloud,
    'the model pill must show the selection before any task has run');
assert(modelLabel({status: 'running', mode: 'local', model: 'qwen3.5:4b', selection}).text ===
    'Local · qwen3.5:4b', 'a running task must show the model it actually reached (fallback)');
assert(modelLabel({}).text === 'Your desktop assistant', 'no selection yet must read as the tagline');
const sections = modelMenuSections(selection);
assert(sections[0].items[0].active && !sections[0].items[1].active && !sections[1].items[0].active,
    'only the selected model on the selected endpoint is checked');
const notReady = modelMenuSections({...selection, endpoint: 'local', cloud_ready: false});
assert(!notReady[0].ready && notReady[0].items.every(item => !item.sensitive) &&
    notReady[1].items[0].active, 'cloud models must be unavailable until the cloud is set up');

assert(ACTIVE_STATUSES.includes('awaiting_confirmation'),
    'a task waiting for confirmation must count as active everywhere');
const rows = confirmationRows({calls: [{id: 'a', tool: 'file_trash', capability: 'Move files to Trash',
    app_name: 'Files', target: '/home/me/note.txt'}, {id: 'b', tool: 'x'}]});
assert(rows[0].title === 'Move files to Trash' && rows[0].detail === 'Files · /home/me/note.txt' &&
    rows[1].title === 'x' && rows[1].detail === '' && confirmationRows(null).length === 0,
    'confirmation rows must say what will happen and where');

assert(attachmentDetails({label: 'PDF', pages: 3, size: 1536}) === 'PDF · 3 p. · 1.5 KB' &&
    attachmentDetails({label: 'Text', size: 200}) === '200 B' && attachmentDetails(null) === '',
    'staged-file details must name the kind, pages and size');
assert(JSON.stringify(pathsFromUriList('# comment\r\nfile:///a.txt\nsftp://host/b.txt\n',
    uri => uri.startsWith('file://') ? uri.slice(7) : null)) === '["/a.txt"]',
    'pasted file lists must keep local files only');
assert(composerKey(0xff0d, 0) === 'send' && composerKey(0xff0d, 1) === null &&
    composerKey(0x76, 4) === 'paste' && composerKey(0x76, 0) === null,
    'Enter sends, Shift+Enter makes a new line, Ctrl+V pastes');

const {actionRows, markdownLite} = await import('../extension/cliveLogic.js');
const rendered = markdownLite('# Result\n- **Bold** and `code`\nSee [the docs](https://example.com/a?b=1&c=2) or https://gnome.org.\n<b>not markup</b>');
assert(rendered.markup.includes('<b>Result</b>') && rendered.markup.includes('• <b>Bold</b>') &&
    rendered.markup.includes('<tt>code</tt>') && rendered.markup.includes('<u>the docs</u>') &&
    rendered.markup.includes('&lt;b&gt;not markup&lt;/b&gt;'),
    `Markdown-lite rendering was wrong or unsafe: ${rendered.markup}`);
assert(rendered.links.length === 2 && rendered.links[0].url === 'https://example.com/a?b=1&c=2' &&
    rendered.links[1].url.startsWith('https://gnome.org'),
    `Links were not collected: ${JSON.stringify(rendered.links)}`);
assert(markdownLite('[x](javascript:alert(1))').links.length === 0, 'only web links may become buttons');
const summarized = actionRows([{tool: 'a'}, {app_name: 'Files', capability: 'Read files', target: '/x', status: 'done'},
    {app_name: 'Gmail', capability: 'Send emails', status: 'declined'}, {app_name: 'Terminal', tool: 'terminal_run', status: 'blocked'}]);
assert(summarized.length === 3 && summarized[0].text === '✓ Files · Read files — /x' &&
    summarized[1].text === '✕ Gmail · Send emails' && summarized[2].text.startsWith('⛔ Terminal'),
    `Action rows were wrong: ${JSON.stringify(summarized)}`);

// Window rules: the same fixtures the Python matcher runs.
const {default: GioForRules} = await import('gi://Gio');
const {actionsFor, matchesRule} = await import('../extension/rulesLogic.js');
const fixturePath = GioForRules.File.new_for_uri(import.meta.url).get_parent().get_child('rules_fixtures.json');
const [, fixtureBytes] = fixturePath.load_contents(null);
for (const fixture of JSON.parse(new TextDecoder().decode(fixtureBytes))) {
    assert(matchesRule(fixture.rule, fixture.window) === fixture.matches,
        `rule fixture "${fixture.name}" should ${fixture.matches ? '' : 'not '}match`);
}
assert(!matchesRule({match: {title: '(', title_regex: true}}, {title: '('}), 'a broken pattern matches nothing');
const badPattern = {match: {title: '(', title_regex: true}};
assert(!matchesRule(badPattern, {title: '('}) && !matchesRule(badPattern, {title: '('}),
    'an unreadable title pattern matches nothing, also from the cache');
const pattern = {match: {title: '^Steam$', title_regex: true}};
assert(matchesRule(pattern, {title: 'Steam'}) && matchesRule(pattern, {title: 'Steam'}) &&
    !matchesRule(pattern, {title: 'steam'}), 'cached patterns give the same answer every time');
const combined = actionsFor([
    {enabled: false, match: {app: 'a'}, actions: {workspace: 9}},
    {match: {app: 'a'}, actions: {workspace: 2, mode: 'default'}},
    {match: {app: 'a'}, actions: {workspace: 5, mode: 'float', no_effects: true}},
], {desktop_id: 'a.desktop'});
assert(combined.workspace === 2 && combined.mode === 'float' && combined.no_effects === true,
    `earlier rules must win per action, disabled ones never: ${JSON.stringify(combined)}`);

// Tiling and snapping geometry.
const tiling = await import('../extension/tilingLogic.js');
const tileArea = {x: 0, y: 32, width: 1200, height: 800};
const inside = (rects, area) => rects.every(r => r.x >= area.x && r.y >= area.y &&
    r.x + r.width <= area.x + area.width && r.y + r.height <= area.y + area.height);
const apart = rects => rects.every((a, i) => rects.every((b, j) => i === j ||
    a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y));
for (const layout of tiling.LAYOUTS) {
    for (const count of [1, 2, 3, 4, 5, 7]) {
        const rects = tiling.layoutRects(layout, count, tileArea, {inner: 8, outer: 12, smart: true, ratio: 0.6});
        assert(rects.length === count, `${layout} × ${count} gave ${rects.length} rectangles`);
        assert(inside(rects, tileArea), `${layout} × ${count} left the work area: ${JSON.stringify(rects)}`);
        if (layout !== 'monocle' && layout !== 'scrolling')
            assert(apart(rects), `${layout} × ${count} overlaps: ${JSON.stringify(rects)}`);
    }
}
const scrolled = tiling.layoutRects('scrolling', 5, tileArea, {inner: 8, outer: 12, columns: 2, first: 2});
assert(apart([scrolled[2], scrolled[3]]) && tiling.sameRect(scrolled[0], scrolled[2]) &&
    tiling.sameRect(scrolled[1], scrolled[2]) && tiling.sameRect(scrolled[4], scrolled[3]),
    `scrolling shows its columns side by side and parks the rest under the edges: ${JSON.stringify(scrolled)}`);
assert(tiling.scrollFirst(0, 3, 4, 2) === 2 && tiling.scrollFirst(2, 0, 4, 2) === 0 &&
    tiling.scrollFirst(1, 1, 4, 2) === 1 && tiling.scrollFirst(3, -1, 3, 2) === 1,
    'scrolling scrolls just far enough to show the focused column');
const centred = tiling.layoutRects('centered', 3, tileArea, {inner: 10, outer: 0, ratio: 0.5});
assert(centred[2].x < centred[0].x && centred[0].x + centred[0].width < centred[1].x &&
    centred[1].x + centred[1].width === 1200, `centered puts the main window between the stacks: ${JSON.stringify(centred)}`);
for (const [layout, count] of [['master', 3], ['centered', 4]]) {
    const options = {inner: 7, outer: 13, ratio: 0.62};
    const rects = tiling.layoutRects(layout, count, tileArea, options);
    for (const index of [0, 1]) {
        const ratio = tiling.resizedRatio(layout, count, index, rects[index], tileArea, options);
        assert(Math.abs(ratio - 0.62) < 0.01, `${layout} resize ratio round trip for window ${index}: ${ratio}`);
    }
}
assert(tiling.resizedRatio('columns', 3, 0, tileArea, tileArea) === null, 'only main areas resize');
assert(tiling.stepIndex('monocle', tiling.layoutRects('monocle', 3, tileArea), 0, 'right') === 1 &&
    tiling.stepIndex('monocle', tiling.layoutRects('monocle', 3, tileArea), 0, 'left') === -1 &&
    tiling.stepIndex('scrolling', scrolled, 4, 'right') === -1 && tiling.stepIndex('scrolling', scrolled, 4, 'left') === 3,
    'monocle and scrolling step through the order');

// Dwindle keeps a tree: new windows split the one they opened from.
let tree = tiling.dwindleSync(null, ['a', 'b']);
tree = tiling.dwindleSync(tree, ['a', 'b', 'c'], ['a']);
let placed = tiling.dwindleRects(tree, tileArea, {inner: 0, outer: 0});
assert(placed.get('b').x === 600 && placed.get('b').height === 800 && placed.get('a').width === 600 &&
    placed.get('c').x === 0 && placed.get('c').y === 432,
    `a new window splits its anchor along the longer side: ${JSON.stringify([...placed])}`);
placed = tiling.dwindleRects(tree, tileArea, {inner: 0, outer: 0}, new Set(['b', 'c']));
assert(!placed.has('a') && placed.get('c').y === 32 && placed.get('c').height === 800,
    'a hidden window takes no room but keeps its leaf');
tree = tiling.dwindleSync(tree, ['b', 'c']);
placed = tiling.dwindleRects(tree, tileArea, {inner: 0, outer: 0});
assert(placed.get('c').width === 600 && placed.get('c').height === 800, 'a closed window gives its room to its sibling');
tiling.dwindleSwap(tree, 'b', 'c');
assert(tiling.dwindleRects(tree, tileArea, {inner: 0, outer: 0}).get('c').x === 600, 'dwindle swap');
assert(tiling.dwindleNudge(tree, 'b', 0.1) &&
    tiling.dwindleRects(tree, tileArea, {inner: 0, outer: 0}).get('b').width === 720, 'dwindle nudge grows a window');
tiling.dwindleRatio(tree, 'c', {x: 0, y: 0, width: 300, height: 800});
assert(tiling.dwindleRects(tree, tileArea, {inner: 0, outer: 0}).get('c').width === 300, 'dwindle mouse resize');
const spiral = tiling.layoutRects('dwindle', 4, tileArea, {inner: 0, outer: 0});
assert(spiral[0].width === 600 && spiral[1].height === 400 && spiral[2].width === 300,
    `dwindle spirals: ${JSON.stringify(spiral)}`);
const single = tiling.layoutRects('master', 1, tileArea, {inner: 8, outer: 12, smart: true});
assert(tiling.sameRect(single[0], tileArea), `smart gaps must drop the gaps for one window: ${JSON.stringify(single)}`);
const spaced = tiling.layoutRects('master', 1, tileArea, {inner: 8, outer: 12, smart: false});
assert(spaced[0].x === 12 && spaced[0].y === 44 && spaced[0].width === 1176, 'outer gaps without smart gaps');
const halves = tiling.layoutRects('master', 2, tileArea, {inner: 10, outer: 0, ratio: 0.5});
assert(halves[1].x - (halves[0].x + halves[0].width) === 10 && halves[0].width === halves[1].width,
    `main and stack must be split evenly with the gap between: ${JSON.stringify(halves)}`);
const corner = tiling.snapRect('bottom-right', tileArea, 10);
assert(corner.x === 605 && corner.y === 437 && corner.x + corner.width === 1190 &&
    corner.y + corner.height === 822, `snap quarter with gaps: ${JSON.stringify(corner)}`);
const display = {x: 0, y: 0, width: 1200, height: 850};
assert(tiling.zoneAt(2, 400, display) === 'left' && tiling.zoneAt(1199, 400, display) === 'right' &&
    tiling.zoneAt(3, 10, display) === 'top-left' && tiling.zoneAt(1198, 840, display) === 'bottom-right' &&
    tiling.zoneAt(600, 2, display) === 'maximize' && tiling.zoneAt(600, 400, display) === null &&
    tiling.zoneAt(2, 10, display, {quarters: false}) === 'left', 'snap zones by pointer position');
const rightTile = {x: 768, y: 40, width: 768, height: 824};
const laptopArea = {x: 0, y: 40, width: 1536, height: 824};
const fits = tiling.keptInside(rightTile, {width: 760, height: 820}, laptopArea);
const tooWide = tiling.keptInside(rightTile, {width: 802, height: 900}, laptopArea);
assert(fits.x === 768 && fits.y === 40 && tooWide.x === 734 && tooWide.y === 40,
    `a window wider than its tile stays inside the work area: ${JSON.stringify([fits, tooWide])}`);
const stacked = tiling.layoutRects('master', 3, tileArea, {inner: 0, outer: 0, ratio: 0.5});
assert(tiling.neighbour(stacked, 0, 'right') === 1 && tiling.neighbour(stacked, 1, 'down') === 2 &&
    tiling.neighbour(stacked, 2, 'left') === 0 && tiling.neighbour(stacked, 0, 'left') === -1,
    'directional neighbours in main and stack');
assert(tiling.insertionIndex(3, 1, 'after_focus') === 2 && tiling.insertionIndex(3, 1, 'master') === 0 &&
    tiling.insertionIndex(3, 1, 'end') === 3, 'new window placement');

// The widget grid follows Customize, and goes back to the defaults.
const layoutModule = await import('../extension/layoutLogic.js');
layoutModule.setLayoutTuning({grid: 16, snap_distance: 0});
assert(layoutModule.GRID_SIZE === 16 && layoutModule.SNAP_DISTANCE === 0, 'layout tuning did not apply');
layoutModule.setLayoutTuning({grid: 'x', snap_distance: 500});
assert(layoutModule.GRID_SIZE === 8 && layoutModule.SNAP_DISTANCE === 48, 'layout tuning must validate');
layoutModule.setLayoutTuning();
assert(layoutModule.GRID_SIZE === 8 && layoutModule.SNAP_DISTANCE === 12, 'layout tuning must reset');
