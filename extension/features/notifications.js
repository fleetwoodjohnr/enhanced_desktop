import Clutter from 'gi://Clutter';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

// messageTray.js shows each banner for this long; other timeouts it uses
// (0 to cancel, 2000 once the user is back) keep their meaning.
const STOCK_TIMEOUT_MS = 4000;
const ALIGNMENTS = {
    left: Clutter.ActorAlign.START,
    center: Clutter.ActorAlign.CENTER,
    right: Clutter.ActorAlign.END,
};

function clamp(value, low, high, fallback) {
    return Number.isFinite(value) ? Math.max(low, Math.min(high, value)) : fallback;
}

/** Banner position, how long banners stay, and their opacity. */
export class NotificationStyle {
    constructor() {
        this._tray = Main.messageTray;
        this._originalAlignment = this._tray.bannerAlignment;
        this._timeout = STOCK_TIMEOUT_MS;
        this._injections = new InjectionManager();
        const self = this;
        this._injections.overrideMethod(this._tray, '_updateNotificationTimeout', original =>
            function (timeout) {
                return original.call(this, timeout === STOCK_TIMEOUT_MS ? self._timeout : timeout);
            });
    }

    update({notifications = {}}) {
        this._tray.bannerAlignment = ALIGNMENTS[notifications.position] ?? Clutter.ActorAlign.CENTER;
        this._timeout = Math.round(clamp(notifications.timeout, 2, 30, 4) * 1000);
        this._tray._bannerBin.opacity = Math.round(clamp(notifications.opacity, 0.4, 1, 1) * 255);
    }

    get timeout() {
        return this._timeout;
    }

    destroy() {
        this._injections.clear();
        this._tray.bannerAlignment = this._originalAlignment;
        this._tray._bannerBin.opacity = 255;
    }
}
