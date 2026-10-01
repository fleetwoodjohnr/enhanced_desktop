import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

/**
 * What each Desktop Forge shortcut does. The keys themselves are in the
 * extension's GSettings schema, edited in Customize → Keyboard Shortcuts;
 * Shell watches them, so a change there applies without a restart.
 */
const ACTIONS = {
    'toggle-tiling': c => c.tiling()?.toggle(),
    'next-layout': c => c.tiling()?.nextLayout(),
    'toggle-floating': c => c.tiling()?.toggleFloating(),
    'focus-left': c => c.tiling()?.focus('left'),
    'focus-right': c => c.tiling()?.focus('right'),
    'focus-up': c => c.tiling()?.focus('up'),
    'focus-down': c => c.tiling()?.focus('down'),
    'swap-left': c => c.tiling()?.swap('left'),
    'swap-right': c => c.tiling()?.swap('right'),
    'swap-up': c => c.tiling()?.swap('up'),
    'swap-down': c => c.tiling()?.swap('down'),
    'grow-main': c => c.tiling()?.resizeMain(0.05),
    'shrink-main': c => c.tiling()?.resizeMain(-0.05),
    'snap-left': c => c.snap()?.snapFocused('left'),
    'snap-right': c => c.snap()?.snapFocused('right'),
    'snap-top-left': c => c.snap()?.snapFocused('top-left'),
    'snap-top-right': c => c.snap()?.snapFocused('top-right'),
    'snap-bottom-left': c => c.snap()?.snapFocused('bottom-left'),
    'snap-bottom-right': c => c.snap()?.snapFocused('bottom-right'),
    'center-window': c => c.snap()?.centerFocused(),
    'toggle-top-bar': c => c.panel()?.toggleForced(),
    'open-clive': c => c.openClive(),
    'scratchpad-toggle': c => c.scratchpad()?.toggle(),
    'scratchpad-send': c => c.scratchpad()?.send(),
};

export const SHORTCUT_NAMES = Object.keys(ACTIONS);

export class Keybindings {
    /**
     * @param {Gio.Settings} settings - the extension's schema
     * @param {{tiling, snap, panel, scratchpad, openClive}} context - where actions go
     */
    constructor(settings, context) {
        this._added = [];
        for (const [name, action] of Object.entries(ACTIONS)) {
            const added = Main.wm.addKeybinding(name, settings, Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
                Shell.ActionMode.NORMAL, () => {
                    try {
                        action(context);
                    } catch (error) {
                        logError(error, `desktop-forge: the ${name} shortcut failed`);
                    }
                });
            if (added !== Meta.KeyBindingAction.NONE)
                this._added.push(name);
        }
    }

    get added() {
        return [...this._added];
    }

    update() {
        // Shortcuts are read from GSettings by Shell itself.
    }

    destroy() {
        for (const name of this._added)
            Main.wm.removeKeybinding(name);
        this._added = [];
    }
}
