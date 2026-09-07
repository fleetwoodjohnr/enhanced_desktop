from __future__ import annotations

import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

from desktop_forge.providers import thunderbird_calendar as thunderbird

UTC = datetime.timezone.utc


def native(value: datetime.datetime) -> int:
    return int(value.timestamp() * 1_000_000)


class ThunderbirdCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / ".thunderbird"
        self.profile = self.root / "Profiles/default"
        data = self.profile / "calendar-data"
        data.mkdir(parents=True)
        self.root.mkdir(exist_ok=True)
        (self.root / "profiles.ini").write_text(
            "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/default\nDefault=1\n",
            encoding="utf-8",
        )
        (self.profile / "prefs.js").write_text(
            '\n'.join((
                'user_pref("calendar.registry.home.name", "Home");',
                'user_pref("calendar.registry.home.type", "storage");',
                'user_pref("calendar.registry.home.uri", "moz-storage-calendar://");',
                'user_pref("calendar.registry.hidden.name", "Hidden");',
                'user_pref("calendar.registry.hidden.type", "storage");',
                'user_pref("calendar.registry.hidden.uri", "moz-storage-calendar://");',
                'user_pref("calendar.registry.hidden.disabled", true);',
            )),
            encoding="utf-8",
        )
        self.database = data / "local.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.executescript(
            """
            CREATE TABLE cal_events (
                cal_id TEXT, id TEXT, title TEXT, ical_status TEXT, flags INTEGER,
                event_start INTEGER, event_end INTEGER, event_start_tz TEXT,
                event_end_tz TEXT, recurrence_id INTEGER, recurrence_id_tz TEXT,
                offline_journal INTEGER
            );
            CREATE TABLE cal_recurrence (item_id TEXT, cal_id TEXT, icalString TEXT);
            CREATE TABLE cal_properties (
                item_id TEXT, key TEXT, value BLOB, recurrence_id INTEGER,
                recurrence_id_tz TEXT, cal_id TEXT
            );
            """
        )

    def insert_event(
        self,
        event_id: str,
        title: str,
        start: datetime.datetime,
        finish: datetime.datetime,
        *,
        flags: int = 0,
        recurrence_id: datetime.datetime | None = None,
        status: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO cal_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "home", event_id, title, status, flags, native(start), native(finish),
                "UTC", "UTC", native(recurrence_id) if recurrence_id else None,
                "UTC" if recurrence_id else None, None,
            ),
        )

    def test_reads_enabled_profile_events_and_location(self) -> None:
        now = datetime.datetime(2026, 9, 4, 12, tzinfo=UTC)
        self.insert_event(
            "meeting", "Project meeting", now + datetime.timedelta(hours=1),
            now + datetime.timedelta(hours=2),
        )
        self.connection.execute(
            "INSERT INTO cal_properties VALUES (?, ?, ?, ?, ?, ?)",
            ("meeting", "LOCATION", b"Conference room", None, None, "home"),
        )
        self.connection.commit()

        events, calendars, problems, watches = thunderbird.fetch_events(
            now, now + datetime.timedelta(days=2), roots=[self.root]
        )

        self.assertEqual(calendars, ["Thunderbird — Home"])
        self.assertEqual(problems, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "Project meeting")
        self.assertEqual(events[0]["location"], "Conference room")
        self.assertEqual(events[0]["source"], "thunderbird")
        self.assertIn(self.profile / "calendar-data", watches)

    def test_reads_all_day_events_as_dates(self) -> None:
        now = datetime.datetime(2026, 9, 4, 12, tzinfo=UTC)
        start = datetime.datetime(2026, 9, 6, tzinfo=UTC)
        self.insert_event(
            "holiday", "All day", start, start + datetime.timedelta(days=1),
            flags=thunderbird.EVENT_ALLDAY,
        )
        self.connection.commit()

        events, _calendars, problems, _watches = thunderbird.fetch_events(
            now, now + datetime.timedelta(days=4), roots=[self.root]
        )

        self.assertEqual(problems, [])
        self.assertEqual(events[0]["start"], "2026-09-06")
        self.assertEqual(events[0]["end"], "2026-09-07")
        self.assertTrue(events[0]["all_day"])

    def test_recurring_spans_window_and_honors_moved_and_cancelled_overrides(self) -> None:
        now = datetime.datetime(2026, 9, 5, 12, tzinfo=UTC)
        initial = datetime.datetime(2026, 9, 4, 11, tzinfo=UTC)
        self.insert_event(
            "daily", "Daily coverage", initial,
            initial + datetime.timedelta(hours=26), flags=thunderbird.HAS_RECURRENCE,
        )
        self.connection.execute(
            "INSERT INTO cal_recurrence VALUES (?, ?, ?)",
            ("daily", "home", "RRULE:FREQ=DAILY;COUNT=4"),
        )

        second = initial + datetime.timedelta(days=1)
        self.insert_event(
            "daily", "Daily coverage", second, second + datetime.timedelta(hours=26),
            flags=thunderbird.HAS_RECURRENCE, recurrence_id=second, status="CANCELLED",
        )

        outside_id = initial + datetime.timedelta(days=3)
        moved = now + datetime.timedelta(hours=3)
        self.insert_event(
            "daily", "Moved coverage", moved, moved + datetime.timedelta(hours=1),
            flags=thunderbird.HAS_RECURRENCE, recurrence_id=outside_id,
        )
        self.connection.commit()

        events, _calendars, problems, _watches = thunderbird.fetch_events(
            now, now + datetime.timedelta(hours=20), roots=[self.root]
        )

        self.assertEqual(problems, [])
        summaries = [event["summary"] for event in events]
        # The first 26-hour occurrence still overlaps at noon. The next day's
        # occurrence is cancelled, so only one regular instance survives.
        self.assertEqual(summaries.count("Daily coverage"), 1)
        self.assertEqual(summaries.count("Moved coverage"), 1)


if __name__ == "__main__":
    unittest.main()
