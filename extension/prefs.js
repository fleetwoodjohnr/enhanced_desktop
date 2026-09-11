import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

/**
 * Deliberately not a settings UI.
 *
 * Everything configurable lives in the Desktop Forge app, which owns
 * config.json and can also create shortcuts and manage the daemon. Duplicating
 * that here would mean two editors for one file racing each other, so this
 * page just points at the real one.
 */
export default class DesktopForgePreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const page = new Adw.PreferencesPage();
        const group = new Adw.PreferencesGroup();

        const status = new Adw.StatusPage({
            title: 'Configured in Desktop Forge',
            description:
                'Widgets, desktop icon materials, dock and top-bar appearance, ' +
                'and CLIVE are all set up in the Desktop Forge app.',
            icon_name: 'preferences-desktop-apps-symbolic',
        });

        const button = new Gtk.Button({
            label: 'Open Desktop Forge',
            halign: Gtk.Align.CENTER,
            css_classes: ['suggested-action', 'pill'],
        });
        button.connect('clicked', () => {
            const app = Gio.DesktopAppInfo.new('org.jrf.DesktopForge.desktop');
            if (app) {
                app.launch([], null);
            } else {
                // Installed from a checkout without the desktop entry in place.
                GLib.spawn_command_line_async('desktop-forge');
            }
            window.close();
        });

        status.set_child(button);
        group.add(status);
        page.add(group);
        window.add(page);
    }
}
