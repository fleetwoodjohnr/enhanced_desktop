"""Compact headlines from user-configurable RSS and Atom feeds."""
from __future__ import annotations

import concurrent.futures
import datetime
import email.utils
import html
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .. import config
from .base import Provider, ProviderError, TIMEOUT, USER_AGENT

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_WORKERS = 6
HTTP_SCHEMES = {"http", "https"}
TAG_RE = re.compile(r"<[^>]*>")
SPACE_RE = re.compile(r"\s+")
UTC = datetime.timezone.utc


class NewsProvider(Provider):
    name = "news"

    def fetch(self, options: dict[str, Any]) -> dict[str, Any]:
        # Keep the legacy echo for an extension already loaded before upgrade.
        signature = filter_signature(options)
        options = {key: options.get(key) if options.get(key) is not None else value
                   for key, value in config.DEFAULT_PROVIDER_OPTIONS["news"].items()}
        configured = options.get("feeds")
        feeds = _unique_urls(configured if isinstance(configured, list) else [])
        presets = _preset_ids(options.get("topic_presets"))
        custom = _topics(options.get("topics"))
        # Presets are matched by their keywords but reported by their labels:
        # the widget shows this list to the user, and "Artificial intelligence"
        # reads better than the keywords standing behind it.
        topics = _preset_labels(presets) + custom
        request = request_signature(options)
        try:
            max_items = int(options.get("max_items", 40))
        except (TypeError, ValueError):
            max_items = 40
        max_items = max(1, min(max_items, 100))

        if not feeds or not presets:
            return {
                "headlines": [],
                "total": 0,
                "sources": [],
                "problems": [],
                "topics": topics,
                "filter_signature": signature,
                "request_signature": request,
                "topics_disabled": not presets,
            }

        results: dict[int, tuple[str, list[dict[str, Any]]]] = {}
        failures: dict[int, str] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(MAX_WORKERS, len(feeds)),
            thread_name_prefix="desktop-forge-news",
        ) as executor:
            pending = {
                executor.submit(_fetch_feed, url): (index, url)
                for index, url in enumerate(feeds)
            }
            for future in concurrent.futures.as_completed(pending):
                index, url = pending[future]
                try:
                    results[index] = future.result()
                except ProviderError as exc:
                    failures[index] = f"{_host(url)}: {exc}"

        problems = [failures[index] for index in sorted(failures)]

        if not results:
            detail = "; ".join(problems[:3]) or "No news feed answered"
            raise ProviderError(detail)

        sources: list[str] = []
        headlines: list[dict[str, Any]] = []
        for index in sorted(results):
            source, items = results[index]
            if source not in sources:
                sources.append(source)
            headlines.extend(items)

        headlines = _deduplicate(headlines)
        excluded = [preset for preset in config.NEWS_TOPIC_IDS if preset not in presets]
        if excluded:
            headlines = _filter_topics(
                headlines, custom, preset_keywords=_preset_keywords(presets),
                excluded_keywords=_preset_keywords(excluded),
            )
        headlines.sort(key=_sort_key, reverse=True)
        return {
            "headlines": [_public_story(item) for item in headlines[:max_items]],
            "total": len(headlines),
            "sources": sources,
            "problems": problems,
            "topics": topics,
            "filter_signature": signature,
            "request_signature": request,
            "topics_disabled": False,
        }


def request_signature(options: dict[str, Any]) -> str:
    """Versioned request identity; mirrors newsRequestSignature in JavaScript."""
    defaults = config.DEFAULT_PROVIDER_OPTIONS["news"]
    values = [options.get(key) if options.get(key) is not None else defaults[key]
              for key in ("feeds", "topic_presets", "topics", "max_items")]
    return json.dumps([3, *values], ensure_ascii=False, separators=(",", ":"))


def filter_signature(options: dict[str, Any]) -> str:
    """Identify the requested filters, including the spelling saved by the UI.

    Mirrors newsFilterSignature in the extension. Echoing the request rather
    than expanded keywords lets the widget reject an older filter's cache.
    """
    values = [options.get(key) for key in ("topic_presets", "topics")]
    return json.dumps([[] if value is None else value for value in values],
                      ensure_ascii=False, separators=(",", ":"))


def _fetch_feed(url: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise ProviderError("Invalid feed URL") from exc
    if parsed.scheme.lower() not in HTTP_SCHEMES or not parsed.netloc:
        raise ProviderError("Feed URL must use HTTP or HTTPS")

    for attempt in range(2):
        try:
            return _fetch_feed_once(url)
        except _TemporaryFeedError:
            if attempt:
                raise
            time.sleep(0.5)
    raise AssertionError("unreachable")


class _TemporaryFeedError(ProviderError):
    """A connection or upstream failure worth one bounded retry."""


def _fetch_feed_once(url: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        })
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            final_url = response.geturl()
            if urllib.parse.urlsplit(final_url).scheme.lower() not in HTTP_SCHEMES:
                raise ProviderError("Feed redirected to an unsupported URL")
            payload = response.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as exc:
        error = _TemporaryFeedError if exc.code == 429 or 500 <= exc.code <= 599 else ProviderError
        exc.close()
        raise error(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise ProviderError("Certificate verification failed") from exc
        reason = "Secure connection interrupted" if isinstance(exc.reason, ssl.SSLError) else "Cannot connect"
        raise _TemporaryFeedError(reason) from exc
    except (TimeoutError, OSError) as exc:
        raise _TemporaryFeedError("Connection timed out" if isinstance(exc, TimeoutError) else "Connection interrupted") from exc
    except ValueError as exc:
        raise ProviderError("Invalid feed URL") from exc

    if len(payload) > MAX_FEED_BYTES:
        raise ProviderError("Feed is larger than 2 MB")
    return _parse_feed(payload, final_url)


def _parse_feed(payload: bytes, feed_url: str) -> tuple[str, list[dict[str, Any]]]:
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, ValueError) as exc:
        raise ProviderError("Invalid RSS/Atom XML") from exc

    if _local_name(root.tag) == "feed":
        return _parse_atom(root, feed_url)
    if _local_name(root.tag) not in ("rss", "rdf") or _child(root, "channel") is None:
        raise ProviderError("Response is not an RSS or Atom feed")
    return _parse_rss(root, feed_url)


def _parse_rss(root: ET.Element, feed_url: str) -> tuple[str, list[dict[str, Any]]]:
    channel = next(
        (element for element in root.iter() if _local_name(element.tag) == "channel"),
        root,
    )
    source = _clean_text(_child_text(channel, "title")) or _host(feed_url)
    headlines = []
    for item in (element for element in root.iter() if _local_name(element.tag) == "item"):
        title = _clean_text(_child_text(item, "title"))
        link = _child_text(item, "link")
        if not link:
            guid = _child(item, "guid")
            if guid is not None and guid.attrib.get("isPermaLink", "true").lower() != "false":
                link = _text(guid)
        story = _story(title, link, source, _first_child_text(
            item, ("pubDate", "published", "updated", "date")
        ), feed_url, _categories(item))
        if story is not None:
            headlines.append(story)
    return source, headlines


def _parse_atom(root: ET.Element, feed_url: str) -> tuple[str, list[dict[str, Any]]]:
    source = _clean_text(_child_text(root, "title")) or _host(feed_url)
    headlines = []
    for entry in (element for element in root if _local_name(element.tag) == "entry"):
        title = _clean_text(_child_text(entry, "title"))
        link = ""
        for element in entry:
            if _local_name(element.tag) != "link":
                continue
            if element.attrib.get("rel", "alternate") == "alternate":
                link = element.attrib.get("href", "")
                if link:
                    break
        story = _story(
            title,
            link,
            source,
            _first_child_text(entry, ("published", "updated")),
            feed_url,
            _categories(entry),
        )
        if story is not None:
            headlines.append(story)
    return source, headlines


def _story(
    title: str,
    link: str,
    source: str,
    published: str,
    feed_url: str,
    categories: list[str] | None = None,
) -> dict[str, Any] | None:
    try:
        url = urllib.parse.urljoin(feed_url, link.strip()) if link else ""
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if not title or parsed.scheme.lower() not in HTTP_SCHEMES or not parsed.netloc:
        return None
    return {
        "title": title,
        "url": url,
        "source": source,
        "published": _published_iso(published),
        "_categories": categories or [],
    }


def _categories(parent: ET.Element) -> list[str]:
    """Read RSS category text and Atom category terms without exposing them."""
    values = []
    seen = set()
    for element in parent:
        if _local_name(element.tag) != "category":
            continue
        value = _clean_text(element.attrib.get("term", "") or _text(element))
        folded = value.casefold()
        if value and folded not in seen:
            seen.add(folded)
            values.append(value)
    return values


def _preset_ids(configured: Any) -> list[str]:
    """Keep the configured preset ids that still exist, in preset order.

    A hand-edited config naming a preset that has since been renamed must not
    break the fetch -- an unknown id simply filters nothing.
    """
    if not isinstance(configured, list):
        return []
    wanted = {
        value.strip().casefold() for value in configured if isinstance(value, str)
    }
    return [
        preset["id"]
        for preset in config.NEWS_TOPIC_PRESETS
        if preset["id"] in wanted
    ]


def _preset_keywords(ids: list[str]) -> list[str]:
    chosen = set(ids)
    keywords: list[str] = []
    for preset in config.NEWS_TOPIC_PRESETS:
        if preset["id"] in chosen:
            keywords.extend(preset["keywords"])
    return keywords


def _preset_labels(ids: list[str]) -> list[str]:
    chosen = set(ids)
    return [
        preset["label"]
        for preset in config.NEWS_TOPIC_PRESETS
        if preset["id"] in chosen
    ]


def _topics(configured: Any) -> list[str]:
    if isinstance(configured, str):
        configured = configured.split(",")
    if not isinstance(configured, list):
        return []
    result = []
    seen = set()
    for value in configured:
        if not isinstance(value, str):
            continue
        topic = SPACE_RE.sub(" ", value).strip()
        folded = topic.casefold()
        if not topic or folded in seen:
            continue
        seen.add(folded)
        result.append(topic)
    return result


def _filter_topics(
    headlines: list[dict[str, Any]], topics: list[str], *,
    preset_keywords: list[str] | None = None,
    excluded_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not topics and not preset_keywords and not excluded_keywords:
        return headlines

    def pattern(topic: str, preset: bool) -> re.Pattern[str]:
        words = re.split(r"[\s\-\u2010-\u2014]+", topic)
        body = (r"[\s\-\u2010-\u2014]+".join(re.escape(word) for word in words)
                if all(words) else re.escape(topic))
        if preset and topic == "U.S.":
            body = r"U\.S\.?"
        # The country abbreviation must not match the English pronoun. A
        # user explicitly entering custom "us" still gets a literal match.
        flags = 0 if preset and topic == "US" else re.IGNORECASE
        return re.compile(rf"(?<!\w){body}(?!\w)", flags)

    patterns = [pattern(topic, False) for topic in topics]
    patterns.extend(pattern(topic, True) for topic in preset_keywords or [])
    excluded = [pattern(topic, True) for topic in excluded_keywords or []]
    result = []
    for story in headlines:
        searchable = [
            str(story.get("title") or ""),
            *(str(value) for value in story.get("_categories") or []),
        ]
        if any(regex.search(field) for regex in excluded for field in searchable):
            continue
        if not patterns or any(regex.search(field) for regex in patterns for field in searchable):
            result.append(story)
    return result


def _public_story(story: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in story.items() if not key.startswith("_")}


def _published_iso(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = {}
    for item in items:
        canonical_url = urllib.parse.urlsplit(item["url"])._replace(fragment="").geturl()
        key = canonical_url.casefold() or (
            item["source"].strip().casefold(), item["title"].strip().casefold()
        )
        if key in seen:
            previous = seen[key]
            previous["_categories"] = _topics([
                *previous.get("_categories", []), *item.get("_categories", []),
            ])
            continue
        merged = {**item, "_categories": list(item.get("_categories", []))}
        seen[key] = merged
        result.append(merged)
    return result


def _sort_key(item: dict[str, Any]) -> tuple[int, float, str]:
    published = item.get("published")
    if published:
        try:
            stamp = datetime.datetime.fromisoformat(published).timestamp()
            return (1, stamp, item["title"].casefold())
        except (TypeError, ValueError, OverflowError):
            pass
    return (0, 0.0, item["title"].casefold())


def _unique_urls(values: list[Any]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        url = value.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    wanted = name.lower()
    return next(
        (element for element in parent if _local_name(element.tag) == wanted), None
    )


def _child_text(parent: ET.Element, name: str) -> str:
    element = _child(parent, name)
    return _text(element) if element is not None else ""


def _first_child_text(parent: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        value = _child_text(parent, name)
        if value:
            return value
    return ""


def _text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def _clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub("", value))).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or url
    except ValueError:
        return "Invalid feed URL"
