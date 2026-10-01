"""Calendars: Evolution Data Server (GNOME Calendar, Google/CalDAV accounts) and Thunderbird.

EDS calendars can be read and changed. Thunderbird's calendars are read from
its local cache, which CLIVE never writes to, so an event there is read-only
and says so. An event is named by an opaque ID such as "eds:<calendar>:<uid>:"
that search results hand back.
"""
from __future__ import annotations

import datetime
import uuid

from .base import Capability, Guards, Integration, Tool
from .schemas import STRING, TEXT

ID = "calendar"
MAX_EVENTS = 100

CAPABILITIES = (
    Capability("view", "See events", "read", "low"),
    Capability("search", "Search events", "search", "low"),
    Capability("create", "Create events", "create", "normal", default=False),
    Capability("modify", "Change events", "modify", "normal", default=False),
    Capability("reschedule", "Move events to another time", "modify", "normal", default=False),
    Capability("cancel", "Cancel events", "delete", "high",
               "Removes the event from its calendar; always asks first.", default=False),
)


def parse_time(value: str) -> datetime.datetime:
    """An ISO time from the model, as an aware datetime; naive means local time."""
    moment = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def is_date(value: str) -> bool:
    return len(value.strip()) == 10


class EdsBackend:
    """The thin layer over ECal; everything above it is plain dicts."""

    def __init__(self):
        import gi
        gi.require_version("EDataServer", "1.2")
        gi.require_version("ECal", "2.0")
        gi.require_version("ICalGLib", "3.0")
        from gi.repository import ECal, EDataServer, ICalGLib
        self.ECal, self.EDataServer, self.ICalGLib = ECal, EDataServer, ICalGLib
        self.registry = EDataServer.SourceRegistry.new_sync(None)
        self.clients = {}

    def _client(self, source):
        uid = source.get_uid()
        if uid not in self.clients:
            self.clients[uid] = self.ECal.Client.connect_sync(
                source, self.ECal.ClientSourceType.EVENTS, 10, None)
        return self.clients[uid]

    def _sources(self):
        return [s for s in self.registry.list_sources(self.EDataServer.SOURCE_EXTENSION_CALENDAR)
                if s.get_enabled()]

    def calendars(self):
        result = []
        for source in self._sources():
            try:
                writable = not self._client(source).is_readonly()
            except Exception:  # noqa: BLE001 - an offline calendar is listed as read-only
                writable = False
            result.append({"id": source.get_uid(), "name": source.get_display_name() or "Calendar",
                           "writable": writable})
        return result

    def events(self, start, end, query):
        from ...providers.calendar import component_to_event
        utc = "%Y%m%dT%H%M%SZ"
        sexp = (f'(occur-in-time-range? (make-time "{start.astimezone(datetime.timezone.utc).strftime(utc)}") '
                f'(make-time "{end.astimezone(datetime.timezone.utc).strftime(utc)}"))')
        if query:
            escaped = query.replace("\\", "\\\\").replace('"', '\\"')
            sexp = f'(and {sexp} (contains? "any" "{escaped}"))'
        found = []
        for source in self._sources():
            name = source.get_display_name() or "Calendar"
            try:
                client = self._client(source)
                ok, components = client.get_object_list_as_comps_sync(sexp, None)
            except Exception:  # noqa: BLE001 - one unreachable calendar hides only itself
                continue
            for component in (components or []) if ok else []:
                event = component_to_event(component, name, client)
                if event is None:
                    continue
                ident = component.get_id()
                event["id"] = f"eds:{source.get_uid()}:{ident.get_uid()}:{ident.get_rid() or ''}"
                description = component.get_descriptions()
                event["description"] = (description[0].get_value() if description else "")[:2000]
                event["writable"] = not client.is_readonly()
                found.append(event)
        return found

    def _time(self, value: str):
        if is_date(value):
            return self.ICalGLib.Time.new_from_string(value.replace("-", ""))
        moment = parse_time(value).astimezone(datetime.timezone.utc)
        return self.ICalGLib.Time.new_from_string(moment.strftime("%Y%m%dT%H%M%SZ"))

    def _locate(self, event_id):
        kind, source_uid, uid, rid = (event_id.split(":", 3) + ["", "", "", ""])[:4]
        if kind != "eds":
            raise ValueError("That event is in a read-only calendar (Thunderbird's)")
        source = self.registry.ref_source(source_uid)
        if source is None:
            raise ValueError("That event's calendar is no longer available")
        client = self._client(source)
        if client.is_readonly():
            raise ValueError("That calendar is read-only")
        return client, uid, rid or None

    def create(self, calendar_id, fields):
        sources = {s.get_uid(): s for s in self._sources()}
        if calendar_id:
            source = sources.get(calendar_id)
            if source is None:
                raise ValueError("Choose one of the calendars calendar_list_calendars returns")
        else:
            writable = [s for s in sources.values() if not self._client(s).is_readonly()]
            if not writable:
                raise ValueError("There is no calendar CLIVE can write to")
            source = writable[0]
        client = self._client(source)
        component = self.ICalGLib.Component.new_vevent()
        component.set_uid(f"{uuid.uuid4().hex}@desktop-forge")
        self._apply(component, fields)
        ok, uid = client.create_object_sync(component, self.ECal.OperationFlags.NONE, None)
        if not ok:
            raise RuntimeError("The calendar did not accept the new event")
        return f"eds:{source.get_uid()}:{uid}:"

    def _apply(self, component, fields):
        if "summary" in fields:
            component.set_summary(fields["summary"])
        if "location" in fields:
            component.set_location(fields["location"])
        if "description" in fields:
            component.set_description(fields["description"])
        if "start" in fields:
            component.set_dtstart(self._time(fields["start"]))
        if "end" in fields:
            component.set_dtend(self._time(fields["end"]))

    def modify(self, event_id, fields):
        client, uid, rid = self._locate(event_id)
        ok, component = client.get_object_sync(uid, rid, None)
        if not ok or component is None:
            raise ValueError("That event no longer exists")
        self._apply(component, fields)
        client.modify_object_sync(component, self.ECal.ObjModType.THIS, self.ECal.OperationFlags.NONE, None)
        return event_id

    def describe(self, event_id):
        """Title and start of an event, for the confirmation card."""
        client, uid, rid = self._locate(event_id)
        ok, component = client.get_object_sync(uid, rid, None)
        if not ok or component is None:
            return event_id
        start = component.get_dtstart()
        return f"{component.get_summary() or '(untitled)'} — {start.as_ical_string() if start else ''}"

    def remove(self, event_id):
        client, uid, rid = self._locate(event_id)
        client.remove_object_sync(uid, rid, self.ECal.ObjModType.THIS, self.ECal.OperationFlags.NONE, None)


backend_factory = EdsBackend
_backend = None


def backend():
    global _backend
    if _backend is None:
        _backend = backend_factory()
    return _backend


def _thunderbird(start, end):
    from ...providers import thunderbird_calendar
    try:
        events, _calendars, _problems, _dirs = thunderbird_calendar.fetch_events(start, end)
    except (OSError, ValueError):
        return []
    for index, event in enumerate(events):
        event["id"] = f"tb:{index}:{event.get('summary', '')[:40]}"
        event["writable"] = False
    return events


def _status():
    try:
        calendars = backend().calendars()
    except Exception:  # noqa: BLE001
        return {"state": "unavailable", "detail": "The GNOME calendar service is not reachable"}
    writable = sum(1 for c in calendars if c["writable"])
    return {"state": "ready", "detail": f"{len(calendars)} calendars, {writable} CLIVE can change. "
                                        "Thunderbird calendars are read-only."}


def integration() -> Integration:
    return Integration(
        ID, "Calendar", "productivity", "x-office-calendar-symbolic",
        "GNOME Calendar and online-account calendars; Thunderbird's calendars read-only.",
        CAPABILITIES, default_enabled=True, status=_status,
        guards=Guards(desktop_ids=("org.gnome.Calendar",), categories=("Calendar",),
                      hosts=("calendar.google.com",), title_markers=("Google Calendar",)))


def _range(a):
    start = parse_time(a["start"]) if a.get("start") else datetime.datetime.now().astimezone()
    end = parse_time(a["end"]) if a.get("end") else start + datetime.timedelta(days=14)
    if end <= start:
        raise ValueError("The end of the range must be after its start")
    if end - start > datetime.timedelta(days=366):
        raise ValueError("Search at most a year at a time")
    return start, end


def _events(_ctx, a):
    start, end = _range(a)
    query = (a.get("query") or "").strip()
    events = backend().events(start, end, query)
    for event in _thunderbird(start, end):
        if not query or query.casefold() in f"{event.get('summary', '')} {event.get('location', '')}".casefold():
            events.append(event)
    events.sort(key=lambda e: e.get("start") or "")
    return {"events": events[:MAX_EVENTS], "truncated": len(events) > MAX_EVENTS}


def _calendars(_ctx, _a):
    return {"calendars": backend().calendars()}


FIELDS = ("summary", "start", "end", "location", "description")


def _create(_ctx, a):
    fields = {k: a[k] for k in FIELDS if k in a}
    if "end" not in fields:
        start = a["start"]
        fields["end"] = start if is_date(start) else (parse_time(start) + datetime.timedelta(hours=1)).isoformat()
    return {"created": backend().create(a.get("calendar", ""), fields)}


def _update(_ctx, a):
    fields = {k: a[k] for k in FIELDS if k in a}
    if not fields:
        raise ValueError("Say what to change")
    return {"updated": backend().modify(a["event_id"], fields)}


def _cancel(_ctx, a):
    backend().remove(a["event_id"])
    return {"cancelled": a["event_id"]}


def _update_capability(a):
    """Moving an event in time is its own permission; anything else is a change."""
    changed = {k for k in FIELDS if k in a}
    return "reschedule" if changed and changed <= {"start", "end"} else "modify"


TIME = {"type": "string", "minLength": 10, "maxLength": 40}

TOOLS = (
    Tool("calendar_list_calendars", "List calendars and whether CLIVE can change them.", {}, "view",
         _calendars, ID, "Reading Calendar…"),
    Tool("calendar_events", "List events between start and end (ISO date-times; default the next 14 "
         "days), optionally matching a query. Each event has an id for updates.",
         {"start": TIME, "end": TIME, "query": STRING},
         lambda a: "search" if a.get("query") else "view", _events, ID, "Searching Calendar…",
         required=[], describe=lambda a: a.get("query") or "upcoming events", possible=("view", "search")),
    Tool("calendar_create", "Create an event. start/end are ISO date-times, or YYYY-MM-DD for all-day.",
         {"summary": STRING, "start": TIME, "end": TIME, "location": STRING, "description": TEXT,
          "calendar": STRING}, "create", _create, ID, "Adding to Calendar…",
         required=["summary", "start"], describe=lambda a: f"{a['summary']} at {a['start']}"),
    Tool("calendar_update", "Change an event by id: new start/end reschedules it; other fields edit it.",
         {"event_id": STRING, "summary": STRING, "start": TIME, "end": TIME, "location": STRING,
          "description": TEXT}, _update_capability, _update, ID, "Updating Calendar…",
         required=["event_id"], possible=("modify", "reschedule"),
         describe=lambda a: ", ".join(f"{k} → {a[k]}" for k in FIELDS if k in a)),
    Tool("calendar_cancel", "Cancel (remove) an event by id.", {"event_id": STRING}, "cancel", _cancel, ID,
         "Cancelling an event…", describe=lambda a: a["event_id"],
         preview=lambda a: backend().describe(a["event_id"])),
)
