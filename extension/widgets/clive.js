import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Animation from 'resource:///org/gnome/shell/ui/animation.js';
import * as GrabHelper from 'resource:///org/gnome/shell/ui/grabHelper.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {
    ACTIVE_STATUSES as ACTIVE, actionRows, attachmentDetails, composerKey, confirmationRows,
    markdownLite, modelLabel, modelMenuSections, pathsFromUriList,
} from '../cliveLogic.js';
import {DesktopWidget, hexToRgba} from './base.js';
import {styleScrollbar} from './scrollbar.js';

const IDLE_HINT = 'Click the box below to type.';
const TYPING_HINT = 'Typing · Esc or click away to release the keyboard';
const WELCOME = 'Hello. I’m CLIVE.\n\nSearch the web, find a file, add a reminder, ' +
    'or work in an app. You see the task before I take any action.';

function label(text, css = '') {
    const actor = new St.Label({text, style_class: css, x_expand: true});
    actor.clutter_text.line_wrap = true;
    actor.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
    actor.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    return actor;
}

/**
 * One speaker turn.
 *
 * The gutter is what keeps the two speakers apart on a narrow card: it has no
 * natural width, so a short reply hugs its own side, and its min-width stops
 * even a long one from spanning the full card and losing the distinction.
 */
function iconFor(name) {
    try {
        return Gio.Icon.new_for_string(name || 'applications-system-symbolic');
    } catch (_error) {
        return Gio.ThemedIcon.new('applications-system-symbolic');
    }
}

function bubble(text, mine, style) {
    const row = new St.BoxLayout({x_expand: true});
    const gutter = new St.Widget({style_class: 'df-clive-gutter', x_expand: true});
    const message = label(text, `df-clive-bubble${mine ? ' df-clive-bubble-mine' : ''}`);
    // The gutter takes the slack, so a short turn hugs its own side instead of
    // stretching across a card whose whole point is who said what.
    message.x_expand = false;
    if (style)
        message.set_style(style);
    if (mine)
        row.add_child(gutter);
    row.add_child(message);
    if (!mine)
        row.add_child(gutter);
    return row;
}

/**
 * The CLIVE card.
 *
 * Typing on the desktop needs an explicit keyboard grab: GNOME Shell routes key
 * events to the focused window, so an St.Entry sitting in the background or in
 * chrome never sees them. GrabHelper is Shell's own answer -- it takes the
 * modal grab, releases on Escape or a click outside the grabbed actor, and
 * forwards events to the on-screen keyboard. The grab covers the whole card,
 * not just the entry, so Send, Approve, Stop and the scrollbar keep working
 * while the keyboard is captured.
 *
 * POPUP rather than NORMAL action mode: under NORMAL, Super or Alt+Tab would
 * still fire and raise something while this card kept the keystrokes, which
 * reads as a frozen desktop. Escape and click-away are the only exits, exactly
 * like every other Shell popup.
 */
export const CliveWidget = GObject.registerClass(
class CliveWidget extends DesktopWidget {
    get interactive() { return true; }

    /** The card's own text scale, which sizes its icons and spinner too. */
    get _scale() {
        return Math.max(0.5, Math.min(3, this._style?.font_scale ?? 1));
    }
    get tracksHover() { return true; }

    /** Whether this card currently holds the desktop keyboard. */
    get isCapturingKeys() {
        return !!this._grabHelper?.grabbed;
    }

    buildBody(body) {
        const {header} = this.addHeader(body, 'CLIVE', 'system-help-symbolic');
        // The model belongs beside the name, not on a row of its own: on a card
        // this size every row spent on chrome is a row of conversation lost.
        // It is a button: one click switches between the cloud and local model.
        const pill = new St.BoxLayout({style_class: 'df-clive-model-box'});
        this._modeIcon = new St.Icon({
            icon_name: 'computer-symbolic', style_class: 'df-clive-model-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._mode = new St.Label({
            style_class: 'df-header-meta', text: 'Your desktop assistant',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._mode.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        pill.add_child(this._modeIcon);
        pill.add_child(this._mode);
        pill.add_child(new St.Icon({
            icon_name: 'pan-down-symbolic', style_class: 'df-clive-model-icon',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        this._modelButton = new St.Button({
            style_class: 'df-clive-model', can_focus: true, child: pill,
            accessible_name: 'Switch between the cloud and local model',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._modelButton.connect('clicked', () => this._openModelMenu());
        header.add_child(this._modelButton);
        this._menuManager = new PopupMenu.PopupMenuManager(this);
        this._modelMenu = null;
        // App Access at a glance, and its emergency stop, on the card itself.
        this._shieldIcon = new St.Icon({icon_name: 'security-high-symbolic', style_class: 'df-clive-icon'});
        this._shield = new St.Button({
            style_class: 'df-add', can_focus: true, child: this._shieldIcon,
            accessible_name: 'App access', y_align: Clutter.ActorAlign.CENTER,
        });
        this._shield.connect('clicked', () => this._openAccessMenu());
        header.add_child(this._shield);
        this._accessMenu = null;
        // Below this width the model name gives way to its icon alone.
        this.connect('notify::width', () => {
            if (this.width < 300 * this._scale)
                this.add_style_class_name('df-clive-narrow');
            else
                this.remove_style_class_name('df-clive-narrow');
            this._mode.visible = this.width >= 300 * this._scale;
        });
        // An icon rather than a '+': beside a window-new button, a plus reads
        // as "add something to this chat", which is what the paperclip below
        // now does. This one has only ever started a new chat.
        this._fresh = new St.Button({
            style_class: 'df-add', can_focus: true, accessible_name: 'Start a new chat',
            child: new St.Icon({icon_name: 'tab-new-symbolic', style_class: 'df-clive-icon'}),
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._fresh.connect('clicked', () => this._client?.call('view', {conversation: ''}));
        header.add_child(this._fresh);
        const open = new St.Button({
            style_class: 'df-add', can_focus: true, accessible_name: 'Open CLIVE in a window',
            child: new St.Icon({icon_name: 'window-new-symbolic', style_class: 'df-clive-icon'}),
            y_align: Clutter.ActorAlign.CENTER,
        });
        open.connect('clicked', () => this._client?.open());
        header.add_child(open);

        this._scroll = new St.ScrollView({overlay_scrollbars: true, x_expand: true, y_expand: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
            style_class: 'df-subtle-scroll df-clive-scroll'});
        this._transcript = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'df-clive-transcript'});
        this._messages = new St.BoxLayout({vertical: true, x_expand: true, style_class: 'df-clive-transcript'});
        this._partial = label('', 'df-clive-message');
        this._partial.hide();
        this._transcript.add_child(this._messages);
        this._transcript.add_child(this._partial);
        // The approval card is a sibling of _messages rather than a child,
        // because render() empties _messages on every change and Approve has to
        // outlive that. It still scrolls with the conversation it belongs to.
        this._transcript.add_child(this._buildPreview());
        // Anything high-risk waits here, whatever the approval mode: what will
        // happen and the buttons that decide it, together.
        this._transcript.add_child(this._buildConfirmation());

        this._scroll.set_child(this._transcript);
        body.add_child(this._scroll);
        styleScrollbar(this, this._scroll);

        const status = new St.BoxLayout({style_class: 'df-clive-actions', x_expand: true});
        this._spinner = new Animation.Spinner(Math.round(14 * this._scale), {animate: true, hideOnStop: true});
        this._spinner.y_align = Clutter.ActorAlign.CENTER;
        status.add_child(this._spinner);
        // The app CLIVE is using right now, beside what it is doing with it.
        this._activityIcon = new St.Icon({style_class: 'df-clive-icon', visible: false,
            y_align: Clutter.ActorAlign.CENTER});
        status.add_child(this._activityIcon);
        this._activity = label(IDLE_HINT, 'df-clive-mode');
        this._activity.y_align = Clutter.ActorAlign.CENTER;
        status.add_child(this._activity);
        this._stop = new St.Button({label: 'Stop', style_class: 'df-clive-button', can_focus: true,
            visible: false, y_align: Clutter.ActorAlign.CENTER});
        this._stop.connect('clicked', () => this._client?.call('cancel'));
        status.add_child(this._stop);
        body.add_child(status);

        // Staged files sit right above the composer they will be sent with, so
        // picking one always shows it -- inside the transcript it was often
        // added below the visible part of the conversation.
        body.add_child(this._buildAttachments());
        this._dropHint = label('Drop the files into the CLIVE window that opened.',
            'df-clive-message df-clive-drop-hint');
        this._dropHint.visible = false;
        body.add_child(this._dropHint);

        const entryRow = new St.BoxLayout({style_class: 'df-clive-actions'});
        this._entry = new St.Entry({hint_text: 'Ask CLIVE…', style_class: 'df-entry', can_focus: true, x_expand: true});
        this._entry.clutter_text.set_max_length(16000);
        // Several lines, like the expanded chat: Enter sends, Shift+Enter
        // starts a new line, Ctrl+V with files or an image attaches them.
        this._entry.clutter_text.single_line_mode = false;
        this._entry.clutter_text.activatable = false;
        this._entry.clutter_text.line_wrap = true;
        this._entry.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        this._entry.clutter_text.connect('key-press-event', (_actor, event) => {
            const action = composerKey(event.get_key_symbol(), event.get_state());
            if (action === 'send') {
                this._send();
                return Clutter.EVENT_STOP;
            }
            if (action === 'paste' && this._paste())
                return Clutter.EVENT_STOP;
            return Clutter.EVENT_PROPAGATE;
        });
        this._entry.clutter_text.connect('text-changed', () => {
            if (this._client)
                this._client.draft = this._entry.get_text();
        });
        // Taking the grab before St.Entry's own handler runs means the caret
        // still lands where the pointer was. Focus-in is the backstop: however
        // the entry came to be focused -- pointer, gesture, or Tab -- it is the
        // moment the keyboard has to start arriving here.
        this._entry.connect('button-press-event', () => {
            this.beginTyping();
            return Clutter.EVENT_PROPAGATE;
        });
        this._entry.clutter_text.connect('key-focus-in', () => this.beginTyping());
        this._attach = new St.Button({style_class: 'df-clive-button', can_focus: true,
            accessible_name: 'Attach files',
            child: new St.Icon({icon_name: 'mail-attachment-symbolic', style_class: 'df-clive-icon'})});
        this._attach.connect('clicked', () => this._pick());
        this._sendButton = new St.Button({label: 'Send', style_class: 'df-clive-button', can_focus: true});
        this._sendButton.connect('clicked', () => this._send());
        entryRow.add_child(this._entry);
        entryRow.add_child(this._attach);
        entryRow.add_child(this._sendButton);
        body.add_child(entryRow);
        // Keep Tab travelling between this card's own controls while grabbed.
        global.focus_manager?.add_group(this);
        this.connect('notify::mapped', () => {
            if (!this.mapped)
                this.endTyping();
        });
        const dnd = global.backend.get_dnd?.();
        if (dnd) {
            this._dndIds = [
                dnd.connect('dnd-position-change', (_dnd, x, y) => this._dragMoved(x, y)),
                dnd.connect('dnd-leave', () => this._dragEnded()),
            ];
        }
        this.connect('destroy', () => {
            this._destroyed = true;
            for (const id of this._dndIds ?? [])
                dnd?.disconnect(id);
            this.endTyping();
            global.focus_manager?.remove_group(this);
            this._unsubscribe?.();
            this._modelMenu?.destroy();
            this._modelMenu = null;
            this._accessMenu?.destroy();
            this._accessMenu = null;
            if (this._scrollIdle)
                GLib.source_remove(this._scrollIdle);
        });
    }

    /** Preview and Approve as one card, so the two are never read apart. */
    _buildPreview() {
        this._preview = new St.BoxLayout({vertical: true, x_expand: true, visible: false,
            style_class: 'df-clive-preview'});
        this._preview.set_style(`border-color: ${hexToRgba(this.accent, 0.55)}`);
        this._previewHeading = label('Task preview', 'df-clive-speaker');
        this._previewHeading.set_style(`color: ${this.accent}`);
        this._preview.add_child(this._previewHeading);
        this._previewText = label('', 'df-clive-message');
        this._preview.add_child(this._previewText);
        const row = new St.BoxLayout({style_class: 'df-clive-actions', x_expand: true});
        // Right-aligned, so the button sits where Stop does on the row below
        // and where Approve does in the expanded chat.
        row.add_child(new St.Widget({x_expand: true}));
        this._approve = new St.Button({label: 'Approve task', can_focus: true, visible: false,
            style_class: 'df-clive-button df-clive-approve'});
        this._approve.set_style(`background-color: ${hexToRgba(this.accent, 0.30)}`);
        this._approve.connect('clicked', () => this._client?.call('approve', {id: this._state.id, version: this._state.version}));
        row.add_child(this._approve);
        this._preview.add_child(row);
        return this._preview;
    }

    _buildConfirmation() {
        this._confirmation = new St.BoxLayout({vertical: true, x_expand: true, visible: false,
            style_class: 'df-clive-preview df-clive-confirmation'});
        this._confirmation.add_child(label('Confirm before CLIVE continues', 'df-clive-speaker df-clive-warning'));
        this._confirmRows = new St.BoxLayout({vertical: true, x_expand: true});
        this._confirmation.add_child(this._confirmRows);
        const row = new St.BoxLayout({style_class: 'df-clive-actions', x_expand: true});
        row.add_child(new St.Widget({x_expand: true}));
        const decline = new St.Button({label: 'Don\u2019t do it', can_focus: true,
            style_class: 'df-clive-button'});
        decline.connect('clicked', () => this._answerConfirmation(false));
        row.add_child(decline);
        this._confirmButton = new St.Button({label: 'Confirm', can_focus: true,
            style_class: 'df-clive-button df-clive-approve'});
        this._confirmButton.set_style(`background-color: ${hexToRgba(this.accent, 0.30)}`);
        this._confirmButton.connect('clicked', () => this._answerConfirmation(true));
        row.add_child(this._confirmButton);
        this._confirmation.add_child(row);
        return this._confirmation;
    }

    _answerConfirmation(approve) {
        const calls = this._state?.confirmation?.calls ?? [];
        this._client?.call('confirm', {id: this._state.id,
            approved: approve ? calls.map(call => call.id) : []});
    }

    _renderConfirmation(state) {
        const waiting = state.status === 'awaiting_confirmation';
        this._confirmation.visible = waiting;
        if (!waiting)
            return;
        const signature = JSON.stringify(state.confirmation ?? null);
        if (signature === this._confirmSignature)
            return;
        this._confirmSignature = signature;
        this._confirmRows.destroy_all_children();
        for (const row of confirmationRows(state.confirmation)) {
            this._confirmRows.add_child(label(row.title, 'df-clive-message df-clive-confirm-title'));
            if (row.detail)
                this._confirmRows.add_child(label(row.detail, 'df-clive-message df-clive-confirm-detail'));
        }
    }

    /** The files staged for the next message, listed one per row. */
    _buildAttachments() {
        this._attachments = new St.BoxLayout({vertical: true, x_expand: true, visible: false,
            style_class: 'df-clive-preview df-clive-attached'});
        this._attachments.add_child(label('Attached', 'df-clive-speaker'));
        this._attachmentRows = new St.BoxLayout({vertical: true, x_expand: true,
            style_class: 'df-clive-attachment-rows'});
        this._attachments.add_child(this._attachmentRows);
        return this._attachments;
    }

    _renderAttachments() {
        const draft = this._state?.draft ?? [];
        const signature = JSON.stringify(draft.map(item => item.id));
        if (signature === this._draftSignature)
            return;
        this._draftSignature = signature;
        this._attachmentRows.destroy_all_children();
        this._attachments.visible = draft.length > 0;
        for (const item of draft) {
            const row = new St.BoxLayout({x_expand: true, style_class: 'df-clive-attachment'});
            row.add_child(new St.Icon({
                icon_name: item.kind === 'image' ? 'image-x-generic-symbolic' : 'text-x-generic-symbolic',
                style_class: 'df-clive-attachment-icon', y_align: Clutter.ActorAlign.CENTER,
            }));
            const shown = new St.Label({text: item.name, x_expand: true, style_class: 'df-clive-message',
                y_align: Clutter.ActorAlign.CENTER});
            // The end of a filename is what tells two of them apart, so the
            // middle goes rather than the extension.
            shown.clutter_text.ellipsize = Pango.EllipsizeMode.MIDDLE;
            row.add_child(shown);
            const details = attachmentDetails(item);
            if (details)
                row.add_child(new St.Label({text: details, style_class: 'df-detail',
                    y_align: Clutter.ActorAlign.CENTER}));
            const remove = new St.Button({style_class: 'df-clive-chip-remove', can_focus: true,
                accessible_name: `Remove ${item.name}`, y_align: Clutter.ActorAlign.CENTER,
                child: new St.Icon({icon_name: 'window-close-symbolic', style_class: 'df-clive-attachment-icon'})});
            remove.connect('clicked', () => this._client?.call('draft_remove', {id: item.id}));
            row.add_child(remove);
            this._attachmentRows.add_child(row);
        }
    }

    _stage(paths) {
        if (!paths.length || !this._client)
            return;
        this._client.call('draft_add', {paths}, result => {
            for (const problem of result?.errors ?? [])
                this._client?.notify?.(problem);
        });
    }

    /** Ctrl+V with files or an image on the clipboard attaches them. */
    _paste() {
        const clipboard = St.Clipboard.get_default();
        const types = clipboard.get_mimetypes(St.ClipboardType.CLIPBOARD) ?? [];
        if (types.includes('text/uri-list')) {
            clipboard.get_content(St.ClipboardType.CLIPBOARD, 'text/uri-list', (_clip, bytes) => {
                const text = new TextDecoder().decode(bytes?.get_data?.() ?? new Uint8Array());
                this._stage(pathsFromUriList(text, uri => Gio.File.new_for_uri(uri).get_path()));
            });
            return true;
        }
        const image = types.find(type => type === 'image/png');
        if (image) {
            clipboard.get_content(St.ClipboardType.CLIPBOARD, image, (_clip, bytes) => {
                const data = bytes?.get_data?.();
                if (!data?.length)
                    return;
                const folder = GLib.build_filenamev([GLib.get_user_data_dir(), 'desktop-forge', 'clive', 'pasted']);
                GLib.mkdir_with_parents(folder, 0o700);
                const stamp = GLib.DateTime.new_now_local().format('%Y-%m-%d %H.%M.%S');
                const path = GLib.build_filenamev([folder, `Pasted image ${stamp}.png`]);
                GLib.file_set_contents(path, data);
                this._stage([path]);
            });
            return true;
        }
        return false;
    }

    /**
     * Shell actors never receive files dropped from apps; only hovering is
     * reported. So a drag over the card opens the CLIVE window, which does
     * take drops, and the card says where to let go.
     */
    _dragMoved(x, y) {
        const [cx, cy] = this.get_transformed_position();
        const [width, height] = this.get_transformed_size();
        const inside = x >= cx && x < cx + width && y >= cy && y < cy + height;
        if (inside && !this._dragOpened) {
            this._dragOpened = true;
            this._dropHint.visible = true;
            this._client?.open();
        }
    }

    _dragEnded() {
        this._dragOpened = false;
        this._dropHint.visible = false;
    }

    _openAccessMenu() {
        if (!this._client)
            return;
        this.endTyping();
        this._accessMenu?.destroy();
        const menu = new PopupMenu.PopupMenu(this._shield, 0.5, St.Side.TOP);
        menu.actor.hide();
        menu.actor.add_style_class_name('df-status-menu');
        menu.actor.add_style_class_name(this._style.dark ? 'df-theme-dark' : 'df-theme-light');
        Main.uiGroup.add_child(menu.actor);
        this._menuManager.addMenu(menu);
        this._accessMenu = menu;
        const access = this._client.state?.access;
        const summary = new PopupMenu.PopupMenuItem(access
            ? (access.paused ? 'All app access is paused'
                : `${access.enabled} of ${access.total} apps can be used`)
            : 'App access');
        summary.setSensitive(false);
        menu.addMenuItem(summary);
        const pause = new PopupMenu.PopupMenuItem(access?.paused ? 'Resume app access' : 'Pause all app access');
        pause.connect('activate', () => this._client?.call('access_pause', {paused: !access?.paused}));
        menu.addMenuItem(pause);
        const manage = new PopupMenu.PopupMenuItem('Manage app access…');
        manage.connect('activate', () => this._client?.openSettings?.('access'));
        menu.addMenuItem(manage);
        menu.open();
    }

    _openModelMenu() {
        if (!this._client)
            return;
        // A popup menu takes its own modal grab; hand back the keyboard first,
        // as the file picker does.
        this.endTyping();
        this._modelMenu?.destroy();
        const menu = new PopupMenu.PopupMenu(this._modelButton, 0.5, St.Side.TOP);
        // BoxPointer actors start at the stage origin until their first open.
        menu.actor.hide();
        menu.actor.add_style_class_name('df-status-menu');
        menu.actor.add_style_class_name(this._style.dark ? 'df-theme-dark' : 'df-theme-light');
        Main.uiGroup.add_child(menu.actor);
        this._menuManager.addMenu(menu);
        this._modelMenu = menu;

        for (const section of modelMenuSections(this._client.state?.selection)) {
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem(section.title));
            for (const item of section.items) {
                const entry = new PopupMenu.PopupMenuItem(item.name);
                entry.setOrnament(item.active ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
                entry.setSensitive(item.sensitive);
                entry.connect('activate', () => this._client?.call(
                    'model_select', {endpoint: section.endpoint, model: item.name}));
                menu.addMenuItem(entry);
            }
            if (!section.ready) {
                const setup = new PopupMenu.PopupMenuItem('Set up Ollama Cloud…');
                setup.connect('activate', () => this._client?.openSettings?.('models'));
                menu.addMenuItem(setup);
            }
        }
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const manage = new PopupMenu.PopupMenuItem('Manage models…');
        manage.connect('activate', () => this._client?.openSettings?.('models'));
        menu.addMenuItem(manage);
        menu.open();
    }

    _pick() {
        if (ACTIVE.includes(this._state?.status) || !this._client?.pickFiles)
            return;
        this.endTyping();
        this._client.pickFiles(paths => this._stage(paths));
    }

    beginTyping() {
        if (!this.mapped || !this.get_parent() || this.isCapturingKeys)
            return;
        // A notice from before typing began is old news; one that arrives
        // while typing (a refused file, a refused send) is shown even though
        // the card holds the keyboard.
        this._noticeWhenTyping = this._state?.notice ?? '';
        if (ACTIVE.includes(this._state?.status))
            return;
        // pushModal remembers whatever holds the stage focus and restores it on
        // release. If St.Entry has already focused itself, that would hand the
        // focus back to an entry no longer receiving keys, so start from none.
        const focus = global.stage.get_key_focus();
        if (focus && this.contains(focus))
            global.stage.set_key_focus(null);
        this._grabHelper ??= new GrabHelper.GrabHelper(this, {actionMode: Shell.ActionMode.POPUP});
        this._grabHelper.grab({
            actor: this,
            focus: this._entry.clutter_text,
            // Escape and click-away are GrabHelper's, not ours, so the visible
            // state has to be undone from the release itself.
            onUngrab: () => this._released(),
        });
        this._entry.add_style_class_name('df-entry-capturing');
        this._syncActivity();
    }

    endTyping() {
        if (!this.isCapturingKeys)
            return;
        try {
            this._grabHelper.ungrab({actor: this});
        } catch (error) {
            logError(error, 'desktop-forge: could not release the CLIVE keyboard grab');
        }
        this._released();
    }

    _released() {
        this._entry.remove_style_class_name('df-entry-capturing');
        this._syncActivity();
    }

    /** Called by the extension before this card is moved behind a window. */
    clearDesktopInteraction() {
        this.endTyping();
        super.clearDesktopInteraction();
    }

    setClient(client) {
        this._client = client;
        this._entry.set_text(client.draft);
        this._unsubscribe = client.subscribe(state => this.render(state));
    }

    _send() {
        const message = this._entry.get_text().trim();
        const staged = (this._state?.draft ?? []).length > 0;
        // Staged files on their own are a message too.
        if ((!message && !staged) || ACTIVE.includes(this._state?.status))
            return;
        // A refused submit never runs this callback, so the text and the
        // staged files survive to be corrected, and the notice says why.
        this._client?.call('submit', {message, conversation: this._state?.conversation ?? '',
            use_draft: true}, () => this._entry.set_text(''));
    }

    /**
     * The activity line doubles as the keyboard cue, so a small card does not
     * lose another row of transcript to a permanent hint.
     */
    _syncActivity() {
        const fresh = this._state?.notice && this._state.notice !== this._noticeWhenTyping;
        if (this.isCapturingKeys && !fresh) {
            this._activity.text = TYPING_HINT;
            this._activity.remove_style_class_name('df-clive-notice');
            return;
        }
        const state = this._state ?? {};
        this._activity.text = state.notice || state.activity ||
            (state.status ? 'Ready' : IDLE_HINT);
        // A failure that reads like a hint gets scrolled past. Only the notice
        // channel carries them, so only it is coloured.
        if (state.notice)
            this._activity.add_style_class_name('df-clive-notice');
        else
            this._activity.remove_style_class_name('df-clive-notice');
    }

    render(state) {
        const adjustment = this._scroll.get_vadjustment();
        const atBottom = adjustment.value >= adjustment.upper - adjustment.page_size - 24 * this._scale;
        this._state = state;
        const active = ACTIVE.includes(state.status);
        const approving = state.status === 'awaiting_approval';
        // Holding the keyboard while the entry refuses input would be a trap.
        if (active)
            this.endTyping();
        this._approve.visible = approving;
        // The card outlives the button. A task approval mode let through without
        // stopping has no button at all, and the preview is the only place its
        // permissions are written out -- and it scrolls with the transcript, so
        // keeping it costs the card no rows.
        this._preview.visible = approving || !!state.preview;
        this._previewHeading.text = approving ? 'Task preview' : 'Approved task';
        this._renderConfirmation(state);
        this._renderAttachments();
        if (state.preferences?.card_density === 'compact')
            this.add_style_class_name('df-clive-compact');
        else
            this.remove_style_class_name('df-clive-compact');
        this._stop.visible = active;
        // A disabled control that still answers Tab is a dead end for the
        // keyboard, which is the one input this card goes out of its way to get.
        this._sendButton.reactive = !active;
        this._sendButton.can_focus = !active;
        this._sendButton.opacity = active ? 100 : 255;
        this._entry.reactive = !active;
        this._entry.can_focus = !active;
        this._entry.opacity = active ? 140 : 255;
        this._attach.reactive = !active;
        this._attach.can_focus = !active;
        this._attach.opacity = active ? 100 : 255;
        // Starting a new chat on an empty one produces an identical state, so
        // the button would appear broken. Show that there is nothing to clear
        // instead of accepting a click that cannot change anything.
        const resettable = !active && ((state.messages?.length ?? 0) > 0 || !!state.preview);
        this._fresh.reactive = resettable;
        this._fresh.can_focus = resettable;
        this._fresh.opacity = resettable ? 255 : 100;
        if (state.status === 'planning' || state.status === 'running')
            this._spinner.play();
        else
            this._spinner.stop();
        this._shieldIcon.icon_name = state.access?.paused ? 'security-low-symbolic' : 'security-high-symbolic';
        const running = state.status === 'running' && !!state.activity_icon;
        this._activityIcon.visible = running;
        if (running)
            this._activityIcon.gicon = iconFor(state.activity_icon);
        const pill = modelLabel(state);
        this._mode.text = pill.text;
        this._modeIcon.icon_name = pill.cloud ? 'weather-overcast-symbolic' : 'computer-symbolic';
        this._syncActivity();
        this._partial.text = state.partial || '';
        this._partial.visible = !!state.partial;
        const showActions = state.preferences?.show_action_details !== false;
        const signature = JSON.stringify([state.messages, state.preview, state.status,
            showActions ? (state.actions ?? []).length : 0]);
        if (atBottom && !this._scrollIdle) {
            this._scrollIdle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._scrollIdle = 0;
                adjustment.value = Math.max(0, adjustment.upper - adjustment.page_size);
                return GLib.SOURCE_REMOVE;
            });
        }
        if (signature === this._signature)
            return;
        this._signature = signature;
        this._previewText.text = state.preview ?? '';
        this._messages.destroy_all_children();
        if (!state.messages?.length)
            this._messages.add_child(label(WELCOME, 'df-clive-message df-clive-welcome'));
        const mine = `background-color: ${hexToRgba(this.accent, 0.30)}`;
        for (const message of state.messages?.slice(-20) ?? []) {
            const user = message.role === 'user';
            const files = message.attachments ?? [];
            const content = files.length ? message.content.split('\n\nAttached: ')[0] : message.content;
            if (user) {
                this._messages.add_child(bubble(content, true, mine));
            } else {
                const {markup, links} = markdownLite(content);
                const row = bubble(content, false, '');
                const text = row.get_children().find(child => child instanceof St.Label);
                text?.clutter_text.set_markup(markup);
                this._messages.add_child(row);
                if (links.length)
                    this._messages.add_child(this._linkRow(links));
            }
            if (files.length)
                this._messages.add_child(label(`📎 ${files.map(file => file.name).join(', ')}`,
                    `df-detail df-clive-message-files${user ? ' df-clive-files-mine' : ''}`));
        }
        if (showActions) {
            for (const row of actionRows(state.actions))
                this._messages.add_child(label(row.text, `df-detail df-clive-action df-clive-action-${row.status}`));
        }
    }
    _linkRow(links) {
        const row = new St.BoxLayout({style_class: 'df-clive-links', x_expand: true});
        for (const link of links) {
            const button = new St.Button({label: `${link.title} ↗`, style_class: 'df-clive-link',
                can_focus: true, accessible_name: `Open ${link.url}`});
            button.connect('clicked', () => {
                try {
                    Gio.AppInfo.launch_default_for_uri(link.url, null);
                } catch (error) {
                    this._client?.notify?.(`Could not open ${link.url}`);
                }
            });
            row.add_child(button);
        }
        return row;
    }

    update(_state) { /* CLIVE receives live D-Bus state, not provider polling. */ }
});
