"""Derive readable order numbers from saved dates without migrating order IDs."""
from datetime import date, datetime, timedelta, timezone
import re


def _day(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _created_day(audit_log):
    dates = []
    for event in audit_log:
        if event.get("action") != "create_case":
            continue
        try:
            stamp = datetime.fromisoformat(event.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone(timedelta(hours=8)))
        dates.append(stamp.date())
    return min(dates, default=None)


def _number(suffix, created, start, end=None):
    created_part = created.strftime("%Y%m%d") if created else "待定"
    execution_part = start.strftime("%Y%m%d") if start else "待定"
    if start and end and end > start:
        execution_part += "~" + end.strftime("%Y%m%d")
    return f"{created_part}-{execution_part}-{suffix}"


def case_number(snapshot):
    req = snapshot.get("requirements") or {}
    date_parts = re.split(r"\s*[~～至到—]\s*", (req.get("event_date") or "").strip(), maxsplit=1)
    start = _day(date_parts[0])
    end = _day(date_parts[1]) if len(date_parts) == 2 else None
    if start and end is None and len(date_parts) == 1:
        duration = req.get("duration") or ""
        days = re.match(r"\s*(\d{1,3})\s*天", duration)
        nights = re.match(r"\s*(\d{1,3})\s*晚", duration)
        offset = max(0, int(days[1]) - 1) if days else int(nights[1]) if nights else 0
        if offset <= (date.max - start).days:
            end = start + timedelta(days=offset)
    return _number(snapshot["id"].removeprefix("case_"), _created_day(snapshot.get("audit_log", [])), start, end)


def supplier_number(order_id, check_in, check_out):
    # Legacy supplier orders have a simulation day, which records the service date.
    return _number(order_id.removeprefix("SIM-ORDER-"), None, _day(check_in), _day(check_out))
