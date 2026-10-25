"""Past hangs, crashes and blue screens from the Windows event logs (pure).

The collector runs ``wevtutil qe <log> /q:<xpath> /f:xml``. This module
builds those queries, parses the XML, counts each kind of problem, and
decides whether the logs go back far enough to mean anything: after a
fresh reinstall (or when someone has cleared them) "no problems" would
only mean "no history".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .. import config

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

# (provider, event id) -> kind of problem.
EVENT_KINDS = {
    ("Application Hang", 1002): "hang",
    ("Application Error", 1000): "crash",
    ("Microsoft-Windows-Kernel-Power", 41): "unexpected_shutdown",
    ("Microsoft-Windows-WER-SystemErrorReporting", 1001): "bsod",
    ("disk", 7): "disk_error",           # bad block
    ("disk", 51): "disk_error",          # error during paging
    ("disk", 153): "disk_error",         # I/O had to be retried
}
KINDS = ("hang", "crash", "unexpected_shutdown", "bsod", "disk_error")
# The event ids to ask each log for; providers are checked after parsing
# (other providers reuse some of these ids).
LOG_EVENT_IDS = {"Application": (1000, 1002), "System": (7, 41, 51, 153, 1001)}

_TIME_RE = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")
_XML_DECL_RE = re.compile(r"<\?xml[^>]*\?>")


def xpath(event_ids: Iterable[int], days: int) -> str:
    """A wevtutil query for ``event_ids`` logged in the last ``days``."""
    ids = " or ".join(f"EventID={i}" for i in event_ids)
    return (f"*[System[({ids}) and "
            f"TimeCreated[timediff(@SystemTime) <= {days * 86_400_000}]]]")


def parse_time(text: object) -> Optional[datetime]:
    """An event's ``SystemTime`` ("2026-10-20T08:15:30.1234567Z") in UTC."""
    match = _TIME_RE.match(str(text or ""))
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


def parse_events(xml_text: str) -> List[Dict[str, object]]:
    """wevtutil's ``/f:xml`` output (one ``<Event>`` per record, no root
    element) -> ``[{provider, id, time, app}]``; [] when unreadable."""
    body = _XML_DECL_RE.sub("", xml_text or "").strip()
    if not body:
        return []
    try:
        root = ET.fromstring(f"<Events>{body}</Events>")
    except ET.ParseError:
        return []
    events = []
    for event in root.iter(f"{NS}Event"):
        system = event.find(f"{NS}System")
        if system is None:
            continue
        provider = system.find(f"{NS}Provider")
        created = system.find(f"{NS}TimeCreated")
        try:
            event_id = int((system.findtext(f"{NS}EventID") or "").strip())
        except ValueError:
            continue
        data = event.findall(f"{NS}EventData/{NS}Data")
        named = [d for d in data if d.get("Name") == "AppName"]
        app = (named or data or [None])[0]
        app_name = (app.text or "").strip() if app is not None else ""
        events.append({
            "provider": provider.get("Name") if provider is not None else None,
            "id": event_id,
            "time": parse_time(created.get("SystemTime")
                               if created is not None else None),
            "app": app_name or None,
        })
    return events


def classify(event: Dict[str, object]) -> Optional[str]:
    """The kind of problem an event records, or None."""
    return EVENT_KINDS.get((event.get("provider"), event.get("id")))


def summarise(events: Iterable[Dict[str, object]], now: datetime,
              days: Optional[int] = None) -> Dict[str, object]:
    """Counts per kind over the last ``days``, plus the programs that hung
    or crashed most often (names only)."""
    days = days or config.PERF_HISTORY_DAYS
    counts = {kind: 0 for kind in KINDS}
    apps: Dict[str, Counter] = {"hang": Counter(), "crash": Counter()}
    for event in events:
        kind = classify(event)
        when = event.get("time")
        if kind is None or (isinstance(when, datetime)
                            and (now - when).days >= days):
            continue
        counts[kind] += 1
        if kind in apps and event.get("app"):
            apps[kind][str(event["app"])] += 1
    return {"counts": counts,
            "top_apps": {k: [name for name, _ in c.most_common(3)]
                         for k, c in apps.items()}}


def history_status(install_date: Optional[datetime],
                   oldest_event: Optional[datetime], now: datetime,
                   logs_read: bool = True) -> Dict[str, object]:
    """Whether the event logs can vouch for the laptop's past.

    History is only "not available" when the logs themselves are young.
    A recent install date alone can be a feature update, which keeps the
    logs.
    """
    min_days = config.PERF_REINSTALL_DAYS
    if not logs_read:
        return {"available": False, "days": None,
                "reason": "history not available — the event logs could "
                          "not be read"}
    covered = (now - oldest_event).days if oldest_event else 0
    installed = (now - install_date).days if install_date else None
    if covered < min_days:
        if installed is not None and installed < min_days:
            reason = ("history not available — Windows was reinstalled "
                      "recently")
        else:
            reason = (f"history not available — the event logs only go back "
                      f"{covered} day{'s' if covered != 1 else ''} (they may "
                      "have been cleared)")
        return {"available": False, "days": covered, "reason": reason}
    return {"available": True, "days": min(covered, config.PERF_HISTORY_DAYS),
            "reason": None}
