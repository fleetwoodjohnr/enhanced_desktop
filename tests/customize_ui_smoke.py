"""The Customize hub, end to end, against isolated settings.

GSettings uses the memory backend and XDG folders point at a temporary
directory, so nothing here touches the real desktop.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
test_dir = Path(tempfile.mkdtemp(prefix='desktop-forge-customize-test-'))
os.environ['XDG_CONFIG_HOME'] = str(test_dir / 'config')
os.environ['XDG_DATA_HOME'] = str(test_dir / 'data')
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk
from desktop_forge import config
from desktop_forge.customize import BY_ID, profiles
from desktop_forge.customize.backends import DESKTOP_PATH
from desktop_forge.pages.customize import CustomizePage, KEEP_SECONDS
from desktop_forge.window import DesktopForgeWindow

app = Adw.Application(application_id='org.jrf.DesktopForge.CustomizeSmoke')
results = {'test_dir': str(test_dir)}
exit_code = 0


def desktop_json():
    return json.loads(Path(DESKTOP_PATH).read_text())


def wait(ms=150):
    loop = GLib.MainLoop.new(None, False)
    GLib.timeout_add(ms, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    loop.run()


def capture(widget, name):
    wait(500)
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, widget.get_width(), widget.get_height())
    node = snapshot.to_node()
    if node is None:
        results.setdefault('snapshots', 'unavailable in this compositor')
        return
    texture = widget.get_native().get_renderer().render_texture(node, None)
    texture.save_to_png(str(test_dir / name))


def fail(exc):
    global exit_code
    traceback.print_exc()
    results['error'] = str(exc)
    exit_code = 1
    app.quit()


def activate(application):
    try:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        window = DesktopForgeWindow(application=application)
        window.set_default_size(1000, 780)
        hub = window._pages['overall']
        assert isinstance(hub, CustomizePage), 'Customize failed to load'
        window.present()
        c = hub.controller

        # desktop.json exists from the start, complete, for the extension.
        data = desktop_json()
        assert data['tiling']['enabled'] is False and data['windows']['corner_radius'] == 0
        results['desktop_json'] = 'written complete on first start'

        # A generated row reaches desktop.json, and the bar offers Revert.
        hub.show_section('tiling')
        c.change('tiling.enabled', True)
        c.change('tiling.gaps_inner', 14)
        assert 'tiling.enabled' not in c._bindings or c.get('tiling.enabled') is True
        c.flush()
        assert desktop_json()['tiling']['gaps_inner'] == 14
        assert hub.preview_bar.get_reveal_child(), 'Revert bar did not appear after a change'
        capture(window, 'tiling.png')
        hub._revert()
        assert desktop_json()['tiling']['enabled'] is False and desktop_json()['tiling']['gaps_inner'] == 8
        assert not hub.preview_bar.get_reveal_child()
        results['edit_revert'] = 'live change and revert passed'

        # The layout pictures: choosing one writes it, and the combo row follows.
        c.change('tiling.enabled', True)
        picker = hub.layout_picker
        picker.flow.emit('child-activated', picker.children['centered'])
        c.flush()
        assert desktop_json()['tiling']['layout'] == 'centered', 'Choosing a layout picture did not apply it'
        assert picker.flow.get_selected_children() == [picker.children['centered']]
        capture(window, 'tiling-layouts.png')
        hub._revert()
        assert desktop_json()['tiling']['layout'] == 'master' and desktop_json()['tiling']['enabled'] is False
        assert picker.flow.get_selected_children() == [picker.children['master']], 'The picker did not follow the revert'
        results['layout_picker'] = 'picture applies the layout; revert restores it'

        # GNOME settings (memory backend) go through the same path.
        c.change('animations.enabled', False)
        c.flush()
        assert c.customizer.gsettings.get(BY_ID['animations.enabled']) is False
        hub._keep()
        assert not c.session.active

        # A preset previews, counts down, and goes back by itself.
        hub.show_section('profiles')
        hyprland = hub.store.get('preset-hyprland')
        hub.apply_profile(hyprland)
        data = desktop_json()
        assert data['tiling']['enabled'] is True and data['tiling']['layout'] == 'dwindle'
        assert config.load().chrome_options('top_bar')['opacity'] == 0.85
        assert abs(hub.overall._top_bar_opacity.get_value() - 0.85) < 1e-6, 'Top Bar rows did not follow'
        assert c.customizer.gsettings.get(BY_ID['animations.enabled']) is True  # preset resets it
        assert hub.store.active() == 'preset-hyprland'
        assert 'Going back in' in hub.preview_bar.label.get_label()
        capture(window, 'profiles-preview.png')
        hub._countdown = 0
        hub._tick()
        assert desktop_json()['tiling']['enabled'] is False, 'Timed revert did not happen'
        assert c.customizer.gsettings.get(BY_ID['animations.enabled']) is False
        assert hub.store.active() == ''
        results['preset_preview'] = f'applied, {KEEP_SECONDS} s countdown, automatic revert passed'

        # Keep a preset, then drift from it: the In Use row says Modified.
        hub.apply_profile(hub.store.get('preset-minimal'))
        hub._keep()
        assert hub.store.active() == 'preset-minimal'
        assert 'Built-in' in hub.profiles.current_row.get_subtitle()
        c.change('windows.corner_radius', 9)
        c.flush()
        hub._keep()
        assert 'Modified' in hub.profiles.current_row.get_subtitle(), hub.profiles.current_row.get_subtitle()
        assert not hub.profiles.update_row.get_visible(), 'Presets cannot be updated in place'
        results['modified_marker'] = 'passed'

        # Save as a profile, export it, import it back.
        hub.profiles._create('Smoke Profile')
        mine = hub.store.user_profiles()
        assert [p.name for p in mine] == ['Smoke Profile']
        assert mine[0].values['windows.corner_radius'] == 9
        assert hub.store.active() == mine[0].id
        exported = test_dir / f'smoke{profiles.EXTENSION}'
        hub.store.export(mine[0].id, str(exported))
        report = hub.store.import_file(str(exported))
        hub.profiles.reload()
        assert report.profile.name == 'Smoke Profile (imported)'
        assert len(hub.profiles._rows) == 2
        results['profiles'] = 'save, export, import passed'

        # Search builds rows bound to the same values, and releases them.
        before = sum(len(v) for v in c._bindings.values())
        hub.search.set_text('gaps')
        wait(400)
        assert hub.stack.get_visible_child_name() == 'search'
        during = sum(len(v) for v in c._bindings.values())
        assert during > before, 'Search found no gap settings'
        hub.search.set_text('')
        wait(400)
        assert sum(len(v) for v in c._bindings.values()) == before, 'Search rows were not released'
        hub.search.set_text('zzzz-nothing')
        wait(400)
        hub.search.set_text('')
        results['search'] = 'passed'

        # Window rules round-trip through desktop.json.
        hub.show_section('rules')
        hub.rules._added({'name': 'Calculator floats', 'match': {'app': 'org.gnome.Calculator.desktop'},
                          'actions': {'mode': 'float', 'center': True}})
        c.flush()
        saved = desktop_json()['rules']['list']
        assert saved[0]['actions']['mode'] == 'float' and saved[0]['match']['type'] == 'any'
        assert len(hub.rules._rows) == 1
        capture(window, 'rules.png')
        results['rules'] = 'passed'

        # Shortcuts: the extension's schema is found in the source tree, and
        # a chosen combination lands in GSettings (memory backend here).
        from desktop_forge.customize import shortcuts as shortcut_logic
        section = hub.shortcuts
        assert section.store.extension_available, 'The shortcuts schema was not found'
        tiling = shortcut_logic.Shortcut(shortcut_logic.EXTENSION, 'toggle-tiling', 'Turn tiling on or off')
        section._chosen(tiling, '<Super><Alt>t')
        assert section.store.get(tiling) == ['<Super><Alt>t']
        assert section._rows[tiling][2].get_visible(), 'Reset did not appear for a changed shortcut'
        section._chosen(tiling, '')
        assert section.store.get(tiling) == []
        path = section.store.save_custom('Terminal', 'ptyxis', '<Super>Return')
        section._fill_custom()
        assert len(section._custom_rows) == 1
        hub.show_section('shortcuts')
        capture(window, 'shortcuts.png')
        section.store.remove_custom(path)
        results['shortcuts'] = 'set, clear, custom command'

        for key in ('windows', 'top_bar', 'input', 'wallpaper'):
            hub.show_section(key)
            capture(window, f'{key}.png')
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        hub.show_section('profiles')
        capture(window, 'profiles-dark.png')

        # A narrow window collapses to one column.
        window.set_default_size(420, 700)
        wait(300)
        results['collapsed'] = hub.split.get_collapsed()
        capture(window, 'narrow.png')
        print(json.dumps(results, indent=2), flush=True)
        app.quit()
    except Exception as exc:
        fail(exc)


app.connect('activate', activate)


def timed_out():
    global exit_code
    exit_code = 1
    print('Customize UI test timed out', flush=True)
    app.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add_seconds(40, timed_out)
app.run([])
sys.exit(exit_code)
