/** Pure shell-chrome configuration and CSS helpers, kept free of Shell imports. */

export const CHROME_DEFAULTS = Object.freeze({
    top_bar: Object.freeze({
        visibility: 'always',
        position: 'top',
        height: 32,
        opacity: 0.96,
        foreground_mode: 'auto',
    }),
    dock: Object.freeze({
        opacity: 0.92,
        foreground_mode: 'auto',
    }),
});

export const DOCK_BEHAVIOR_DEFAULTS = Object.freeze({
    animation_time: 0.2,
    show_delay: 0.25,
    hide_delay: 0.2,
    require_pressure: true,
    pressure_threshold: 100,
    autohide_in_fullscreen: false,
    // Keep the top bar's historical maximized-window behavior when the
    // Dash-to-Dock schema is not installed.
    intellihide_mode: 'MAXIMIZED_WINDOWS',
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

    return {
        top_bar: {
            visibility: ['always', 'intelligent', 'auto'].includes(rawTop.visibility)
                ? rawTop.visibility : CHROME_DEFAULTS.top_bar.visibility,
            position: ['top', 'bottom'].includes(rawTop.position)
                ? rawTop.position : CHROME_DEFAULTS.top_bar.position,
            height: Math.round(clampNumber(
                rawTop.height, 24, 64, CHROME_DEFAULTS.top_bar.height)),
            background: topBackground,
            opacity: clampNumber(
                rawTop.opacity, 0, 1, CHROME_DEFAULTS.top_bar.opacity),
            foreground_mode: topMode,
            foreground: topMode === 'auto'
                ? automaticForeground(topBackground)
                : normalizeHex(rawTop.foreground, palette.top_bar.foreground),
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

export function resolveDockBehavior(configured = {}) {
    const defaults = DOCK_BEHAVIOR_DEFAULTS;
    const mode = typeof configured.intellihide_mode === 'string'
        ? configured.intellihide_mode.toUpperCase() : '';
    return {
        animation_time: clampNumber(
            configured.animation_time, 0, 5, defaults.animation_time),
        show_delay: clampNumber(configured.show_delay, 0, 5, defaults.show_delay),
        hide_delay: clampNumber(configured.hide_delay, 0, 5, defaults.hide_delay),
        require_pressure: typeof configured.require_pressure === 'boolean'
            ? configured.require_pressure : defaults.require_pressure,
        pressure_threshold: clampNumber(
            configured.pressure_threshold, 0, 1000, defaults.pressure_threshold),
        autohide_in_fullscreen: typeof configured.autohide_in_fullscreen === 'boolean'
            ? configured.autohide_in_fullscreen : defaults.autohide_in_fullscreen,
        intellihide_mode: [
            'ALL_WINDOWS', 'FOCUS_APPLICATION_WINDOWS',
            'MAXIMIZED_WINDOWS', 'ALWAYS_ON_TOP',
        ].includes(mode) ? mode : defaults.intellihide_mode,
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

export function topBarShouldHide(mode, overlaps, overview, heldOpen) {
    if (overview || heldOpen || mode === 'always')
        return false;
    return mode === 'auto' || (mode === 'intelligent' && overlaps);
}

export function panelTranslation(position, height, hidden) {
    if (!hidden)
        return 0;
    return position === 'bottom' ? height - 1 : 1 - height;
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
