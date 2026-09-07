import {systemIsDark} from '../extension/themeLogic.js';
import {
    addAttachmentPaths, displayHostname, headlinePanDistance, headlineTickerDuration,
    rectanglesOverlap, widgetLayer, newsFilterSignature, newsOptions, NEWS_TOPIC_IDS,
    MAX_ATTACHMENTS,
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
assert(MAX_ATTACHMENTS === 8, 'the card must stage the same 8 files the service reads');
assert(JSON.stringify(addAttachmentPaths([], ['/home/a/one.md', '/home/a/two.png']).paths) ===
    '["/home/a/one.md","/home/a/two.png"]', 'Picked files must stage in the order they were chosen');
assert(JSON.stringify(addAttachmentPaths(['/home/a/one.md'], ['/home/a/one.md']).paths) ===
    '["/home/a/one.md"]', 'Picking a staged file again must not stage it twice');
assert(JSON.stringify(addAttachmentPaths(['/home/a/one.md'], [null, 7, '', '/home/a/two.md']).paths) ===
    '["/home/a/one.md","/home/a/two.md"]', 'Only real paths may be staged');
assert(!addAttachmentPaths([], []).overflow, 'An empty pick cannot overflow');

const eight = Array.from({length: MAX_ATTACHMENTS}, (_, index) => `/home/a/file-${index}.md`);
const exact = addAttachmentPaths([], eight);
assert(exact.paths.length === MAX_ATTACHMENTS && !exact.overflow,
    'Exactly the ceiling must stage without reporting overflow');
const ninth = addAttachmentPaths(eight, ['/home/a/extra.md']);
assert(ninth.paths.length === MAX_ATTACHMENTS && ninth.overflow,
    'A file past the ceiling must be turned away and reported');
assert(!ninth.paths.includes('/home/a/extra.md'), 'The turned-away file must not be staged');
// A duplicate at the ceiling is already staged, so it is not an overflow.
assert(!addAttachmentPaths(eight, ['/home/a/file-0.md']).overflow,
    'Re-picking a staged file at the ceiling must not be reported as overflow');

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
