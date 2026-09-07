from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop_forge import config


class ConfigMigrationTests(unittest.TestCase):
    def test_v1_always_on_top_is_removed_on_save(self) -> None:
        legacy = {
            "version": 1,
            "widgets": [{
                "type": "todos",
                "id": "legacy-card",
                "always_on_top": True,
                "x": 20,
                "y": 20,
                "width": 420,
                "height": 320,
            }],
            "providers": {},
            "style": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with mock.patch.object(config, "CONFIG_PATH", str(path)):
                loaded = config.load()
                self.assertEqual(loaded.version, 3)
                self.assertFalse(hasattr(loaded.widgets[0], "always_on_top"))
                config.save(loaded)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved["version"], 3)
        self.assertNotIn("always_on_top", saved["widgets"][0])

    def test_default_and_legacy_accent_modes(self) -> None:
        weather = config.Widget(type="weather")
        defaults = config.Config().widget_style(weather)
        self.assertTrue(defaults["colorful_accents"])
        self.assertEqual(defaults["accent"], config.WIDGET_ACCENTS["weather"])

        legacy = config.Config(style={"accent": "#123456"}).widget_style(weather)
        self.assertFalse(legacy["colorful_accents"])
        self.assertEqual(legacy["accent"], "#123456")

    def test_news_topics_default_to_unfiltered(self) -> None:
        self.assertEqual(config.DEFAULT_PROVIDER_OPTIONS["news"]["topics"], [])
        self.assertEqual(config.DEFAULT_PROVIDER_OPTIONS["news"]["topic_presets"], config.NEWS_TOPIC_IDS)

    def test_news_migration_preserves_choices_and_custom_feeds(self) -> None:
        for version, presets, expected in ((2, [], config.NEWS_TOPIC_IDS), (3, [], []),
                                           (2, ["technology"], ["technology"])):
            with self.subTest(version=version, presets=presets), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.json"
                custom = ["https://example.com/feed"]
                raw = {"version": version, "providers": {"news": {
                    "topic_presets": presets, "topics": ["C++"], "feeds": custom}}}
                path.write_text(json.dumps(raw))
                with mock.patch.object(config, "CONFIG_PATH", str(path)):
                    config.migrate()
                    config.migrate()  # Idempotent, including the backup.
                    result = config.load()
                    self.assertEqual(result.provider_options("news")["topic_presets"], expected)
                    self.assertEqual(result.provider_options("news")["feeds"], custom)
                    self.assertEqual(result.provider_options("news")["topics"], ["C++"])
                    self.assertEqual(json.loads(path.read_text())["version"], 3)
                    if version == 2:
                        self.assertEqual(json.loads(Path(str(path) + ".pre-v3").read_text()), raw)

    def test_news_defaults_are_not_shared_between_config_instances(self) -> None:
        options = config.Config().provider_options("news")
        options["topic_presets"].clear()
        options["feeds"].clear()
        fresh = config.Config().provider_options("news")
        self.assertEqual(fresh["topic_presets"], config.NEWS_TOPIC_IDS)
        self.assertEqual(len(fresh["feeds"]), 6)

    def test_news_topic_presets_are_well_formed(self) -> None:
        ids = [preset["id"] for preset in config.NEWS_TOPIC_PRESETS]
        self.assertEqual(len(ids), len(set(ids)))
        for preset in config.NEWS_TOPIC_PRESETS:
            self.assertTrue(preset["id"].islower(), preset["id"])
            self.assertTrue(preset["label"].strip(), preset["id"])
            self.assertTrue(preset["keywords"], preset["id"])
            for keyword in preset["keywords"]:
                self.assertEqual(keyword, keyword.strip(), preset["id"])

    def test_weather_defaults_poll_often_and_start_hourly(self) -> None:
        weather = config.DEFAULT_PROVIDER_OPTIONS["weather"]
        self.assertEqual(weather["interval"], 300)
        self.assertEqual(weather["forecast_mode"], "hourly")


if __name__ == "__main__":
    unittest.main()
