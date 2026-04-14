import datetime
import sys

from .clockify_client import ClockifyClient, ClockifyError
from .config import Config

NOT_WORKING_PROJECT = "Not working"
_LUNCH_START = datetime.time(12, 0, 0)

_DAY_NAMES = {
    0: "monday",
    1: "tuesday",
    2: "wednesday",
    3: "thursday",
    4: "friday",
    5: "saturday",
    6: "sunday",
}


def _compute_slots(
    date: datetime.date,
    config: Config,
    projects: list,  # list of (project_id, weight)
) -> list:
    """
    Distribute worked hours across projects proportionally by weight, respecting
    the lunch break. Uses the largest-remainder method so allocated minutes always
    sum to the exact worked duration with no drift.

    The day is split into two real blocks (morning and afternoon) separated by a
    lunch break that starts at 12:00. A project slice that straddles 12:00 is
    automatically emitted as two separate entries.

    Returns a list of (project_id, start_datetime, end_datetime).
    """
    tz = config.timezone
    day_start = datetime.datetime(
        date.year, date.month, date.day,
        config.working_days.start_time.hour,
        config.working_days.start_time.minute,
        config.working_days.start_time.second,
        tzinfo=tz,
    )
    day_end = datetime.datetime(
        date.year, date.month, date.day,
        config.working_days.end_time.hour,
        config.working_days.end_time.minute,
        config.working_days.end_time.second,
        tzinfo=tz,
    )
    worked_minutes = int(config.working_days.worked_hours.total_seconds() // 60)
    total_span_minutes = int((day_end - day_start).total_seconds() // 60)
    lunch_minutes = total_span_minutes - worked_minutes

    # Determine the two real time blocks (morning / afternoon)
    lunch_start_dt = datetime.datetime(
        date.year, date.month, date.day,
        _LUNCH_START.hour, _LUNCH_START.minute, _LUNCH_START.second,
        tzinfo=tz,
    )
    if lunch_minutes > 0 and day_start < lunch_start_dt < day_end:
        morning_minutes = int((lunch_start_dt - day_start).total_seconds() // 60)
        afternoon_start_dt = lunch_start_dt + datetime.timedelta(minutes=lunch_minutes)
    else:
        # No lunch break: one contiguous block
        morning_minutes = worked_minutes
        afternoon_start_dt = None

    # Distribute worked_minutes across projects using largest-remainder
    total_weight = sum(w for _, w in projects)
    exact = [worked_minutes * w / total_weight for _, w in projects]
    floored = [int(m) for m in exact]
    remainders = [exact[i] - floored[i] for i in range(len(projects))]
    leftover = worked_minutes - sum(floored)
    indices_by_remainder = sorted(
        range(len(projects)), key=lambda i: remainders[i], reverse=True
    )
    for i in range(leftover):
        floored[indices_by_remainder[i]] += 1

    # Map virtual timeline [0, worked_minutes) onto real time blocks
    slots = []
    virtual_cursor = 0
    for i, (pid, _) in enumerate(projects):
        v_start = virtual_cursor
        v_end = virtual_cursor + floored[i]
        virtual_cursor = v_end

        if floored[i] == 0:
            continue

        if afternoon_start_dt is None or v_end <= morning_minutes:
            # Entirely in the morning block
            slots.append((
                pid,
                day_start + datetime.timedelta(minutes=v_start),
                day_start + datetime.timedelta(minutes=v_end),
            ))
        elif v_start >= morning_minutes:
            # Entirely in the afternoon block
            pm_offset = v_start - morning_minutes
            slots.append((
                pid,
                afternoon_start_dt + datetime.timedelta(minutes=pm_offset),
                afternoon_start_dt + datetime.timedelta(minutes=pm_offset + floored[i]),
            ))
        else:
            # Straddles the lunch break — emit two entries
            slots.append((
                pid,
                day_start + datetime.timedelta(minutes=v_start),
                lunch_start_dt,
            ))
            pm_minutes = v_end - morning_minutes
            slots.append((
                pid,
                afternoon_start_dt,
                afternoon_start_dt + datetime.timedelta(minutes=pm_minutes),
            ))

    return slots


def run(config: Config) -> None:
    client = ClockifyClient(config.api_key)

    print("Connecting to Clockify...")
    try:
        wid = client.get_workspace_id()
    except ClockifyError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Workspace: {wid}")

    # Resolve all project names → IDs upfront (fail fast on unknown projects)
    print("Resolving projects...")
    project_ids: dict[str, str] = {}
    # Resolve the "Not working" project first
    try:
        not_working_id = client.resolve_project_id(NOT_WORKING_PROJECT)
        print(f"  ✓ {NOT_WORKING_PROJECT!r}")
    except ValueError as e:
        print(f"  ✗ {e}")
        sys.exit(1)
    except ClockifyError as e:
        print(f"  ✗ API error: {e}")
        sys.exit(1)
    for p in config.projects:
        try:
            pid = client.resolve_project_id(p.name)
            project_ids[p.name] = pid
            print(f"  ✓ {p.name!r}")
        except ValueError as e:
            print(f"  ✗ {e}")
            sys.exit(1)
        except ClockifyError as e:
            print(f"  ✗ API error: {e}")
            sys.exit(1)

    # Main loop
    stats = {"days_filled": 0, "entries_created": 0, "days_skipped": 0, "days_off": 0}
    current_date = config.date_range_start

    print(f"\nFilling timesheets from {config.date_range_start} to {config.date_range_end}...\n")

    while current_date <= config.date_range_end:
        day_name = _DAY_NAMES[current_date.weekday()]

        # Skip non-working days of the week silently
        if day_name not in config.working_days.days:
            current_date += datetime.timedelta(days=1)
            continue

        # Off days: fill with a single "Not working" entry spanning the full working day
        if current_date in config.off_days:
            try:
                existing_entries = client.get_entries_for_day(current_date, config.timezone)
            except ClockifyError as e:
                print(f"  {current_date} ({day_name[:3].title()}) — error fetching entries: {e}")
                current_date += datetime.timedelta(days=1)
                continue

            existing_project_ids = {
                e.get("projectId") for e in existing_entries if e.get("projectId")
            }
            if not_working_id in existing_project_ids:
                print(
                    f"  {current_date} ({day_name[:3].title()}) "
                    f"— off day, already filled, skipped"
                )
            else:
                tz = config.timezone
                day_start = datetime.datetime(
                    current_date.year, current_date.month, current_date.day,
                    config.working_days.start_time.hour,
                    config.working_days.start_time.minute,
                    config.working_days.start_time.second,
                    tzinfo=tz,
                )
                day_end = datetime.datetime(
                    current_date.year, current_date.month, current_date.day,
                    config.working_days.end_time.hour,
                    config.working_days.end_time.minute,
                    config.working_days.end_time.second,
                    tzinfo=tz,
                )
                try:
                    client.create_entry(not_working_id, day_start, day_end)
                    print(
                        f"  {current_date} ({day_name[:3].title()}) "
                        f"— off day, filled with {NOT_WORKING_PROJECT!r}"
                    )
                    stats["entries_created"] += 1
                except ClockifyError as e:
                    print(
                        f"  {current_date} ({day_name[:3].title()}) "
                        f"— error creating off-day entry: {e}"
                    )
            stats["days_off"] += 1
            current_date += datetime.timedelta(days=1)
            continue

        # Fetch existing entries to detect conflicts
        try:
            existing_entries = client.get_entries_for_day(current_date, config.timezone)
        except ClockifyError as e:
            print(f"  {current_date} ({day_name[:3].title()}) — error fetching entries: {e}")
            current_date += datetime.timedelta(days=1)
            continue

        existing_project_ids = {
            e.get("projectId") for e in existing_entries if e.get("projectId")
        }

        projects_to_fill = []
        skipped_projects = []
        for p in config.projects:
            pid = project_ids[p.name]
            if pid in existing_project_ids:
                skipped_projects.append(p.name)
            else:
                projects_to_fill.append((pid, p.weight))

        if not projects_to_fill:
            print(
                f"  {current_date} ({day_name[:3].title()}) "
                f"— all projects already filled, skipped"
            )
            stats["days_skipped"] += 1
            current_date += datetime.timedelta(days=1)
            continue

        # Create entries
        slots = _compute_slots(current_date, config, projects_to_fill)
        created = 0
        error_occurred = False
        for pid, slot_start, slot_end in slots:
            try:
                client.create_entry(pid, slot_start, slot_end)
                created += 1
            except ClockifyError as e:
                print(f"  {current_date} ({day_name[:3].title()}) — error creating entry: {e}")
                error_occurred = True

        if not error_occurred:
            skip_note = (
                f" (skipped existing: {', '.join(skipped_projects)})"
                if skipped_projects
                else ""
            )
            print(
                f"  {current_date} ({day_name[:3].title()}) "
                f"— {created} entr{'y' if created == 1 else 'ies'} created{skip_note}"
            )
            stats["days_filled"] += 1
            stats["entries_created"] += created

        current_date += datetime.timedelta(days=1)

    print(f"\n{'─' * 48}")
    print(f"Done!")
    print(f"  Days filled:   {stats['days_filled']}")
    print(f"  Entries created: {stats['entries_created']}")
    print(f"  Days skipped:  {stats['days_skipped']}  (already had entries)")
    print(f"  Off days:      {stats['days_off']}")
