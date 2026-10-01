/**
 * Window rule matching, free of Shell imports so it runs under plain gjs.
 *
 * Mirrors desktop_forge/customize/rules.py `matches`; tests run the shared
 * fixtures in tests/rules_fixtures.json through both.
 */

const ACTION_KEYS = ['mode', 'workspace', 'monitor', 'center', 'maximize', 'fullscreen', 'above',
    'sticky', 'opacity', 'no_effects', 'width', 'height'];
const NEUTRAL = {mode: 'default', workspace: 0, monitor: -1, center: false, maximize: false,
    fullscreen: false, above: false, sticky: false, opacity: 1, no_effects: false, width: 0, height: 0};

function sameApp(wanted, desktopId) {
    const a = String(wanted).toLowerCase();
    const b = String(desktopId ?? '').toLowerCase();
    const bare = value => value.endsWith('.desktop') ? value.slice(0, -'.desktop'.length) : value;
    return a === b || bare(a) === bare(b);
}

// Rules are checked on every title change and tiling decision; compile each
// pattern once.
// ponytail: unbounded, but patterns are typed by hand and few.
const patterns = new Map();

function compiled(source) {
    if (!patterns.has(source)) {
        let pattern = null;
        try {
            pattern = new RegExp(source);
        } catch {
            // Validated in Python, but a hand edit could still be bad:
            // a rule that cannot be read matches nothing.
        }
        patterns.set(source, pattern);
    }
    return patterns.get(source);
}

/** Whether a window, described as the Shell bridge describes it, matches. */
export function matchesRule(rule, window) {
    const match = rule?.match ?? {};
    if (match.app && !sameApp(match.app, window.desktop_id))
        return false;
    if (match.wm_class) {
        const wanted = String(match.wm_class).toLowerCase();
        if (wanted !== String(window.wm_class ?? '').toLowerCase() &&
            wanted !== String(window.wm_class_instance ?? '').toLowerCase())
            return false;
    }
    if (match.title) {
        const title = String(window.title ?? '');
        if (match.title_regex) {
            const pattern = compiled(String(match.title));
            if (!pattern?.test(title))
                return false;
        } else if (!title.toLowerCase().includes(String(match.title).toLowerCase())) {
            return false;
        }
    }
    const type = window.type ?? 'normal';
    return !match.type || match.type === 'any' || match.type === type;
}

/**
 * The combined actions for a window: for each action, the first enabled
 * matching rule that sets it (to something other than "leave it") wins.
 */
export function actionsFor(rules, window) {
    const result = {...NEUTRAL};
    const decided = new Set();
    for (const rule of Array.isArray(rules) ? rules : []) {
        if (rule?.enabled === false || !matchesRule(rule, window))
            continue;
        const actions = rule.actions ?? {};
        for (const key of ACTION_KEYS) {
            if (decided.has(key) || !(key in actions) || actions[key] === NEUTRAL[key])
                continue;
            result[key] = actions[key];
            decided.add(key);
        }
    }
    return result;
}

export {NEUTRAL as NEUTRAL_ACTIONS};
