"""Real GTK dialog construction and save behavior in the isolated compositor."""
import sys
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_forge import config
from desktop_forge.pages.widgets import Adw, GLib, Gtk, WidgetsPage, _NewsFeedsDialog, _NewsTopicsDialog

Gtk.init()
Adw.init()
window = Adw.Window(default_width=720, default_height=740)
window.present()


def flush():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


with tempfile.TemporaryDirectory() as tmp, patch.object(config, "CONFIG_PATH", str(Path(tmp) / "config.json")):
    page = SimpleNamespace(_config=config.Config(), _save_source=0,
                           _toast=Mock(), _rebuild_widget_list=Mock())
    page._save_now = MethodType(WidgetsPage._save_now, page)
    dialog = _NewsTopicsDialog(page, config.NEWS_TOPIC_IDS, [])
    dialog.present(window)
    flush()
    dialog._switches["ai"].set_active(False)
    with patch.object(config, "save", side_effect=OSError("disk full")):
        dialog._save()
    assert dialog.get_visible(), "Failed save closed the dialog"
    assert "ai" in page._config.provider_options("news")["topic_presets"]
    dialog._save()
    assert "ai" not in config.load().provider_options("news")["topic_presets"]
    flush()
    dialog = _NewsTopicsDialog(page, config.NEWS_TOPIC_IDS, ["C++"])
    dialog.present(window)
    flush()
    dialog._select_all(False)
    assert not any(row.get_active() for row in dialog._switches.values())
    assert dialog._custom.get_text() == ""
    dialog._save()
    assert config.load().provider_options("news")["topic_presets"] == []
    flush()
    sources = _NewsFeedsDialog(page, ["https://example.com/feed"])
    sources.present(window)
    flush()
    sources._restore_defaults()
    sources._save()
    assert config.load().provider_options("news")["feeds"] == config.DEFAULT_NEWS_FEEDS
window.destroy()
print("News GTK dialogs passed")
