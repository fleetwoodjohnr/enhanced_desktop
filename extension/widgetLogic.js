/** Pure helpers kept outside Shell imports so layer policy can be unit tested. */

export function rectanglesOverlap(first, second) {
    return first.x < second.x + second.width &&
        first.x + first.width > second.x &&
        first.y < second.y + second.height &&
        first.y + first.height > second.y;
}

/** Mirrors attachments.MAX_FILES; the service is what actually enforces it. */
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

// -- system widget ---------------------------------------------------------

export const MAIN_THERMAL_KINDS = Object.freeze(['cpu', 'gpu', 'disk']);

// Where a reading starts to look warm or hot when the sensor reports no
// limit of its own (Ryzen's Tctl and AMD graphics report none).
const THERMAL_DEFAULTS = Object.freeze({
    cpu: [80, 90], gpu: [80, 95], disk: [60, 70], board: [70, 85], wifi: [70, 85],
});

export function formatTemperature(celsius, unit = 'celsius') {
    if (!Number.isFinite(celsius))
        return '--';
    return unit === 'fahrenheit'
        ? `${Math.round(celsius * 9 / 5 + 32)}°F` : `${Math.round(celsius)}°C`;
}

/** 'normal', 'warm' (within 10° of the limit) or 'hot' (at or past it). */
export function thermalLevel(sensor) {
    const celsius = sensor?.celsius;
    if (!Number.isFinite(celsius))
        return 'normal';
    const high = Number.isFinite(sensor.high) ? sensor.high : null;
    const [warm, hot] = high !== null
        ? [high - 10, high] : THERMAL_DEFAULTS[sensor.kind] ?? [75, 90];
    if (celsius >= hot)
        return 'hot';
    return celsius >= warm ? 'warm' : 'normal';
}

/** The sensors a card shows: the headline three, or every chip. */
export function visibleSensors(thermals, which = 'main') {
    const sensors = Array.isArray(thermals?.sensors) ? thermals.sensors : [];
    return which === 'all' ? sensors
        : sensors.filter(sensor => MAIN_THERMAL_KINDS.includes(sensor.kind));
}

export function formatRate(bytesPerSecond) {
    if (!Number.isFinite(bytesPerSecond))
        return '--';
    const units = ['B', 'K', 'M', 'G'];
    let value = bytesPerSecond;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
    }
    return `${value < 10 ? value.toFixed(1) : Math.round(value)}${units[index]}/s`;
}

/** Title, detail line and icon for the network section. */
export function describeConnection(network, {showIp = true} = {}) {
    if (!network || network.state === 'disconnected' || !network.state)
        return {title: network ? 'Offline' : 'Network unavailable', detail: '',
            icon: 'network-offline-symbolic'};
    if (network.state === 'connecting')
        return {title: 'Connecting…', detail: '', icon: 'network-wireless-acquiring-symbolic'};
    let title = network.name ?? network.interface ?? 'Connected';
    let icon = 'network-wired-symbolic';
    if (network.kind === 'wifi') {
        title = network.ssid ?? title;
        const signal = Number.isFinite(network.signal) ? network.signal : null;
        const level = signal === null ? 'good' : signal >= 75 ? 'excellent'
            : signal >= 50 ? 'good' : signal >= 25 ? 'ok' : 'weak';
        icon = `network-wireless-signal-${level}-symbolic`;
        if (signal !== null)
            title = `${title} · ${signal}%`;
    } else if (network.kind === 'ethernet') {
        title = `Wired · ${title}`;
    } else if (network.kind === 'mobile') {
        icon = 'network-cellular-signal-good-symbolic';
    }
    const vpn = Array.isArray(network.vpn) ? network.vpn.filter(Boolean) : [];
    const parts = [];
    if (showIp && network.ipv4)
        parts.push(network.ipv4);
    if (vpn.length)
        parts.push(`VPN ${vpn.join(', ')}`);
    if (network.metered)
        parts.push('Metered');
    if (vpn.length)
        icon = 'network-vpn-symbolic';
    return {title, detail: parts.join(' · '), icon};
}
