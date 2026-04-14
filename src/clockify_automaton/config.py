import json
import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VALID_DAY_NAMES = frozenset(
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
)


@dataclass
class ProjectConfig:
    name: str
    weight: float


@dataclass
class WorkingDays:
    days: frozenset
    start_time: datetime.time
    end_time: datetime.time
    worked_hours: datetime.timedelta  # total worked time per day (excl. lunch)


@dataclass
class Config:
    api_key: str
    date_range_start: datetime.date
    date_range_end: datetime.date
    working_days: WorkingDays
    off_days: frozenset
    projects: list
    timezone: ZoneInfo


def _parse_date(value: str, field: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid date in {field!r}: {value!r} (expected YYYY-MM-DD)")


def _parse_duration(value: str, field: str) -> datetime.timedelta:
    """Parse a duration string like '7:30' or '8:00' into a timedelta."""
    try:
        parts = str(value).split(":")
        if len(parts) != 2:
            raise ValueError
        hours, minutes = int(parts[0]), int(parts[1])
        if hours < 0 or not (0 <= minutes < 60):
            raise ValueError
        return datetime.timedelta(hours=hours, minutes=minutes)
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid duration in {field!r}: {value!r} (expected H:MM or HH:MM)")


def _parse_time(value: str, field: str) -> datetime.time:
    try:
        return datetime.time.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid time in {field!r}: {value!r} (expected HH:MM or HH:MM:SS)")


def _expand_off_days(off_days_raw: list) -> frozenset:
    dates = set()
    for i, entry in enumerate(off_days_raw):
        if isinstance(entry, str):
            dates.add(_parse_date(entry, f"off_days[{i}]"))
        elif isinstance(entry, dict):
            if "from" not in entry or "to" not in entry:
                raise ValueError(f"off_days[{i}] range must have both 'from' and 'to' keys")
            start = _parse_date(entry["from"], f"off_days[{i}].from")
            end = _parse_date(entry["to"], f"off_days[{i}].to")
            if start > end:
                raise ValueError(f"off_days[{i}]: 'from' ({start}) must be <= 'to' ({end})")
            current = start
            while current <= end:
                dates.add(current)
                current += datetime.timedelta(days=1)
        else:
            raise ValueError(
                f"off_days[{i}] must be a date string or a {{\"from\": ..., \"to\": ...}} object"
            )
    return frozenset(dates)


def load_config(path: str) -> Config:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path!r}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")

    if not isinstance(data, dict):
        raise ValueError("Config file must be a JSON object")

    # api_key
    if "api_key" not in data:
        raise ValueError("Missing required field: 'api_key'")
    api_key = str(data["api_key"]).strip()
    if not api_key:
        raise ValueError("'api_key' must not be empty")

    # date_range
    if "date_range" not in data:
        raise ValueError("Missing required field: 'date_range'")
    dr = data["date_range"]
    if not isinstance(dr, dict) or "from" not in dr or "to" not in dr:
        raise ValueError("'date_range' must be an object with 'from' and 'to' keys")
    date_start = _parse_date(dr["from"], "date_range.from")
    date_end = _parse_date(dr["to"], "date_range.to")
    if date_start > date_end:
        raise ValueError(
            f"'date_range.from' ({date_start}) must be <= 'date_range.to' ({date_end})"
        )

    # working_days
    if "working_days" not in data:
        raise ValueError("Missing required field: 'working_days'")
    wd = data["working_days"]
    if not isinstance(wd, dict):
        raise ValueError("'working_days' must be an object")
    for key in ("days", "start_time", "end_time"):
        if key not in wd:
            raise ValueError(f"'working_days.{key}' is required")

    if not isinstance(wd["days"], list):
        raise ValueError("'working_days.days' must be a list")
    days = frozenset(d.lower() for d in wd["days"])
    invalid_days = days - VALID_DAY_NAMES
    if invalid_days:
        raise ValueError(f"Invalid day names in 'working_days.days': {sorted(invalid_days)}")
    if not days:
        raise ValueError("'working_days.days' must not be empty")

    start_time = _parse_time(wd["start_time"], "working_days.start_time")
    end_time = _parse_time(wd["end_time"], "working_days.end_time")
    if start_time >= end_time:
        raise ValueError(
            f"'working_days.start_time' ({start_time}) must be before 'working_days.end_time' ({end_time})"
        )

    full_span = datetime.timedelta(
        hours=end_time.hour - start_time.hour,
        minutes=end_time.minute - start_time.minute,
        seconds=end_time.second - start_time.second,
    )
    if "number_of_worked_hours" in wd:
        worked_hours = _parse_duration(
            wd["number_of_worked_hours"], "working_days.number_of_worked_hours"
        )
        if worked_hours <= datetime.timedelta(0):
            raise ValueError("'working_days.number_of_worked_hours' must be positive")
        if worked_hours > full_span:
            raise ValueError(
                f"'working_days.number_of_worked_hours' ({worked_hours}) "
                f"cannot exceed the span from start_time to end_time ({full_span})"
            )
    else:
        worked_hours = full_span  # no lunch break

    working_days = WorkingDays(
        days=days, start_time=start_time, end_time=end_time, worked_hours=worked_hours
    )

    # off_days (optional)
    off_days_raw = data.get("off_days", [])
    if not isinstance(off_days_raw, list):
        raise ValueError("'off_days' must be a list")
    off_days = _expand_off_days(off_days_raw)

    # projects
    if "projects" not in data:
        raise ValueError("Missing required field: 'projects'")
    if not isinstance(data["projects"], list) or len(data["projects"]) == 0:
        raise ValueError("'projects' must be a non-empty list")

    projects = []
    for i, p in enumerate(data["projects"]):
        if not isinstance(p, dict):
            raise ValueError(f"projects[{i}] must be an object")
        if "name" not in p:
            raise ValueError(f"projects[{i}] is missing 'name'")
        if "weight" not in p:
            raise ValueError(f"projects[{i}] is missing 'weight'")
        name = str(p["name"]).strip()
        if not name:
            raise ValueError(f"projects[{i}] 'name' must not be empty")
        try:
            weight = float(p["weight"])
        except (TypeError, ValueError):
            raise ValueError(f"projects[{i}] 'weight' must be a number, got {p['weight']!r}")
        if weight <= 0:
            raise ValueError(f"projects[{i}] 'weight' must be positive, got {weight}")
        projects.append(ProjectConfig(name=name, weight=weight))

    # timezone (optional, default UTC)
    tz_str = data.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError(f"Unknown timezone: {tz_str!r}")

    return Config(
        api_key=api_key,
        date_range_start=date_start,
        date_range_end=date_end,
        working_days=working_days,
        off_days=off_days,
        projects=projects,
        timezone=tz,
    )
