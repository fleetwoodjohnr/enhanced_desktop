"""Exercise the real dialog save handlers without needing a display server."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from desktop_forge import config
from desktop_forge.pages.widgets import WidgetsPage, _NewsFeedsDialog, _NewsTopicsDialog, _topic_summary


class NewsSettingsTests(unittest.TestCase):
    def page(self):
        page = SimpleNamespace(
            _config=config.Config(providers={"news": {"topic_presets": ["ai"], "topics": []}}),
            _save_source=0, _toast=Mock(), _rebuild_widget_list=Mock(),
        )
        page._save_now = MethodType(WidgetsPage._save_now, page)
        return page

    def dialog(self, page, sources=False):
        if sources:
            return SimpleNamespace(_page=page, close=Mock(),
                                   _rows=[Mock(get_text=Mock(return_value="https://example.com/feed"))])
        return SimpleNamespace(_page=page, close=Mock(), _switches={
            "ai": Mock(get_active=Mock(return_value=False)),
            "technology": Mock(get_active=Mock(return_value=True)),
        }, _custom=Mock(get_text=Mock(return_value=" C++, C++ ")))

    def test_failed_saves_keep_active_settings_and_dialog_open(self):
        for sources in (False, True):
            with self.subTest(sources=sources):
                page = self.page()
                original = page._config
                dialog = self.dialog(page, sources)
                save = _NewsFeedsDialog._save if sources else _NewsTopicsDialog._save
                with patch.object(config, "save", side_effect=OSError("disk full")):
                    save(dialog)
                self.assertIs(page._config, original)
                self.assertEqual(page._config.providers["news"]["topic_presets"], ["ai"])
                dialog.close.assert_not_called()
                page._rebuild_widget_list.assert_not_called()
                page._toast.assert_called_once_with("Could not save settings: disk full")

    def test_successful_saves_persist_then_update_the_page(self):
        for sources in (False, True):
            with self.subTest(sources=sources), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.json"
                page = self.page()
                dialog = self.dialog(page, sources)
                save = _NewsFeedsDialog._save if sources else _NewsTopicsDialog._save
                with patch.object(config, "CONFIG_PATH", str(path)):
                    save(dialog)
                    saved = config.load().provider_options("news")
                if sources:
                    self.assertEqual(saved["feeds"], ["https://example.com/feed"])
                else:
                    self.assertEqual(saved["topic_presets"], ["technology"])
                    self.assertEqual(saved["topics"], ["C++"])
                dialog.close.assert_called_once()
                page._rebuild_widget_list.assert_called_once()

    def test_select_all_clear_all_and_summaries(self):
        dialog = self.dialog(self.page())
        _NewsTopicsDialog._select_all(dialog, True)
        for switch in dialog._switches.values():
            switch.set_active.assert_called_with(True)
        _NewsTopicsDialog._select_all(dialog, False)
        for switch in dialog._switches.values():
            switch.set_active.assert_called_with(False)
        dialog._custom.set_text.assert_called_once_with("")
        self.assertEqual(_topic_summary([], ["C++"]), "No topics selected")
        self.assertEqual(_topic_summary(config.NEWS_TOPIC_IDS, []), "All topics")


if __name__ == "__main__":
    unittest.main()
