import GLib from 'gi://GLib';

import {readJson, watchJson} from './store.js';

export const DESKTOP_PATH = GLib.build_filenamev(
    [GLib.get_user_config_dir(), 'desktop-forge', 'desktop.json']);

const RELOAD_DELAY_MS = 80;

/**
 * Feed desktop.json to the customization modules, each only what it reads.
 *
 * The app writes the file complete (every value resolved), grouped by the
 * first part of each setting ID. A module declares the groups it reads and
 * is updated only when one of them changes, so moving a slider for window
 * corners never touches tiling, and nothing here rebuilds the widgets.
 *
 * A module that throws is destroyed and left off until the next session;
 * one broken feature must not take the others with it.
 */
export class DesktopSettings {
    /**
     * @param {{name: string, groups: string[], create: () => {update(values), destroy()}}[]} specs
     */
    constructor(specs, path = DESKTOP_PATH) {
        this._specs = specs;
        this._path = path;
        this._running = new Map();
        this._failed = new Set();
        this._reloadId = 0;
        this._monitor = watchJson(path, () => this._schedule());
        this._reload();
    }

    /** Names of the modules currently running, for diagnostics and tests. */
    get active() {
        return [...this._running.keys()];
    }

    get failed() {
        return [...this._failed];
    }

    module(name) {
        return this._running.get(name)?.module ?? null;
    }

    _schedule() {
        if (this._reloadId)
            return;
        this._reloadId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, RELOAD_DELAY_MS, () => {
            this._reloadId = 0;
            this._reload();
            return GLib.SOURCE_REMOVE;
        });
    }

    _reload() {
        // No file yet (the app has not been opened since installing) means
        // every default: modules start neutral, and shortcuts still work.
        // A file that cannot be read is mid-write: keep what is applied
        // rather than resetting every feature for one frame.
        const exists = GLib.file_test(this._path, GLib.FileTest.EXISTS);
        const data = exists ? readJson(this._path) : {};
        if (!data || typeof data !== 'object')
            return;
        for (const spec of this._specs) {
            if (this._failed.has(spec.name))
                continue;
            const values = {};
            for (const group of spec.groups)
                values[group] = data[group] && typeof data[group] === 'object' ? data[group] : {};
            const key = JSON.stringify(values);
            const entry = this._running.get(spec.name);
            if (entry?.key === key)
                continue;
            try {
                if (entry) {
                    entry.key = key;
                    entry.module.update(values);
                } else {
                    const module = spec.create();
                    this._running.set(spec.name, {module, key});
                    module.update(values);
                }
            } catch (error) {
                logError(error, `desktop-forge: ${spec.name} failed and is switched off until next login`);
                this._stop(spec.name);
                this._failed.add(spec.name);
            }
        }
    }

    _stop(name) {
        const entry = this._running.get(name);
        this._running.delete(name);
        try {
            entry?.module.destroy();
        } catch (error) {
            logError(error, `desktop-forge: could not stop ${name}`);
        }
    }

    destroy() {
        if (this._reloadId) {
            GLib.source_remove(this._reloadId);
            this._reloadId = 0;
        }
        this._monitor?.cancel();
        this._monitor = null;
        // Reverse order: later modules may build on earlier ones (tiling
        // consults rules), so they are taken down first.
        for (const name of [...this._running.keys()].reverse())
            this._stop(name);
    }
}
