import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
test_dir = Path(tempfile.mkdtemp(prefix='desktop-forge-folder-test-'))
os.environ['XDG_CONFIG_HOME'] = str(test_dir / 'config')
os.environ['XDG_DATA_HOME'] = str(test_dir / 'data')
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from desktop_forge import config
from desktop_forge.backend.folder_colors import FolderColors, PRESETS
from desktop_forge.pages.overall import FolderColorDialog, OverallPage
from desktop_forge.window import DesktopForgeWindow

app = Adw.Application(application_id='org.jrf.DesktopForge.FolderSmoke')
results = {'test_dir': str(test_dir)}
exit_code = 0

def capture(widget, name):
    # Wait for a new GTK frame after edits invalidate the previous snapshot.
    context = GLib.MainContext.default()
    loop = GLib.MainLoop.new(context, False)
    GLib.timeout_add(150, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
    loop.run()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, widget.get_width(), widget.get_height())
    node = snapshot.to_node()
    # GTK 4.22 can decline to snapshot a foreign/headless toplevel even after
    # it has allocated successfully. Functional assertions still exercise the
    # page; the PNG is only an optional debugging artifact.
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
        store = FolderColors()
        folders = [test_dir / 'Example Blue & Work', test_dir / 'Example Purple 資料']
        for folder in folders:
            folder.mkdir()
        a = store.apply(folders[0].as_uri(), PRESETS[0][1])
        b = store.apply(folders[1].as_uri(), PRESETS[7][1])
        assert store._read_icon(Gio.File.new_for_uri(a.uri)) == a.icon_uri
        assert store._read_icon(Gio.File.new_for_uri(b.uri)) == b.icon_uri
        # A new store must retain both colors; test real GVfs removal and restore.
        assert len(FolderColors().load()) == 2
        store.reset(a.uri)
        assert store._read_icon(Gio.File.new_for_uri(a.uri)) is None
        store._write_icon(Gio.File.new_for_uri(a.uri), b.icon_uri)
        store.apply(a.uri, '#e35151')
        store.reset(a.uri)
        assert store._read_icon(Gio.File.new_for_uri(a.uri)) == b.icon_uri
        store._write_icon(Gio.File.new_for_uri(a.uri), None)
        a = store.apply(a.uri, '#3584e4')
        results['native_metadata'] = 'apply, independent colors, restart, reset and prior-icon restoration passed'
        window = DesktopForgeWindow(application=application)
        window.set_title('Desktop Forge folder colors test')
        hub = window._pages['overall']
        assert window._stack.get_visible_child() is hub, 'Customize is not the first tab'
        # The Overall controls now live in Customize's sections.
        page = hub.overall
        assert isinstance(page, OverallPage), 'Overall controls failed to load'
        assert page._top_group.get_parent() is not None, 'Top-bar controls were not moved into Customize'
        hub.show_section('desktop')
        assert page._top_group.get_title() == 'Top Bar', 'Top-bar controls are missing'
        assert page._dock_group.get_title() == 'Dock', 'Dock controls are missing'
        assert page._icons_group.get_title() == 'Desktop Icons', 'Desktop icon controls are missing'
        assert page._icon_material.get_model().get_n_items() == 4, 'Glass material presets are missing'
        assert page._icon_artwork.get_model().get_n_items() == 2, 'Artwork finishes are missing'
        assert page._desktop_icon_size.get_sensitive() == page._desktop_icons.ding_available
        assert page._top_height.get_value() == 32, 'Top-bar default height is incorrect'
        if page._dock:
            # The default dock is at the bottom: assigning the bar there must
            # be rejected, after which moving the dock left is valid.
            page._top_position.set_selected(1)
            assert page._top_position.get_selected() == 0, 'Conflicting panel edge was accepted'
            page._dock_position.set_selected(3)
            assert page._dock.read().position == 'LEFT', 'Dock position did not reach GSettings'
        page._top_visibility.set_selected(1)
        page._top_height.set_value(36)
        page._auto_rows['top_bar'].set_active(False)
        window.present()
        results['saved_folders'] = [a.uri, b.uri]

        def show_editor():
            try:
                assert len(page._rows) == 2
                chrome = config.load().chrome_options('top_bar')
                assert chrome['visibility'] == 'intelligent', 'Top-bar visibility was not saved'
                assert chrome['height'] == 36, 'Top-bar height was not saved'
                assert chrome['foreground_mode'] == 'custom', 'Manual foreground mode was not saved'
                capture(window, 'overall-light.png')
                dialog = FolderColorDialog(Gio.File.new_for_uri(a.uri), a.color,
                    lambda color, editor: page._operate(lambda: store.apply(a.uri, color), 'Applied', editor))
                dialog.present(page)
                def check_editor():
                    try:
                        dialog.hex.set_text('#oops')
                        assert not dialog.apply_button.get_sensitive()
                        dialog.hex.set_text('#3ca878')
                        assert dialog.apply_button.get_sensitive()
                        capture(window, 'color-picker.png')
                        dialog._on_apply()
                        def completed():
                            try:
                                if page._busy:
                                    return GLib.SOURCE_CONTINUE
                                assert next(e for e in store.load() if e.uri == a.uri).color == '#3ca878'
                                Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                                def finish():
                                    try:
                                        capture(window, 'overall-dark.png')
                                        for entry in store.load():
                                            store.reset(entry.uri)
                                        results['ui'] = 'rows, preview, hex validation, asynchronous Apply, light and dark passed'
                                        print(json.dumps(results, indent=2), flush=True)
                                        app.quit()
                                    except Exception as exc:
                                        fail(exc)
                                    return GLib.SOURCE_REMOVE
                                GLib.timeout_add(450, finish)
                            except Exception as exc:
                                fail(exc)
                            return GLib.SOURCE_REMOVE
                        GLib.timeout_add(100, completed)
                    except Exception as exc:
                        fail(exc)
                    return GLib.SOURCE_REMOVE
                GLib.timeout_add(450, check_editor)
            except Exception as exc:
                fail(exc)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(650, show_editor)
    except Exception as exc:
        fail(exc)

app.connect('activate', activate)
def timed_out():
    global exit_code
    exit_code = 1
    print('Folder-color UI test timed out', flush=True)
    app.quit()
    return GLib.SOURCE_REMOVE

GLib.timeout_add_seconds(20, timed_out)
app.run([])
sys.exit(exit_code)
