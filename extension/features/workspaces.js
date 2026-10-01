import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {setLayoutTuning} from '../layoutLogic.js';

const DIRECTIONAL = ['switch-to-workspace-left', 'switch-to-workspace-right', 'switch-to-workspace-up',
    'switch-to-workspace-down', 'move-to-workspace-left', 'move-to-workspace-right',
    'move-to-workspace-up', 'move-to-workspace-down'];

/**
 * Going past the last workspace wraps to the first (and back).
 *
 * Shell registers its workspace keys with a handler bound at startup, so the
 * directional ones are re-registered here. Away from the ends they still go
 * straight to Shell's own handler, popup and all; only at an end does this
 * one pick the workspace on the far side.
 */
export class WorkspaceWrap {
    constructor() {
        this._wrapping = false;
    }

    update({workspaces = {}}) {
        const wanted = workspaces.wrap === true;
        if (wanted === this._wrapping)
            return;
        this._wrapping = wanted;
        for (const name of DIRECTIONAL) {
            Meta.keybindings_set_custom_handler(name, wanted
                ? (display, window, event, binding) => this._switch(display, window, event, binding)
                : Main.wm._showWorkspaceSwitcher.bind(Main.wm));
        }
    }

    _switch(display, window, event, binding) {
        const manager = display.get_workspace_manager();
        const [action, , , target] = binding.get_name().split('-');
        const active = manager.get_active_workspace();
        const direction = Meta.MotionDirection[target.toUpperCase()];
        if (manager.n_workspaces < 2 || active.get_neighbor(direction) !== active) {
            Main.wm._showWorkspaceSwitcher(display, window, event, binding);
            return;
        }
        const forward = target === 'right' || target === 'down';
        const wrapped = manager.get_workspace_by_index(forward ? 0 : manager.n_workspaces - 1);
        if (action === 'switch')
            Main.wm.actionMoveWorkspace(wrapped);
        else if (window && !window.is_always_on_all_workspaces())
            Main.wm.actionMoveWindow(window, wrapped);
    }

    destroy() {
        if (this._wrapping)
            this.update({workspaces: {wrap: false}});
    }
}

/** The desktop widgets' grid and magnet distance. */
export class WidgetGrid {
    update({desktop = {}}) {
        setLayoutTuning({grid: desktop.grid, snap_distance: desktop.snap_distance});
    }

    destroy() {
        setLayoutTuning();
    }
}
