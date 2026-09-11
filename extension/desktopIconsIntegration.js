import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const DING_USABLE_AREA_ID = '130cbc66-235c-4bd6-8571-98d2d8bba5e2';
const PUBLISH_DELAY_MS = 100;

/** Publish icon-only monitor margins through Desktop Icons NG's public API. */
export class DesktopIconsIntegration {
    constructor(extensionUuid) {
        this._extensionUuid = extensionUuid;
        this._margins = null;
        this._publishId = 0;
        this._extensionStateId = Main.extensionManager.connect(
            'extension-state-changed', () => this._schedulePublish());
    }

    /** Keep the primary monitor's icon grid outside the panel's shown area. */
    setPanelMargin(position, height) {
        this._margins = {
            '-1': {
                top: position === 'top' ? height : 0,
                bottom: position === 'bottom' ? height : 0,
                left: 0,
                right: 0,
            },
        };
        this._schedulePublish();
    }

    _schedulePublish() {
        if (this._publishId)
            GLib.source_remove(this._publishId);
        this._publishId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, PUBLISH_DELAY_MS, () => {
                this._publishId = 0;
                this._publish();
                return GLib.SOURCE_REMOVE;
            });
    }

    _publish() {
        for (const uuid of Main.extensionManager.getUuids()) {
            const usableArea = Main.extensionManager.lookup(uuid)
                ?.stateObj?.DesktopIconsUsableArea;
            if (usableArea?.uuid !== DING_USABLE_AREA_ID)
                continue;
            try {
                usableArea.setMarginsForExtension(this._extensionUuid, this._margins);
            } catch (error) {
                console.warn(`desktop-forge: could not update desktop-icon margins: ${error}`);
            }
        }
    }

    destroy() {
        if (this._extensionStateId) {
            Main.extensionManager.disconnect(this._extensionStateId);
            this._extensionStateId = 0;
        }
        if (this._publishId) {
            GLib.source_remove(this._publishId);
            this._publishId = 0;
        }
        this._margins = null;
        this._publish();
        this._extensionUuid = null;
    }
}
