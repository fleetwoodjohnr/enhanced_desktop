import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Animation from 'resource:///org/gnome/shell/ui/animation.js';
import * as GrabHelper from 'resource:///org/gnome/shell/ui/grabHelper.js';

import {addAttachmentPaths, MAX_ATTACHMENTS} from '../widgetLogic.js';
import {DesktopWidget, hexToRgba} from './base.js';
import {styleScrollbar} from './scrollbar.js';

const IDLE_HINT = 'Click the box below to type.';
const TYPING_HINT = 'Typing · Esc or click away to release the keyboard';
const WELCOME = 'Hello. I’m CLIVE.\n\nSearch the web, find a file, add a reminder, ' +
    'or work in an app. You see the task before I take any action.';
const ACTIVE = ['planning', 'running', 'awaiting_approval'];

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
    get tracksHover() { return true; }

    /** Whether this card currently holds the desktop keyboard. */
    get isCapturingKeys() {
        return !!this._grabHelper?.grabbed;
    }

    buildBody(body) {
        const {header} = this.addHeader(body, 'CLIVE', 'system-help-symbolic');
        // The model belongs beside the name, not on a row of its own: on a card
        // this size every row spent on chrome is a row of conversation lost.
        this._mode = new St.Label({
            style_class: 'df-header-meta', text: 'Your desktop assistant',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._mode.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        header.add_child(this._mode);
        // An icon rather than a '+': beside a window-new button, a plus reads
        // as "add something to this chat", which is what the paperclip below
        // now does. This one has only ever started a new chat.
        this._fresh = new St.Button({
            style_class: 'df-add', can_focus: true, accessible_name: 'Start a new chat',
            child: new St.Icon({icon_name: 'tab-new-symbolic', icon_size: 12}),
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._fresh.connect('clicked', () => this._client?.call('view', {conversation: ''}));
        header.add_child(this._fresh);
        const open = new St.Button({
            style_class: 'df-add', can_focus: true, accessible_name: 'Open CLIVE in a window',
            child: new St.Icon({icon_name: 'window-new-symbolic', icon_size: 12}),
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
        // Staged files sit at the end of the transcript for the same reason the
        // preview does, and because a card this size has no row to spare on a
        // strip that is empty most of the time. It scrolls into view on its own.
        this._transcript.add_child(this._buildAttachments());
        this._scroll.set_child(this._transcript);
        body.add_child(this._scroll);
        styleScrollbar(this, this._scroll);

        const status = new St.BoxLayout({style_class: 'df-clive-actions', x_expand: true});
        this._spinner = new Animation.Spinner(14, {animate: true, hideOnStop: true});
        this._spinner.y_align = Clutter.ActorAlign.CENTER;
        status.add_child(this._spinner);
        this._activity = label(IDLE_HINT, 'df-clive-mode');
        this._activity.y_align = Clutter.ActorAlign.CENTER;
        status.add_child(this._activity);
        this._stop = new St.Button({label: 'Stop', style_class: 'df-clive-button', can_focus: true,
            visible: false, y_align: Clutter.ActorAlign.CENTER});
        this._stop.connect('clicked', () => this._client?.call('cancel'));
        status.add_child(this._stop);
        body.add_child(status);

        const entryRow = new St.BoxLayout({style_class: 'df-clive-actions'});
        this._entry = new St.Entry({hint_text: 'Ask CLIVE…', style_class: 'df-entry', can_focus: true, x_expand: true});
        this._entry.clutter_text.set_max_length(16000);
        this._entry.clutter_text.connect('activate', () => this._send());
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
            child: new St.Icon({icon_name: 'mail-attachment-symbolic', icon_size: 12})});
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
        this.connect('destroy', () => {
            this._destroyed = true;
            this.endTyping();
            global.focus_manager?.remove_group(this);
            this._unsubscribe?.();
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
        const paths = this._client?.attachments ?? [];
        this._attachmentRows.destroy_all_children();
        this._attachments.visible = paths.length > 0;
        for (const path of paths) {
            const row = new St.BoxLayout({x_expand: true, style_class: 'df-clive-attachment'});
            const name = path.split('/').pop();
            const shown = new St.Label({text: name, x_expand: true, style_class: 'df-clive-message',
                y_align: Clutter.ActorAlign.CENTER});
            // The end of a filename is what tells two of them apart, so the
            // middle goes rather than the extension.
            shown.clutter_text.ellipsize = Pango.EllipsizeMode.MIDDLE;
            row.add_child(shown);
            const remove = new St.Button({style_class: 'df-clive-chip-remove', can_focus: true,
                accessible_name: `Remove ${name}`, y_align: Clutter.ActorAlign.CENTER,
                child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 10})});
            remove.connect('clicked', () => {
                if (!this._client)
                    return;
                this._client.attachments = this._client.attachments.filter(one => one !== path);
                this._renderAttachments();
            });
            row.add_child(remove);
            this._attachmentRows.add_child(row);
        }
    }

    /**
     * Hand the picker the keyboard before it asks for it.
     *
     * The portal dialog is a window, so the extension will demote this card
     * behind it and clearDesktopInteraction() would drop the grab anyway.
     * Releasing here makes that certain instead of a race with the dialog
     * being mapped.
     */
    _pick() {
        if (ACTIVE.includes(this._state?.status) || !this._client?.pickFiles)
            return;
        this.endTyping();
        this._client.pickFiles(paths => {
            const staged = addAttachmentPaths(this._client.attachments ?? [], paths);
            this._client.attachments = staged.paths;
            if (this._destroyed)
                return;
            this._renderAttachments();
            if (staged.overflow)
                this._client.notify?.(`CLIVE takes at most ${MAX_ATTACHMENTS} files with one message.`);
        });
    }

    beginTyping() {
        if (!this.mapped || !this.get_parent() || this.isCapturingKeys)
            return;
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
        client.attachments ??= [];
        this._renderAttachments();
        this._unsubscribe = client.subscribe(state => this.render(state));
    }

    _send() {
        const message = this._entry.get_text().trim();
        if (!message || ACTIVE.includes(this._state?.status))
            return;
        // A refused file leaves the whole submit failing, and call() never runs
        // this callback -- so the list survives to be corrected, and the notice
        // names the file that has to go.
        const attachments = [...(this._client?.attachments ?? [])];
        this._client?.call('submit', {message, conversation: this._state?.conversation ?? '', attachments}, () => {
            this._entry.set_text('');
            if (this._client)
                this._client.attachments = [];
            this._renderAttachments();
        });
    }

    /**
     * The activity line doubles as the keyboard cue, so a small card does not
     * lose another row of transcript to a permanent hint.
     */
    _syncActivity() {
        if (this.isCapturingKeys) {
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
        const atBottom = adjustment.value >= adjustment.upper - adjustment.page_size - 24;
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
        this._mode.text = state.model ? `${state.mode === 'cloud' ? 'Cloud' : 'Local'} · ${state.model}` : 'Your desktop assistant';
        this._syncActivity();
        this._partial.text = state.partial || '';
        this._partial.visible = !!state.partial;
        const signature = JSON.stringify([state.messages, state.preview, state.status]);
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
            this._messages.add_child(bubble(message.content, user, user ? mine : ''));
        }
    }
    update(_state) { /* CLIVE receives live D-Bus state, not provider polling. */ }
});
