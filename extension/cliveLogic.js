/** Pure CLIVE card decisions, kept free of Shell imports so gjs can test them. */

export const ACTIVE_STATUSES = Object.freeze([
    'planning', 'running', 'awaiting_approval', 'awaiting_confirmation',
]);

/**
 * What the model pill says.
 *
 * A running task reports the model it actually reached, which differs from
 * the selection after a cloud failure fell back to the local model. The rest
 * of the time the pill shows the selection, so it is right before any task
 * has run and straight after a switch.
 */
export function modelLabel(state) {
    const running = ACTIVE_STATUSES.includes(state?.status);
    const chosen = state?.selection;
    if (running && state?.model)
        return {cloud: state.mode === 'cloud', text: `${state.mode === 'cloud' ? 'Cloud' : 'Local'} · ${state.model}`};
    if (chosen) {
        const cloud = chosen.endpoint === 'cloud';
        const model = cloud ? chosen.cloud_model : chosen.local_model;
        return {cloud, text: `${cloud ? 'Cloud' : 'Local'}${model ? ` · ${model}` : ''}`};
    }
    return {cloud: false, text: 'Your desktop assistant'};
}

/** The model menu: one section per endpoint, the active model checked. */
export function modelMenuSections(selection) {
    const chosen = selection ?? {};
    return [['cloud', 'Cloud'], ['local', 'Local']].map(([endpoint, title]) => {
        const ready = endpoint === 'local' || !!chosen.cloud_ready;
        const names = Array.isArray(chosen[`${endpoint}_models`]) ? chosen[`${endpoint}_models`] : [];
        return {
            endpoint, title, ready,
            items: names.map(name => ({
                name,
                active: chosen.endpoint === endpoint && chosen[`${endpoint}_model`] === name,
                sensitive: ready,
            })),
        };
    });
}

/**
 * One line per action waiting on the confirmation card.
 *
 * The capability is the headline ("Move files to Trash"), the target is what
 * it will touch, and the app names where -- so the card reads as exactly what
 * will happen, not as a tool name.
 */
export function confirmationRows(confirmation) {
    const calls = Array.isArray(confirmation?.calls) ? confirmation.calls : [];
    return calls.map(call => ({
        id: call.id,
        title: call.capability || call.label || call.tool || 'Action',
        detail: [call.app_name, call.target].filter(Boolean).join(' · '),
    }));
}

/** Kind, size and pages of a staged file, for its row on the card. */
export function attachmentDetails(item) {
    const parts = [];
    if (item?.label && item.label !== 'Text')
        parts.push(item.label);
    if (item?.pages)
        parts.push(`${item.pages} p.`);
    const size = Number(item?.size ?? 0);
    if (size > 0) {
        const units = ['B', 'KB', 'MB', 'GB'];
        let value = size;
        let index = 0;
        while (value >= 1024 && index < units.length - 1) {
            value /= 1024;
            index++;
        }
        parts.push(`${index ? value.toFixed(value < 10 ? 1 : 0) : value} ${units[index]}`);
    }
    return parts.join(' · ');
}

/** Local file paths from a text/uri-list clipboard, skipping comments and remote URIs. */
export function pathsFromUriList(text, toPath) {
    return String(text ?? '').split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => line && !line.startsWith('#'))
        .map(uri => toPath(uri))
        .filter(path => !!path);
}

const KEY_RETURN = [0xff0d, 0xff8d, 0xfe34];
const KEY_V = [0x76, 0x56];
const SHIFT_MASK = 1 << 0;
const CONTROL_MASK = 1 << 2;

/** What a key press in the card's composer means: 'send', 'paste' or null. */
export function composerKey(keysym, state) {
    if (KEY_RETURN.includes(keysym))
        return state & SHIFT_MASK ? null : 'send';
    if (KEY_V.includes(keysym) && state & CONTROL_MASK)
        return 'paste';
    return null;
}

function escapeMarkup(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * The little Markdown CLIVE writes, as Pango markup, plus its links.
 *
 * Everything is escaped first, so an answer can never inject markup of its
 * own. Links become underlined text, and are returned separately so the card
 * can offer them as buttons -- a label cannot be clicked per word.
 */
export function markdownLite(text) {
    const links = [];
    const addLink = (title, url) => {
        if (/^https?:\/\//i.test(url) && !links.some(link => link.url === url))
            links.push({title, url});
    };
    const lines = String(text ?? '').split('\n').map(raw => {
        let line = escapeMarkup(raw);
        const heading = /^#{1,6} (.*)$/.exec(line);
        if (heading)
            line = `<b>${heading[1]}</b>`;
        line = line.replace(/^(\s*)[-*] /, '$1• ');
        line = line.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, title, url) => {
            addLink(title, url.replace(/&amp;/g, '&'));
            return `<u>${title}</u>`;
        });
        line = line.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
        line = line.replace(/`([^`]+)`/g, '<tt>$1</tt>');
        for (const match of raw.matchAll(/(?<!\()https?:\/\/[^\s)>\]]+/g))
            addLink(match[0].replace(/^https?:\/\//, '').slice(0, 40), match[0]);
        return line;
    });
    return {markup: lines.join('\n'), links: links.slice(0, 5)};
}

/** The last few actions, one line each, for under CLIVE's answer on the card. */
export function actionRows(actions, max = 3) {
    const list = Array.isArray(actions) ? actions : [];
    return list.slice(-max).map(action => {
        const status = action.status ?? 'done';
        const what = action.capability || String(action.tool ?? '').replace(/_/g, ' ');
        const title = [action.app_name, what].filter(Boolean).join(' · ');
        const mark = {blocked: '⛔', declined: '✕'}[status] ?? '✓';
        return {text: `${mark} ${title}${action.target ? ` — ${action.target}` : ''}`, status};
    });
}
