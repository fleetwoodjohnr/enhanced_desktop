/** Pure shell-chrome configuration and CSS helpers, kept free of Shell imports. */

export const TOP_BAR_MODES = Object.freeze(['always', 'intelligent', 'auto']);
export const HIDE_WHEN = Object.freeze(['any', 'focused', 'maximized']);
export const REVEAL_METHODS = Object.freeze(['hover', 'pressure']);
export const SENSITIVITIES = Object.freeze(['low', 'medium', 'high']);

// Mirrored by desktop_forge/config.py DEFAULT_CHROME; tests/test_config.py
// compares the two so they cannot drift apart.
export const CHROME_DEFAULTS = Object.freeze({
    top_bar: Object.freeze({
        visibility: 'always',
        position: 'top',
        height: 32,
        opacity: 0.96,
        foreground_mode: 'auto',
        reveal_delay: 0.1,
        hide_delay: 0.5,
        animation_time: 0.2,
        reveal_method: 'hover',
        sensitivity: 'medium',
        hide_when: 'any',
        reveal_in_fullscreen: false,
    }),
    dock: Object.freeze({
        opacity: 0.92,
        foreground_mode: 'auto',
    }),
});

const PALETTES = Object.freeze({
    light: Object.freeze({
        top_bar: Object.freeze({background: '#fafafb', foreground: '#222226'}),
        dock: Object.freeze({background: '#f8fbff', foreground: '#172033'}),
    }),
    dark: Object.freeze({
        top_bar: Object.freeze({background: '#18181b', foreground: '#ffffff'}),
        dock: Object.freeze({background: '#18202c', foreground: '#f7faff'}),
    }),
});

export function normalizeHex(value, fallback) {
    return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
        ? value.toLowerCase() : fallback;
}

function clampNumber(value, minimum, maximum, fallback) {
    const number = Number(value);
    return Number.isFinite(number)
        ? Math.max(minimum, Math.min(maximum, number)) : fallback;
}

function channel(value) {
    value /= 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

export function automaticForeground(background) {
    const value = normalizeHex(background, '#000000');
    const luminance = 0.2126 * channel(parseInt(value.slice(1, 3), 16)) +
        0.7152 * channel(parseInt(value.slice(3, 5), 16)) +
        0.0722 * channel(parseInt(value.slice(5, 7), 16));
    const white = 1.05 / (luminance + 0.05);
    const black = (luminance + 0.05) / 0.05;
    return white >= black ? '#ffffff' : '#000000';
}

export function resolveChrome(configured, dark = false) {
    const palette = PALETTES[dark ? 'dark' : 'light'];
    const rawTop = configured?.top_bar && typeof configured.top_bar === 'object'
        ? configured.top_bar : {};
    const rawDock = configured?.dock && typeof configured.dock === 'object'
        ? configured.dock : {};
    const topBackground = normalizeHex(rawTop.background, palette.top_bar.background);
    const dockBackground = normalizeHex(rawDock.background, palette.dock.background);
    const topMode = rawTop.foreground_mode === 'custom' ? 'custom' : 'auto';
    const dockMode = rawDock.foreground_mode === 'custom' ? 'custom' : 'auto';
    const defaults = CHROME_DEFAULTS.top_bar;

    return {
        top_bar: {
            visibility: TOP_BAR_MODES.includes(rawTop.visibility)
                ? rawTop.visibility : defaults.visibility,
            position: ['top', 'bottom'].includes(rawTop.position)
                ? rawTop.position : defaults.position,
            height: Math.round(clampNumber(rawTop.height, 24, 64, defaults.height)),
            background: topBackground,
            opacity: clampNumber(rawTop.opacity, 0, 1, defaults.opacity),
            foreground_mode: topMode,
            foreground: topMode === 'auto'
                ? automaticForeground(topBackground)
                : normalizeHex(rawTop.foreground, palette.top_bar.foreground),
            reveal_delay: clampNumber(rawTop.reveal_delay, 0, 2, defaults.reveal_delay),
            hide_delay: clampNumber(rawTop.hide_delay, 0, 5, defaults.hide_delay),
            animation_time: clampNumber(rawTop.animation_time, 0, 1, defaults.animation_time),
            reveal_method: REVEAL_METHODS.includes(rawTop.reveal_method)
                ? rawTop.reveal_method : defaults.reveal_method,
            sensitivity: SENSITIVITIES.includes(rawTop.sensitivity)
                ? rawTop.sensitivity : defaults.sensitivity,
            hide_when: HIDE_WHEN.includes(rawTop.hide_when)
                ? rawTop.hide_when : defaults.hide_when,
            reveal_in_fullscreen: typeof rawTop.reveal_in_fullscreen === 'boolean'
                ? rawTop.reveal_in_fullscreen : defaults.reveal_in_fullscreen,
        },
        dock: {
            background: dockBackground,
            opacity: clampNumber(
                rawDock.opacity, 0, 1, CHROME_DEFAULTS.dock.opacity),
            foreground_mode: dockMode,
            foreground: dockMode === 'auto'
                ? automaticForeground(dockBackground)
                : normalizeHex(rawDock.foreground, palette.dock.foreground),
        },
    };
}

function rgb(hex) {
    return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16));
}

function rgba(hex, opacity) {
    const [red, green, blue] = rgb(hex);
    return `rgba(${red},${green},${blue},${opacity})`;
}

export function chromeStylesheet(chrome) {
    const panel = chrome.top_bar;
    const dock = chrome.dock;
    const panelBorder = panel.position === 'bottom' ? 'top' : 'bottom';
    const oppositeBorder = panel.position === 'bottom' ? 'bottom' : 'top';
    const panelHover = rgba(panel.foreground, 0.10);
    const panelActive = rgba(panel.foreground, 0.17);
    const dockHover = rgba(dock.foreground, 0.10);
    const dockActive = rgba(dock.foreground, 0.17);
    return `
#panel {
    background-color: ${rgba(panel.background, panel.opacity)} !important;
    color: ${panel.foreground} !important;
    border-${panelBorder}-width: 1px !important;
    border-${panelBorder}-color: ${rgba(panel.foreground, 0.10)} !important;
    border-${oppositeBorder}-width: 0 !important;
    box-shadow: none !important;
}
#panel .panel-button { color: ${panel.foreground} !important; box-shadow: none; }
#panel .panel-button:hover, #panel .panel-button:focus { background-color: ${panelHover} !important; }
#panel .panel-button:active, #panel .panel-button:checked { background-color: ${panelActive} !important; }
#panel .panel-button.clock-display .clock { color: ${panel.foreground} !important; box-shadow: none; }
#panel .panel-button.clock-display:hover .clock,
#panel .panel-button.clock-display:focus .clock { background-color: ${panelHover} !important; }
#panel .panel-button.clock-display:active .clock,
#panel .panel-button.clock-display:checked .clock { background-color: ${panelActive} !important; }
#panel .panel-button#panelActivities .workspace-dot { background-color: ${panel.foreground} !important; }
#panel .panel-button.screen-recording-indicator {
    color: #ffffff !important; background-color: #c01c28 !important;
}
#panel .panel-button.screen-sharing-indicator {
    color: #ffffff !important; background-color: #e66100 !important;
}
#panel:overview, #panel.unlock-screen, #panel.login-screen {
    background-color: transparent !important; border-color: transparent !important;
}
#panel:overview .panel-button, #panel.unlock-screen .panel-button,
#panel.login-screen .panel-button { color: #fafafb !important; }
#dashtodockContainer #dash .dash-background {
    background-color: ${rgba(dock.background, dock.opacity)} !important;
    border-color: ${rgba(dock.foreground, 0.12)} !important;
    box-shadow: none !important;
}
#dashtodockContainer #dash .dash-separator { background-color: ${rgba(dock.foreground, 0.14)} !important; }
#dashtodockContainer #dash .show-apps .overview-icon,
#dashtodockContainer #dash .overview-tile .overview-icon {
    color: ${dock.foreground} !important; background-color: transparent;
}
#dashtodockContainer #dash .show-apps:hover .overview-icon,
#dashtodockContainer #dash .overview-tile:hover .overview-icon { background-color: ${dockHover} !important; }
#dashtodockContainer #dash .show-apps:active .overview-icon,
#dashtodockContainer #dash .show-apps:checked .overview-icon,
#dashtodockContainer #dash .overview-tile:active .overview-icon,
#dashtodockContainer #dash .overview-tile:checked .overview-icon { background-color: ${dockActive} !important; }
#dashtodockContainer #dash .app-well-app-running-dot,
#dashtodockContainer #dash .app-grid-running-dot { background-color: ${dock.foreground} !important; }
`;
}

/** How far the edge reaches and how hard it has to be pushed, per sensitivity. */
export function edgeActivation(sensitivity) {
    const level = SENSITIVITIES.includes(sensitivity) ? sensitivity : 'medium';
    return {
        // Logical pixels from the screen edge that count as "at the edge".
        band: {low: 1, medium: 2, high: 4}[level],
        // Pixels of pointer travel into the edge a pressure reveal needs.
        pressure: {low: 220, medium: 120, high: 50}[level],
    };
}

/**
 * Should the bar be on screen right now?
 *
 * `held` is the user's own reveal: the pointer reached the edge or is resting
 * on the bar. A menu or keyboard focus in the bar holds it regardless of the
 * fullscreen rule, because a menu opened from an off-screen bar has nowhere
 * to anchor. The lock screen always shows it: it carries the lock screen's own
 * battery and network indicators.
 */
export function barShouldShow(situation) {
    const s = situation ?? {};
    if (s.locked || s.overview || s.mode === 'always')
        return true;
    if (s.menuOpen || s.keyFocus)
        return true;
    if (s.fullscreen && !s.revealInFullscreen)
        return false;
    if (s.held)
        return true;
    if (s.mode === 'auto')
        return false;
    return !s.overlap;
}

/** Whether reaching the edge may reveal a hidden bar at all. */
export function revealAllowed(situation) {
    const s = situation ?? {};
    return !s.locked && !s.overview && s.mode !== 'always' &&
        (!s.fullscreen || !!s.revealInFullscreen);
}

export function rectsIntersect(a, b) {
    return !!a && !!b && a.x < b.x + b.width && a.x + a.width > b.x &&
        a.y < b.y + b.height && a.y + a.height > b.y;
}

/**
 * Does any counted window cover the bar, under the chosen hide rule?
 *
 * Geometry decides, whichever monitor a window calls home: one straddling in
 * from the next display covers the bar exactly as much as a local one.
 * Edges are exclusive, so a window resting flush against the bar is clear.
 */
export function barOverlap(windows, bar, hideWhen = 'any') {
    return (windows ?? []).some(window => {
        if (!rectsIntersect(window.rect, bar))
            return false;
        if (hideWhen === 'focused')
            return !!window.focusedApp;
        if (hideWhen === 'maximized')
            return !!window.maximized;
        return true;
    });
}

/** Translation that takes the whole bar off its monitor, or 0 when shown. */
export function panelTranslation(position, height, hidden) {
    if (!hidden)
        return 0;
    return position === 'bottom' ? height : -height;
}

/** The band along the bar's screen edge that counts as reaching it. */
export function pointerAtEdge(position, monitor, band, pointerX, pointerY) {
    if (!monitor || pointerX < monitor.x || pointerX >= monitor.x + monitor.width)
        return false;
    if (position === 'bottom') {
        const edge = monitor.y + monitor.height;
        return pointerY >= edge - band && pointerY < edge;
    }
    return pointerY >= monitor.y && pointerY < monitor.y + band;
}

/** The stable shown-panel area, including the screen edge that revealed it. */
export function pointerInPanelCorridor(position, monitor, panel, pointerX, pointerY) {
    if (!monitor || !panel)
        return false;
    const x1 = monitor.x;
    const x2 = monitor.x + monitor.width;
    if (pointerX < x1 || pointerX >= x2)
        return false;
    if (position === 'bottom')
        return pointerY >= panel.y && pointerY < monitor.y + monitor.height;
    return pointerY >= monitor.y && pointerY < panel.y + panel.height;
}

/**
 * Coalesces a burst of window changes into one decision.
 *
 * `now` is injected so the rule is testable: a change restarts the quiet
 * period, but never beyond `maxWait` after the first change of the burst,
 * so a window that animates forever still gets evaluated.
 */
export class SettleTimer {
    constructor(quiet, maxWait) {
        this.quiet = quiet;
        this.maxWait = maxWait;
        this.first = null;
    }

    /** Milliseconds to wait before deciding, after a change at `now`. */
    delayFor(now) {
        if (this.first === null)
            this.first = now;
        return Math.max(0, Math.min(this.quiet, this.first + this.maxWait - now));
    }

    settled() {
        this.first = null;
    }
}
