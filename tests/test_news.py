from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from desktop_forge import config
from desktop_forge.daemon import service
from desktop_forge.providers import news
from desktop_forge.providers.base import ProviderError


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Example News</title>
  <item><title><![CDATA[First <b>headline</b>]]></title>
    <link>/first</link><category>Artificial Intelligence</category>
    <pubDate>Fri, 04 Sep 2026 14:00:00 GMT</pubDate></item>
  <item><title>Older headline</title>
    <link>https://example.com/older#section</link>
    <category>Markets</category>
    <pubDate>Fri, 04 Sep 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Example Tech</title>
  <entry><title>Atom headline</title>
    <link rel="alternate" href="https://tech.example/atom" />
    <category term="Technology" />
    <updated>2026-09-04T15:00:00Z</updated></entry>
</feed>"""


class FakeResponse:
    def __init__(self, payload: bytes, url: str = "https://example.com/feed"):
        self.payload = payload
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, _amount: int) -> bytes:
        return self.payload


class NewsProviderTests(unittest.TestCase):
    def test_parses_rss_atom_dates_relative_links_and_markup(self) -> None:
        rss_source, rss_items = news._parse_feed(RSS, "https://example.com/feed.xml")
        atom_source, atom_items = news._parse_feed(ATOM, "https://tech.example/feed")

        self.assertEqual(rss_source, "Example News")
        self.assertEqual(rss_items[0]["title"], "First headline")
        self.assertEqual(rss_items[0]["url"], "https://example.com/first")
        self.assertEqual(rss_items[0]["published"], "2026-09-04T14:00:00+00:00")
        self.assertEqual(atom_source, "Example Tech")
        self.assertEqual(atom_items[0]["published"], "2026-09-04T15:00:00+00:00")

    def test_merges_deduplicates_sorts_and_reports_partial_failure(self) -> None:
        duplicate = {
            "title": "Duplicate",
            "url": "https://example.com/older",
            "source": "Second Source",
            "published": "2026-09-04T16:00:00+00:00",
        }

        def fetch(url: str):
            if url.endswith("broken"):
                raise ProviderError("HTTP 503")
            if url.endswith("atom"):
                return news._parse_feed(ATOM, url)
            source, items = news._parse_feed(RSS, url)
            return source, [*items, duplicate]

        with patch.object(news, "_fetch_feed", side_effect=fetch):
            state = news.NewsProvider().fetch({
                "feeds": [
                    "https://example.com/rss",
                    "https://example.com/broken",
                    "https://example.com/atom",
                ],
                "max_items": 2,
            })

        self.assertEqual(state["total"], 3)
        self.assertEqual(len(state["headlines"]), 2)
        self.assertEqual(state["headlines"][0]["title"], "Atom headline")
        self.assertEqual(state["problems"], ["example.com: HTTP 503"])
        self.assertEqual(state["sources"], ["Example News", "Example Tech"])

    def test_filters_topics_against_titles_and_feed_categories(self) -> None:
        _, rss = news._parse_feed(RSS, "https://example.com/rss")
        _, atom = news._parse_feed(ATOM, "https://example.com/atom")
        topics = news._topics(["  TECHNOLOGY ", "technology"])
        self.assertEqual(topics, ["TECHNOLOGY"])
        self.assertEqual(news._filter_topics(rss + atom, topics), atom)
        self.assertEqual(news._filter_topics(rss, ["first headline"]), rss[:1])
        self.assertEqual(news._filter_topics(rss, ["weather"]), [])

    def test_presets_filter_by_keyword_but_report_their_label(self) -> None:
        def fetch(url: str):
            payload = ATOM if url.endswith("atom") else RSS
            return news._parse_feed(payload, url)

        feeds = ["https://example.com/rss", "https://example.com/atom"]
        with patch.object(news, "_fetch_feed", side_effect=fetch):
            # "Artificial Intelligence" is a keyword of the ai preset, and is
            # the RSS item's category -- the preset id itself matches nothing.
            preset_match = news.NewsProvider().fetch({
                "feeds": feeds, "topic_presets": ["ai"],
            })
            combined = news.NewsProvider().fetch({
                "feeds": feeds,
                "topic_presets": ["ai"],
                "topics": ["Atom headline"],
            })
            unknown = news.NewsProvider().fetch({
                "feeds": feeds, "topic_presets": ["not-a-preset"],
            })

        self.assertEqual(preset_match["topics"], ["Artificial intelligence"])
        self.assertEqual(preset_match["total"], 1)
        self.assertEqual(preset_match["headlines"][0]["title"], "First headline")

        self.assertEqual(
            combined["topics"], ["Artificial intelligence", "Atom headline"]
        )
        self.assertEqual(combined["total"], 1)  # Technology is off, despite the custom match.

        # An unknown id cannot silently enable topics the user switched off.
        self.assertEqual(unknown["topics"], [])
        self.assertEqual(unknown["total"], 0)

    def test_preset_resolution_orders_dedupes_and_drops_unknown_ids(self) -> None:
        self.assertEqual(news._preset_ids(["linux", "AI ", "nope"]), ["ai", "linux"])
        self.assertEqual(news._preset_ids("ai"), [])
        self.assertEqual(news._preset_ids(None), [])
        self.assertEqual(news._preset_labels(["linux"]), ["Linux & open source"])
        self.assertIn("Fedora", news._preset_keywords(["linux"]))
        self.assertEqual(news._preset_keywords([]), [])

        # A custom topic duplicating a preset keyword must not be matched twice.
        merged = news._topics(news._preset_keywords(["linux"]) + ["fedora"])
        self.assertEqual(len(merged), len(set(t.casefold() for t in merged)))

    def test_topic_matching_uses_word_boundaries_and_blank_means_all(self) -> None:
        stories = [
            {"title": "Said yesterday", "_categories": []},
            {"title": "AI advances", "_categories": []},
        ]
        self.assertEqual(news._filter_topics(stories, ["AI"]), [stories[1]])
        self.assertIs(news._filter_topics(stories, []), stories)

    def test_country_preset_does_not_match_pronoun(self) -> None:
        stories = [{"title": title} for title in (
            "Tell us what you think", "US economy grows", "U.S. economy grows",
            "U.S economy grows", "United States economy grows", "Russia reacts",
        )]
        self.assertEqual(news._filter_topics(stories, [],
            preset_keywords=news._preset_keywords(["us"])), stories[1:5])
        self.assertEqual(news._filter_topics(stories, ["us"]), stories[:2])

    def test_technology_security_is_specific_and_phrases_allow_hyphens(self) -> None:
        stories = [{"title": title} for title in (
            "Security council meets", "Computer-security update released",
            "Cyber security breakthrough", "Artificial-intelligence tools",
            "Machine‐learning breakthroughs", "Machine—learning breakthroughs",
            "Said yesterday", "A chair made of wood",
        )]
        self.assertEqual(news._filter_topics(stories, [],
            preset_keywords=news._preset_keywords(["technology"])), stories[1:3])
        self.assertEqual(news._filter_topics(stories, [],
            preset_keywords=news._preset_keywords(["ai"])), stories[3:6])
        self.assertEqual(news._filter_topics([
            {"title": "Artificial", "_categories": ["Intelligence"]},
        ], ["artificial intelligence"]), [])

    def test_duplicate_category_evidence_survives_before_filter_and_limit(self) -> None:
        first = {"title": "New research", "source": "First", "url": "https://example.com/a", "_categories": []}
        second = {**first, "source": "Second", "url": first["url"] + "#part", "_categories": ["Machine-learning"]}
        with patch.object(news, "_fetch_feed", return_value=("Feed", [first, second])):
            result = news.NewsProvider().fetch({
                "feeds": ["https://example.com/feed"], "topic_presets": ["ai", "science"], "max_items": 1,
            })
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["headlines"][0]["source"], "First")
        self.assertNotIn("_categories", result["headlines"][0])
        self.assertEqual(first["_categories"], [])

    def test_filter_signature_echoes_request_including_empty_feed(self) -> None:
        self.assertEqual(news.filter_signature({}), '[[],[]]')
        self.assertEqual(news.filter_signature({"topics": None}), '[[],[]]')
        options = {"feeds": [], "topic_presets": ["ai"], "topics": ["café", 'a "quote"']}
        self.assertEqual(news.NewsProvider().fetch(options)["filter_signature"],
            '[["ai"],["café","a \\"quote\\""]]')

    def test_custom_punctuation_is_literal_and_limit_applies_after_filter(self) -> None:
        self.assertEqual(news._filter_topics([{"title": "ordinary words"}], ["-"]), [])
        stories = [{"title": title, "url": f"https://example.com/{i}", "source": "Test"}
                   for i, title in enumerate(["Unrelated", "C++ update", "C++ release"])]
        with patch.object(news, "_fetch_feed", return_value=("Test", stories)):
            result = news.NewsProvider().fetch({"feeds": ["https://example.com/feed"],
                                                "topic_presets": ["technology"], "topics": ["C++"], "max_items": 1})
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["headlines"]), 1)
        self.assertIn("C++", result["headlines"][0]["title"])

    def test_atomic_filter_saves_reload_daemon_and_publish_matching_state(self) -> None:
        # Real Gio monitoring, config.save's atomic replace, and daemon polling;
        # only network fetching and unrelated providers are replaced.
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(config,
                CONFIG_DIR=tmp, CONFIG_PATH=str(Path(tmp) / "config.json"), STATE_DIR=tmp):
            cfg = config.Config(widgets=[config.Widget(type="news")], providers={
                "news": {"feeds": ["https://example.com/feed"], "interval": 900},
            })
            config.save(cfg)
            daemon = service.Daemon()
            daemon._instances = {"news": news.NewsProvider()}
            daemon._next_run = {"news": float("inf")}
            daemon._watch_config()
            try:
                with patch.object(daemon, "_sync_providers"), patch.object(daemon, "_fire_due_reminders"), \
                        patch.object(news, "_fetch_feed", return_value=news._parse_feed(RSS, "https://example.com/feed")):
                    for presets, expected in [(["ai"], 1), (["sports"], 0), ([], 0), (config.NEWS_TOPIC_IDS, 2)]:
                        cfg.providers["news"]["topic_presets"] = ["health"]
                        config.save(cfg)
                        cfg.providers["news"]["topic_presets"] = presets
                        config.save(cfg)
                        deadline = time.monotonic() + 2
                        context = service.GLib.MainContext.default()
                        while daemon._next_run["news"] != 0 and time.monotonic() < deadline:
                            while context.pending():
                                context.iteration(False)
                            time.sleep(0.01)
                        self.assertEqual(daemon._next_run["news"], 0, "Atomic settings save did not trigger immediate poll")
                        daemon._tick()
                        state = config.read_json(config.state_path("news"))
                        self.assertTrue(state["ok"])
                        self.assertEqual(len(state["data"]["headlines"]), expected)
                        self.assertEqual(state["data"]["filter_signature"], news.filter_signature(cfg.provider_options("news")))
                        self.assertEqual(state["data"]["request_signature"], news.request_signature(cfg.provider_options("news")))
                    good = state["data"]
                    cfg.providers["news"]["topic_presets"] = ["ai"]
                    with patch.object(news, "_fetch_feed", side_effect=ProviderError("offline")):
                        daemon._poll("news", daemon._instances["news"], cfg.provider_options("news"))
                    stale = config.read_json(config.state_path("news"))
                    self.assertFalse(stale["ok"])
                    self.assertIsNone(stale["data"], "A failed refresh republished headlines from excluded topics")
                    # An outage with unchanged settings still retains matching data.
                    daemon._last_good["news"] = good
                    cfg.providers["news"]["topic_presets"] = config.NEWS_TOPIC_IDS
                    with patch.object(news, "_fetch_feed", side_effect=ProviderError("offline")):
                        daemon._poll("news", daemon._instances["news"], cfg.provider_options("news"))
                    stale = config.read_json(config.state_path("news"))
                    self.assertTrue(stale["stale"])
                    self.assertEqual(stale["data"], good)
            finally:
                daemon._config_monitor.cancel()

    def test_all_fail_uses_provider_error_but_empty_configuration_is_valid(self) -> None:
        with patch.object(news, "_fetch_feed", side_effect=ProviderError("offline")):
            with self.assertRaisesRegex(ProviderError, "offline"):
                news.NewsProvider().fetch({"feeds": ["https://example.com/feed"]})

        self.assertEqual(
            news.NewsProvider().fetch({"feeds": []})["headlines"], []
        )

    def test_rejects_unsafe_oversized_and_malformed_feeds(self) -> None:
        with self.assertRaisesRegex(ProviderError, "HTTP or HTTPS"):
            news._fetch_feed("file:///tmp/private")

        oversized = FakeResponse(b"x" * (news.MAX_FEED_BYTES + 1))
        with patch.object(news.urllib.request, "urlopen", return_value=oversized):
            with self.assertRaisesRegex(ProviderError, "larger than 2 MB"):
                news._fetch_feed("https://example.com/feed")

        with self.assertRaisesRegex(ProviderError, "Invalid RSS/Atom XML"):
            news._parse_feed(b"<rss><broken>", "https://example.com/feed")

        for payload in (b"<html><title>Access denied</title></html>", b"<response/>", b"<rss/>"):
            with self.subTest(payload=payload), self.assertRaisesRegex(ProviderError, "not an RSS or Atom"):
                news._parse_feed(payload, "https://example.com/feed")
        source, items = news._parse_feed(
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><channel><title>RSS 1.0</title></channel></rdf:RDF>',
            "https://example.com/feed")
        self.assertEqual((source, items), ("RSS 1.0", []))

    def test_off_topics_exclude_overlaps_and_custom_matches(self) -> None:
        titles = ["AI software", "Medical AI study", "A new discovery", "Market update", "A chair", "Rare custom match",
                  "OpenAI software release", "LLMs in medical research", "ChatGPT technology update"]
        stories = [{"title": title, "url": f"https://example.com/{i}", "source": "Feed",
                    "_categories": ["Science", "Machine Learning"] if i == 2 else []}
                   for i, title in enumerate(titles)]
        options = {"feeds": ["https://example.com/feed"],
                   "topic_presets": ["business", "technology", "science", "health", "sports"],
                   "topics": ["AI", "Rare custom match"]}
        with patch.object(news, "_fetch_feed", return_value=("Feed", stories)):
            result = news.NewsProvider().fetch(options)
            self.assertEqual([s["title"] for s in result["headlines"]], ["Rare custom match", "Market update"])
            self.assertNotIn("_categories", result["headlines"][0])
            # All off remains empty even with custom keywords, without network.
            with patch.object(news, "_fetch_feed") as fetch:
                disabled = news.NewsProvider().fetch({**options, "topic_presets": []})
                self.assertEqual(disabled["headlines"], [])
                self.assertTrue(disabled["topics_disabled"])
                fetch.assert_not_called()
            all_on = news.NewsProvider().fetch({**options, "topic_presets": config.NEWS_TOPIC_IDS})
            self.assertEqual(all_on["total"], len(stories))

    def test_duplicate_ai_category_excludes_earlier_technology_story(self) -> None:
        first = {"title": "Software breakthrough", "url": "https://example.com/a", "source": "First"}
        second = {**first, "url": first["url"] + "#section", "_categories": ["Artificial Intelligence"]}
        with patch.object(news, "_fetch_feed", return_value=("Feed", [first, second])):
            result = news.NewsProvider().fetch({"feeds": ["https://example.com/feed"],
                                                "topic_presets": ["technology"]})
        self.assertEqual(result["total"], 0)

    def test_transient_feed_errors_retry_once_and_recover(self) -> None:
        url = "https://example.com/feed"
        for error in (urllib.error.URLError("offline"), TimeoutError(),
                      urllib.error.HTTPError(url, 429, "busy", {}, None),
                      urllib.error.HTTPError(url, 503, "busy", {}, None)):
            with self.subTest(error=error), patch.object(news.time, "sleep"), \
                    patch.object(news.urllib.request, "urlopen", side_effect=[error, FakeResponse(RSS)]) as fetch:
                self.assertEqual(len(news._fetch_feed(url)[1]), 2)
                self.assertEqual(fetch.call_count, 2)
        for code, attempts in ((404, 1), (403, 1), (503, 2)):
            with self.subTest(code=code), patch.object(news.time, "sleep"), \
                    patch.object(news.urllib.request, "urlopen", side_effect=urllib.error.HTTPError(url, code, "error", {}, None)) as fetch:
                with self.assertRaises(ProviderError):
                    news._fetch_feed(url)
                self.assertEqual(fetch.call_count, attempts)

    def test_partial_feed_failure_uses_backoff_and_success_resets_it(self) -> None:
        daemon = service.Daemon()
        provider = Mock()
        provider.fetch.return_value = {"headlines": [{"title": "Available"}], "problems": ["missing: HTTP 503"]}
        daemon._instances = {"news": provider}
        daemon._next_run = {"news": 0}
        with patch.object(daemon, "_write_state") as write, patch.object(daemon, "_fire_due_reminders"), \
                patch.object(service.time, "monotonic", return_value=100):
            daemon._tick()
            self.assertEqual(daemon._next_run["news"], 130)
            self.assertEqual(daemon._last_good["news"]["headlines"], [{"title": "Available"}])
            self.assertNotIn("error", write.call_args.kwargs)
            provider.fetch.return_value = {"headlines": [], "problems": []}
            daemon._next_run["news"] = 0
            daemon._tick()
            self.assertNotIn("news", daemon._failures)
            self.assertEqual(daemon._next_run["news"], 1000)

    @unittest.skipUnless(shutil.which("gjs"), "GJS is not installed")
    def test_request_signatures_agree_with_javascript_and_identify_changes(self) -> None:
        cases = [{}, {"topics": None}, {"topic_presets": []},
                 {"feeds": []}, {"max_items": 5},
                 {"topic_presets": ["AI", "science"], "topics": ["café", 'a "quote"']},
                 {"feeds": ["https://example.com/feed"], "topic_presets": ["ai"], "topics": "C++, café"}]
        script = Path(__file__).with_name("news_signature_test.js")
        result = subprocess.run(["gjs", "-m", str(script), json.dumps(cases)],
                                capture_output=True, text=True, timeout=10, check=True)
        signatures = json.loads(result.stdout)
        self.assertEqual(signatures, [news.request_signature(options) for options in cases])
        self.assertEqual(len(set(signatures)), len(cases) - 1)  # null uses the default

    def test_defaults_and_daemon_registration(self) -> None:
        self.assertEqual(len(config.DEFAULT_NEWS_FEEDS), 6)
        self.assertEqual(config.WIDGET_TYPES["news"]["provider"], "news")
        self.assertIs(service.provider_class("news"), news.NewsProvider)
        self.assertEqual(
            config.DEFAULT_PROVIDER_OPTIONS["news"]["interval"],
            int(datetime.timedelta(minutes=15).total_seconds()),
        )


if __name__ == "__main__":
    unittest.main()
