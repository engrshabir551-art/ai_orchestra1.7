"""Time zone handling. All times are stored as UTC epoch seconds and shown in one chosen time zone (default Australia/Sydney)."""
import datetime as dt

from . import db

COMMON = ["Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide", "Australia/Perth", "Australia/Darwin",
          "Australia/Hobart", "Pacific/Auckland", "Asia/Karachi", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York", "UTC"]


def tz():
    name = db.get_setting("timezone", "Australia/Sydney")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:  # missing tz database: fall back to UTC rather than crash
        return dt.timezone.utc, "UTC"


def fmt(t, seconds=False):
    """'2026-11-08 15:49 AEDT' style text, or '' when there is no time."""
    if not t:
        return ""
    z, _ = tz()
    d = dt.datetime.fromtimestamp(t, z)
    return d.strftime("%Y-%m-%d %H:%M" + (":%S" if seconds else "")) + " " + (d.tzname() or "")


def to_epoch(date, time_):
    """Combine a date and a time typed in the chosen time zone into epoch seconds."""
    z, _ = tz()
    return dt.datetime.combine(date, time_).replace(tzinfo=z).timestamp()


def duration(seconds):
    """Human text: '45 s', '12 min', '3 h 5 min', '2 days 4 h'. Empty when unknown."""
    if seconds is None:
        return ""
    s = int(max(0, seconds))
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(h, 24)
    return f"{d} day{'s' if d != 1 else ''}" + (f" {h} h" if h else "")
