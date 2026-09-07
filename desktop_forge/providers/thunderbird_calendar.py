"""Read upcoming events from Thunderbird's on-disk calendar cache.

Thunderbird keeps local calendars in ``calendar-data/local.sqlite`` and the
offline copies of network calendars in ``calendar-data/cache.sqlite``.  Those
databases are deliberately treated as read-only here: Thunderbird remains the
owner of syncing and editing them, while Desktop Forge only projects their
contents into the same plain JSON shape used for Evolution Data Server events.
"""
from __future__ import annotations

import configparser
import datetime
import io
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dateutil.rrule import rrulestr
    from dateutil.tz import tzical
except ImportError:  # Non-recurring events still work without this optional package.
    rrulestr = None
    tzical = None

EVENT_ALLDAY = 8
HAS_RECURRENCE = 16
OFFLINE_DELETED = 4
UTC = datetime.timezone.utc

PREF_RE = re.compile(
    r'^user_pref\("calendar\.registry\.([^."\\]+)\.([^"\\]+)",\s*(.*?)\);\s*$'
)


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    name: str


@dataclass(frozen=True)
class Profile:
    name: str
    path: Path


def thunderbird_roots() -> list[Path]:
    """Known Thunderbird profile roots on Fedora, native and Flatpak."""
    home = Path.home()
    candidates = [
        home / ".thunderbird",
        home / ".var/app/org.mozilla.thunderbird/.thunderbird",
    ]
    return [path for path in candidates if path.is_dir()]


def discover_profiles(roots: list[Path] | None = None) -> list[Profile]:
    """Return every declared profile that still exists.

    Inspecting all profiles is intentional.  ``profiles.ini`` can contain a
    stale ``Default=1`` entry while an installation-specific ``Default`` points
    at the profile Thunderbird actually opens.  Empty/old profiles cost almost
    nothing and exact event de-duplication prevents them duplicating a current
    profile's data.
    """
    profiles: list[Profile] = []
    seen: set[Path] = set()

    for root in roots if roots is not None else thunderbird_roots():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with (root / "profiles.ini").open(encoding="utf-8") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error):
            continue

        for section in parser.sections():
            if not section.startswith("Profile"):
                continue
            raw_path = parser.get(section, "Path", fallback="").strip()
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if parser.getboolean(section, "IsRelative", fallback=True):
                path = root / path
            try:
                path = path.resolve()
            except OSError:
                continue
            if path in seen or not path.is_dir():
                continue
            seen.add(path)
            profiles.append(Profile(parser.get(section, "Name", fallback=section), path))

    return profiles


def read_calendars(profile: Profile) -> list[CalendarInfo]:
    """Parse enabled calendars which Thunderbird displays in its main view."""
    grouped: dict[str, dict[str, Any]] = {}
    try:
        lines = (profile.path / "prefs.js").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        match = PREF_RE.match(line)
        if not match:
            continue
        calendar_id, key, raw_value = match.groups()
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            continue
        grouped.setdefault(calendar_id, {})[key] = value

    calendars = []
    for calendar_id, prefs in grouped.items():
        if prefs.get("disabled") is True:
            continue
        if prefs.get("calendar-main-in-composite") is False:
            continue
        if not prefs.get("type") or not prefs.get("uri"):
            continue
        calendars.append(
            CalendarInfo(calendar_id, str(prefs.get("name") or "Thunderbird Calendar"))
        )
    return calendars


def fetch_events(
    now: datetime.datetime,
    end: datetime.datetime,
    *,
    roots: list[Path] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str], set[Path]]:
    """Read all usable Thunderbird profiles for the requested UTC range."""
    events: list[dict[str, Any]] = []
    calendars_found: list[str] = []
    problems: list[str] = []
    watch_dirs: set[Path] = set()

    profiles = discover_profiles(roots)
    show_profile = len(profiles) > 1
    for profile in profiles:
        watch_dirs.add(profile.path)
        calendar_data = profile.path / "calendar-data"
        if calendar_data.is_dir():
            watch_dirs.add(calendar_data)

        calendars = read_calendars(profile)
        if not calendars:
            continue
        names = {calendar.id: calendar.name for calendar in calendars}
        for calendar in calendars:
            suffix = f" ({profile.name})" if show_profile else ""
            calendars_found.append(f"Thunderbird — {calendar.name}{suffix}")

        for database_name in ("local.sqlite", "cache.sqlite"):
            database = calendar_data / database_name
            if not database.is_file():
                continue
            try:
                events.extend(_events_from_database(database, names, now, end))
            except (OSError, sqlite3.Error, ValueError) as exc:
                problems.append(f"Thunderbird {profile.name}: {exc}")

    return events, _unique(calendars_found), problems, watch_dirs


def _events_from_database(
    path: Path,
    calendar_names: dict[str, str],
    now: datetime.datetime,
    end: datetime.datetime,
) -> list[dict[str, Any]]:
    # A normal read-only connection sees committed WAL contents while refusing
    # every accidental write.  Do not use immutable=1: it ignores the WAL file
    # while Thunderbird is running and would therefore miss its newest events.
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cal_events)")
        }
        required = {
            "cal_id", "id", "title", "flags", "event_start", "event_end",
            "event_start_tz", "event_end_tz", "recurrence_id",
            "recurrence_id_tz", "ical_status", "offline_journal",
        }
        if not required.issubset(columns):
            raise ValueError(f"unsupported calendar schema in {path.name}")

        ids = tuple(calendar_names)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = list(connection.execute(
            f"SELECT * FROM cal_events WHERE cal_id IN ({placeholders})",
            ids,
        ))
        recurrence = _recurrence_rows(connection, ids)
        locations = _location_rows(connection, ids)
    finally:
        connection.close()

    masters: list[sqlite3.Row] = []
    exceptions: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        if row["recurrence_id"] is None:
            masters.append(row)
        else:
            exceptions.setdefault((row["cal_id"], row["id"]), []).append(row)

    result: list[dict[str, Any]] = []
    for row in masters:
        if _is_deleted(row):
            continue
        key = (row["cal_id"], row["id"])
        if int(row["flags"] or 0) & HAS_RECURRENCE:
            result.extend(_expand_recurring(
                row,
                recurrence.get(key, []),
                exceptions.get(key, []),
                locations,
                calendar_names[row["cal_id"]],
                now,
                end,
            ))
            continue

        event = _row_to_event(
            row,
            calendar_names[row["cal_id"]],
            locations.get((row["cal_id"], row["id"], None), ""),
        )
        if event is not None and _overlaps(event, now, end):
            result.append(event)
    return result


def _recurrence_rows(
    connection: sqlite3.Connection, ids: tuple[str, ...]
) -> dict[tuple[str, str], list[str]]:
    if not _has_table(connection, "cal_recurrence"):
        return {}
    placeholders = ",".join("?" for _ in ids)
    result: dict[tuple[str, str], list[str]] = {}
    for row in connection.execute(
        f"SELECT cal_id, item_id, icalString FROM cal_recurrence "
        f"WHERE cal_id IN ({placeholders})",
        ids,
    ):
        if row["icalString"]:
            result.setdefault((row["cal_id"], row["item_id"]), []).append(
                str(row["icalString"]).replace("\r\n ", "")
            )
    return result


def _location_rows(
    connection: sqlite3.Connection, ids: tuple[str, ...]
) -> dict[tuple[str, str, int | None], str]:
    if not _has_table(connection, "cal_properties"):
        return {}
    placeholders = ",".join("?" for _ in ids)
    result = {}
    for row in connection.execute(
        f"SELECT cal_id, item_id, recurrence_id, value FROM cal_properties "
        f"WHERE key = 'LOCATION' AND cal_id IN ({placeholders})",
        ids,
    ):
        value = row["value"]
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        result[(row["cal_id"], row["item_id"], row["recurrence_id"])] = str(value or "")
    return result


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _expand_recurring(
    master: sqlite3.Row,
    recurrence_lines: list[str],
    exception_rows: list[sqlite3.Row],
    locations: dict[tuple[str, str, int | None], str],
    calendar_name: str,
    now: datetime.datetime,
    end: datetime.datetime,
) -> list[dict[str, Any]]:
    all_day = bool(int(master["flags"] or 0) & EVENT_ALLDAY)
    start = _native_datetime(master["event_start"], master["event_start_tz"], all_day)
    if start is None or not recurrence_lines or rrulestr is None:
        return []

    duration = 0
    if master["event_start"] is not None and master["event_end"] is not None:
        duration = max(0, int(master["event_end"]) - int(master["event_start"]))

    try:
        recurrence = rrulestr(
            "\n".join(recurrence_lines),
            dtstart=start,
            forceset=True,
            tzids=_tzids,
        )
        range_start = _range_in_zone(now, start) - datetime.timedelta(
            microseconds=duration
        )
        range_end = _range_in_zone(end, start)
        occurrences = recurrence.between(range_start, range_end, inc=True)
    except (TypeError, ValueError, OverflowError):
        return []

    exception_map: dict[str, sqlite3.Row] = {}
    for row in exception_rows:
        recurrence_start = _native_datetime(
            row["recurrence_id"], row["recurrence_id_tz"], all_day
        )
        if recurrence_start is not None:
            exception_map[_occurrence_key(recurrence_start, all_day)] = row

    result = []
    handled_exceptions: set[str] = set()
    for occurrence in occurrences:
        occurrence_key = _occurrence_key(occurrence, all_day)
        override = exception_map.get(occurrence_key)
        if override is not None:
            handled_exceptions.add(occurrence_key)
            if _is_deleted(override):
                continue
            location = locations.get(
                (override["cal_id"], override["id"], override["recurrence_id"]),
                locations.get((master["cal_id"], master["id"], None), ""),
            )
            event = _row_to_event(override, calendar_name, location)
        else:
            event = _occurrence_to_event(
                master,
                occurrence,
                duration,
                calendar_name,
                locations.get((master["cal_id"], master["id"], None), ""),
            )
        if event is not None and _overlaps(event, now, end):
            result.append(event)

    # A modified occurrence can be moved into this window from a recurrence
    # ID outside it. Such an override does not appear in ``between()`` above,
    # so inspect the remaining exception rows by their actual start time.
    for occurrence_key, override in exception_map.items():
        if occurrence_key in handled_exceptions or _is_deleted(override):
            continue
        location = locations.get(
            (override["cal_id"], override["id"], override["recurrence_id"]),
            locations.get((master["cal_id"], master["id"], None), ""),
        )
        event = _row_to_event(override, calendar_name, location)
        if event is not None and _overlaps(event, now, end):
            result.append(event)
    return result


def _row_to_event(
    row: sqlite3.Row, calendar_name: str, location: str
) -> dict[str, Any] | None:
    all_day = bool(int(row["flags"] or 0) & EVENT_ALLDAY)
    start = _native_datetime(row["event_start"], row["event_start_tz"], all_day)
    if start is None:
        return None
    end = _native_datetime(row["event_end"], row["event_end_tz"], all_day)
    return {
        "summary": row["title"] or "(untitled)",
        "start": _iso(start, all_day),
        "end": _iso(end, all_day) if end is not None else None,
        "all_day": all_day,
        "location": location,
        "calendar": calendar_name,
        "source": "thunderbird",
    }


def _occurrence_to_event(
    row: sqlite3.Row,
    occurrence: datetime.datetime,
    duration_microseconds: int,
    calendar_name: str,
    location: str,
) -> dict[str, Any]:
    all_day = bool(int(row["flags"] or 0) & EVENT_ALLDAY)
    finish = occurrence + datetime.timedelta(microseconds=duration_microseconds)
    return {
        "summary": row["title"] or "(untitled)",
        "start": _iso(occurrence, all_day),
        "end": _iso(finish, all_day),
        "all_day": all_day,
        "location": location,
        "calendar": calendar_name,
        "source": "thunderbird",
    }


def _native_datetime(
    native: int | None, timezone_text: str | None, all_day: bool
) -> datetime.datetime | None:
    if native is None:
        return None
    instant = datetime.datetime.fromtimestamp(int(native) / 1_000_000, UTC)
    if all_day:
        return instant.replace(tzinfo=None)
    if not timezone_text or timezone_text == "floating":
        # Thunderbird encodes floating wall-clock digits as if they were UTC.
        return instant.replace(tzinfo=None)
    return instant.astimezone(_tzids(timezone_text))


def _tzids(timezone_text: str) -> datetime.tzinfo:
    if not timezone_text or timezone_text in ("UTC", "Etc/UTC", "GMT", "floating"):
        return UTC
    if timezone_text.startswith("BEGIN:VTIMEZONE") and tzical is not None:
        try:
            parsed = tzical(io.StringIO(timezone_text))
            return parsed.get()
        except (ValueError, TypeError):
            return UTC
    try:
        return ZoneInfo(timezone_text)
    except ZoneInfoNotFoundError:
        return UTC


def _range_in_zone(value: datetime.datetime, example: datetime.datetime) -> datetime.datetime:
    if example.tzinfo is None:
        return value.astimezone().replace(tzinfo=None)
    return value.astimezone(example.tzinfo)


def _iso(value: datetime.datetime, all_day: bool) -> str:
    if all_day:
        return value.date().isoformat()
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(UTC).isoformat()


def _occurrence_key(value: datetime.datetime, all_day: bool) -> str:
    return _iso(value, all_day)


def _is_deleted(row: sqlite3.Row) -> bool:
    return (
        str(row["ical_status"] or "").upper() == "CANCELLED"
        or row["offline_journal"] == OFFLINE_DELETED
    )


def _overlaps(
    event: dict[str, Any], now: datetime.datetime, end: datetime.datetime
) -> bool:
    if event["all_day"]:
        start = datetime.date.fromisoformat(event["start"])
        finish = datetime.date.fromisoformat(event["end"] or event["start"])
        if finish <= start:
            finish = start + datetime.timedelta(days=1)
        return finish > now.astimezone().date() and start < end.astimezone().date()

    start = datetime.datetime.fromisoformat(event["start"])
    finish = datetime.datetime.fromisoformat(event["end"] or event["start"])
    return finish >= now and start < end


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
