/** Pure helpers kept outside Shell imports so layer policy can be unit tested. */

export function rectanglesOverlap(first, second) {
    return first.x < second.x + second.width &&
        first.x + first.width > second.x &&
        first.y < second.y + second.height &&
        first.y + first.height > second.y;
}

/** Mirrors attachments.MAX_FILES; the service is what actually enforces it. */
export const MAX_ATTACHMENTS = 8;

/**
 * Merge newly picked files into the staged list.
 *
 * The picker can hand back a file that is already staged, and the user can
 * keep picking past the ceiling. Order is the order they were chosen in, and
 * `overflow` says whether anything had to be turned away, so the card can tell
 * the user rather than dropping files quietly.
 */
export function addAttachmentPaths(existing, picked, max = MAX_ATTACHMENTS) {
    const paths = Array.isArray(existing) ? existing.filter(one => typeof one === 'string' && one) : [];
    let overflow = false;
    for (const path of Array.isArray(picked) ? picked : []) {
        if (typeof path !== 'string' || !path || paths.includes(path))
            continue;
        if (paths.length >= max) {
            overflow = true;
            continue;
        }
        paths.push(path);
    }
    return {paths, overflow};
}

/** Choose the Shell layer without importing Shell modules into unit tests. */
export function widgetLayer(interactive, covered) {
    if (interactive && !covered)
        return 'chrome';
    return 'background';
}

/** Preserve real hostnames while presenting Fedora's default one as a name. */
export function displayHostname(hostname) {
    const value = typeof hostname === 'string' ? hostname.trim() : '';
    if (!value)
        return 'System';
    return value.toLocaleLowerCase() === 'fedora' ? 'Fedora' : value;
}

/** Number of horizontal pixels hidden by a clipped headline viewport. */
export function headlinePanDistance(viewportWidth, naturalWidth) {
    const viewport = Number.isFinite(viewportWidth) ? Math.max(0, viewportWidth) : 0;
    const natural = Number.isFinite(naturalWidth) ? Math.max(0, naturalWidth) : 0;
    return Math.max(0, natural - viewport);
}

/** A continuous ticker has to stay readable, so it runs slower than a reveal. */
export function headlineTickerDuration(cycleWidth, pixelsPerSecond = 70) {
    const speed = Number.isFinite(pixelsPerSecond) ? Math.max(1, pixelsPerSecond) : 70;
    const cycle = Number.isFinite(cycleWidth) ? Math.max(0, cycleWidth) : 0;
    return cycle ? Math.max(1200, Math.round(cycle / speed * 1000)) : 0;
}

/** Mirrors the provider's compact JSON request signature, without case folding. */
export function newsFilterSignature(options = {}) {
    return JSON.stringify([options.topic_presets ?? [], options.topics ?? []]);
}

// Keep these defaults in agreement with desktop_forge/config.py. The parity
// test compares actual provider signatures, including omitted settings.
export const NEWS_TOPIC_IDS = [
    'world', 'us', 'politics', 'business', 'technology', 'ai',
    'science', 'health', 'sports', 'linux',
];
export const DEFAULT_NEWS_FEEDS = [
    'https://feeds.npr.org/1003/rss.xml',
    'https://feeds.npr.org/1006/rss.xml',
    'https://feeds.npr.org/1004/rss.xml',
    'https://feeds.npr.org/1019/rss.xml',
    'https://news.mit.edu/rss/topic/artificial-intelligence2',
    'https://www.phoronix.com/rss.php',
];

export function newsRequestSignature(options = {}) {
    return JSON.stringify([
        3, options.feeds ?? DEFAULT_NEWS_FEEDS,
        options.topic_presets ?? NEWS_TOPIC_IDS, options.topics ?? [], options.max_items ?? 40,
    ]);
}

export function newsOptions(config) {
    const options = {...config.providers?.news};
    if ((config.version ?? 1) < 3 && !options.topic_presets?.length)
        options.topic_presets = NEWS_TOPIC_IDS;
    return options;
}
