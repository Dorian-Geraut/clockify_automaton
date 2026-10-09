from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass, field

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


@dataclass
class Slot:
    """One time entry to create."""
    project_id: str
    start: datetime.datetime
    end: datetime.datetime
    description: str = ""
    task_id: str | None = None
    tag_ids: list = field(default_factory=list)
    billable: bool | None = None


@dataclass
class TemplateEntry:
    """One entry of a Clockify timesheet template, relative to its weekday."""
    weekday: int                 # 0 = Monday ... 6 = Sunday
    start_time: datetime.time    # wall-clock start in the template's timezone
    duration: datetime.timedelta
    project_id: str
    task_id: str | None
    description: str
    tag_ids: list
    billable: bool | None


def _parse_template(template: dict) -> list:
    """
    Turn a Clockify template (as returned by GET /templates) into weekday-relative
    entries. The template stores real entries from the week it was saved from; only
    the weekday, wall-clock start time and duration are kept.
    """
    entries = []
    for e in template.get("entries", []):
        ti = e["timeInterval"]
        zoned_start = datetime.datetime.fromisoformat(ti["zonedStart"])
        utc_start = datetime.datetime.fromisoformat(ti["start"].replace("Z", "+00:00"))
        utc_end = datetime.datetime.fromisoformat(ti["end"].replace("Z", "+00:00"))
        entries.append(TemplateEntry(
            weekday=zoned_start.weekday(),
            start_time=zoned_start.time(),
            duration=utc_end - utc_start,
            project_id=e["projectId"],
            task_id=e.get("taskId"),
            description=e.get("description") or "",
            tag_ids=list(e.get("tagIds") or []),
            billable=e.get("billable"),
        ))
    entries.sort(key=lambda t: (t.weekday, t.start_time))
    return entries


def _fmt_duration(td: datetime.timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


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

    Returns a list of Slot.
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
    total_span_minutes = int((day_end - day_start).total_seconds() // 60)  # wall-clock span, lunch included
    lunch_minutes = total_span_minutes - worked_minutes  # derived lunch duration

    # Determine the two real time blocks (morning / afternoon)
    lunch_start_dt = datetime.datetime(
        date.year, date.month, date.day,
        _LUNCH_START.hour, _LUNCH_START.minute, _LUNCH_START.second,
        tzinfo=tz,
    )
    if lunch_minutes > 0 and day_start < lunch_start_dt < day_end:
        morning_minutes = int((lunch_start_dt - day_start).total_seconds() // 60)  # worked minutes available before noon
        afternoon_start_dt = lunch_start_dt + datetime.timedelta(minutes=lunch_minutes)  # when work resumes after lunch
    else:
        # No lunch break: one contiguous block
        morning_minutes = worked_minutes
        afternoon_start_dt = None  # signals "no lunch break" to the slot-mapping logic below

    # Distribute worked_minutes across projects using largest-remainder
    total_weight = sum(w for _, w in projects)
    exact = [worked_minutes * w / total_weight for _, w in projects]   # ideal fractional allocation per project
    floored = [int(m) for m in exact]                                   # rounded-down integer allocation
    remainders = [exact[i] - floored[i] for i in range(len(projects))] # fractional parts discarded by flooring
    leftover = worked_minutes - sum(floored)                            # minutes lost to rounding, must be redistributed
    indices_by_remainder = sorted(                                      # projects ranked: largest remainder gets an extra minute first
        range(len(projects)), key=lambda i: remainders[i], reverse=True
    )
    for i in range(leftover):
        floored[indices_by_remainder[i]] += 1

    # Map virtual timeline [0, worked_minutes) onto real time blocks
    slots = []
    virtual_cursor = 0  # current position in the virtual worked timeline
    for i, (pid, _) in enumerate(projects):
        v_start = virtual_cursor           # virtual start of this project's slice
        v_end = virtual_cursor + floored[i]  # virtual end of this project's slice
        virtual_cursor = v_end

        if floored[i] == 0:
            continue

        if afternoon_start_dt is None or v_end <= morning_minutes:
            # Entirely in the morning block
            slots.append(Slot(
                pid,
                day_start + datetime.timedelta(minutes=v_start),
                day_start + datetime.timedelta(minutes=v_end),
            ))
        elif v_start >= morning_minutes:
            # Entirely in the afternoon block
            pm_offset = v_start - morning_minutes  # distance from afternoon_start_dt
            slots.append(Slot(
                pid,
                afternoon_start_dt + datetime.timedelta(minutes=pm_offset),
                afternoon_start_dt + datetime.timedelta(minutes=pm_offset + floored[i]),
            ))
        else:
            # Straddles the lunch break — emit two entries
            slots.append(Slot(
                pid,
                day_start + datetime.timedelta(minutes=v_start),
                lunch_start_dt,
            ))
            pm_minutes = v_end - morning_minutes  # portion of this project's time that falls in the afternoon
            slots.append(Slot(
                pid,
                afternoon_start_dt,
                afternoon_start_dt + datetime.timedelta(minutes=pm_minutes),
            ))

    return slots


def _template_slots(date: datetime.date, config: Config, entries: list) -> list:
    """Instantiate weekday-relative template entries on a concrete date."""
    tz = config.timezone
    slots = []
    for t in entries:
        start = datetime.datetime(
            date.year, date.month, date.day,
            t.start_time.hour, t.start_time.minute, t.start_time.second,
            tzinfo=tz,
        )
        slots.append(Slot(
            project_id=t.project_id,
            start=start,
            end=start + t.duration,
            description=t.description,
            task_id=t.task_id,
            tag_ids=t.tag_ids,
            billable=t.billable,
        ))
    return slots


def _fill_slots(
    client: ClockifyClient,
    slots: list,
    date: datetime.date,
    day_name: str,
    error_label: str,
    dry_run: bool,
) -> tuple[int, bool]:
    created = 0
    error_occurred = False
    for slot in slots:
        if dry_run:
            print(
                f"  {date} ({day_name[:3].title()}) — would create "
                f"{slot.start.strftime('%H:%M')}–{slot.end.strftime('%H:%M')} "
                f"({_fmt_duration(slot.end - slot.start)}) {client.get_project_name(slot.project_id)!r}"
            )
            created += 1
            continue
        try:
            client.create_entry(
                slot.project_id, slot.start, slot.end,
                description=slot.description, task_id=slot.task_id,
                tag_ids=slot.tag_ids, billable=slot.billable,
            )
            created += 1
        except ClockifyError as e:
            print(f"  {date} ({day_name[:3].title()}) — {error_label}: {e}")
            error_occurred = True
    return created, error_occurred


def run(config: Config, dry_run: bool = False) -> None:
    client = ClockifyClient(config.api_key)

    if dry_run:
        print("DRY RUN — nothing will be written to Clockify\n")
    print("Connecting to Clockify...")
    try:
        wid = client.get_workspace_id()
    except ClockifyError as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"Workspace: {wid}")

    # Resolve all project names → IDs upfront (fail fast on unknown projects)
    print("Resolving projects...")
    project_ids: dict[str, str] = {}  # project name → Clockify project ID
    # Resolve the "Not working" project first
    try:
        not_working_id = client.resolve_project_id(NOT_WORKING_PROJECT)  # Clockify ID for the off-day project
        print(f"  ✓ {NOT_WORKING_PROJECT!r}")
    except ValueError as e:
        print(f"  ✗ {e}")
        sys.exit(1)
    except ClockifyError as e:
        print(f"  ✗ API error: {e}")
        sys.exit(1)
    all_project_names = sorted({p.name for dr in config.date_ranges for p in dr.projects})  # deduplicated across all periods
    for name in all_project_names:
        try:
            pid = client.resolve_project_id(name)
            project_ids[name] = pid
            print(f"  ✓ {name!r}")
        except ValueError as e:
            print(f"  ✗ {e}")
            sys.exit(1)
        except ClockifyError as e:
            print(f"  ✗ API error: {e}")
            sys.exit(1)

    # Resolve templates → weekday-relative entries (fail fast on unknown templates)
    template_entries: dict[str, list] = {}  # template name (as written in config) → list of TemplateEntry
    template_names = sorted({dr.template for dr in config.date_ranges if dr.template})
    if template_names:
        print("Resolving templates...")
    for name in template_names:
        try:
            entries = _parse_template(client.resolve_template(name))
        except ValueError as e:
            print(f"  ✗ {e}")
            sys.exit(1)
        except ClockifyError as e:
            print(f"  ✗ API error: {e}")
            sys.exit(1)
        if not entries:
            print(f"  ✗ Template {name!r} has no time entries (save it from the timesheet with time filled in)")
            sys.exit(1)
        template_entries[name] = entries
        weekly = sum((t.duration for t in entries), datetime.timedelta(0))
        print(f"  ✓ {name!r} ({len(entries)} entries, {_fmt_duration(weekly)}/week)")
        for t in entries:
            end_time = (datetime.datetime.combine(datetime.date(2000, 1, 3), t.start_time) + t.duration).time()
            print(
                f"      {_DAY_NAMES[t.weekday][:3].title()} "
                f"{t.start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')} "
                f"({_fmt_duration(t.duration)}) {client.get_project_name(t.project_id)!r}"
            )

    # Main loop
    stats = {"days_filled": 0, "entries_created": 0, "days_skipped": 0, "days_off": 0, "approvals_submitted": 0}
    # weeks (Monday dates) that had at least one entry created, tracked per date range index
    weeks_with_entries: dict[int, set] = {i: set() for i in range(len(config.date_ranges))}
    overall_start = min(dr.start for dr in config.date_ranges)  # earliest date across all periods
    overall_end = max(dr.end for dr in config.date_ranges)      # latest date across all periods
    current_date = overall_start

    print(f"\nFilling timesheets from {overall_start} to {overall_end}...\n")

    while current_date <= overall_end:
        day_name = _DAY_NAMES[current_date.weekday()]

        # Skip days that fall between date ranges
        active_range_entry = next(
            ((i, dr) for i, dr in enumerate(config.date_ranges) if dr.start <= current_date <= dr.end),
            None,
        )
        if active_range_entry is None:
            current_date += datetime.timedelta(days=1)
            continue
        active_range_idx, active_range = active_range_entry  # index used to bucket weeks for approval submission

        is_working_day = day_name in config.working_days.days
        if active_range.template is not None:
            # Template mode: the template decides which weekdays carry entries;
            # off days still follow working_days (hours of the "Not working" entry).
            day_template_entries = [
                t for t in template_entries[active_range.template]
                if t.weekday == current_date.weekday()
            ]
            is_off = is_working_day and current_date in config.off_days
            if not day_template_entries and not is_off:
                current_date += datetime.timedelta(days=1)
                continue
        else:
            day_template_entries = []
            # Skip non-working days of the week silently
            if not is_working_day:
                current_date += datetime.timedelta(days=1)
                continue
            is_off = current_date in config.off_days

        # Fetch existing entries to detect conflicts
        try:
            existing_entries = client.get_entries_for_day(current_date, config.timezone)
        except ClockifyError as e:
            print(f"  {current_date} ({day_name[:3].title()}) — error fetching entries: {e}")
            current_date += datetime.timedelta(days=1)
            continue

        existing_project_ids = {  # set of project IDs that already have an entry today → used to skip conflicts
            e.get("projectId") for e in existing_entries if e.get("projectId")
        }

        if is_off:
            if not_working_id in existing_project_ids:
                print(
                    f"  {current_date} ({day_name[:3].title()}) "
                    f"— off day, already filled, skipped"
                )
            else:
                slots = _compute_slots(current_date, config, [(not_working_id, 1)])
                created, error_occurred = _fill_slots(
                    client, slots, current_date, day_name, "error creating off-day entry", dry_run
                )
                if not error_occurred:
                    print(
                        f"  {current_date} ({day_name[:3].title()}) "
                        f"— off day, {'would be ' if dry_run else ''}filled with {NOT_WORKING_PROJECT!r}"
                    )
                    stats["entries_created"] += created
                if created > 0:
                    week_start = current_date - datetime.timedelta(days=current_date.weekday())
                    weeks_with_entries[active_range_idx].add(week_start)
            stats["days_off"] += 1
        elif active_range.template is not None:
            to_fill = [t for t in day_template_entries if t.project_id not in existing_project_ids]
            skipped_projects = sorted({
                client.get_project_name(t.project_id)
                for t in day_template_entries if t.project_id in existing_project_ids
            })
            if not to_fill:
                print(
                    f"  {current_date} ({day_name[:3].title()}) "
                    f"— all template projects already filled, skipped"
                )
                stats["days_skipped"] += 1
            else:
                slots = _template_slots(current_date, config, to_fill)
                created, error_occurred = _fill_slots(
                    client, slots, current_date, day_name, "error creating entry", dry_run
                )
                if not error_occurred:
                    skip_note = (
                        f" (skipped existing: {', '.join(skipped_projects)})"
                        if skipped_projects
                        else ""
                    )
                    print(
                        f"  {current_date} ({day_name[:3].title()}) "
                        f"— {created} entr{'y' if created == 1 else 'ies'} {'would be ' if dry_run else ''}created from template "
                        f"{active_range.template!r}{skip_note}"
                    )
                    stats["days_filled"] += 1
                    stats["entries_created"] += created
                if created > 0:
                    week_start = current_date - datetime.timedelta(days=current_date.weekday())
                    weeks_with_entries[active_range_idx].add(week_start)
        else:
            projects_to_fill = []   # projects with no entry yet on this day → will be created
            skipped_projects = []   # projects already filled on this day → logged but not touched
            for p in active_range.projects:
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
            else:
                slots = _compute_slots(current_date, config, projects_to_fill)
                created, error_occurred = _fill_slots(
                    client, slots, current_date, day_name, "error creating entry", dry_run
                )
                if not error_occurred:
                    skip_note = (
                        f" (skipped existing: {', '.join(skipped_projects)})"
                        if skipped_projects
                        else ""
                    )
                    print(
                        f"  {current_date} ({day_name[:3].title()}) "
                        f"— {created} entr{'y' if created == 1 else 'ies'} {'would be ' if dry_run else ''}created{skip_note}"
                    )
                    stats["days_filled"] += 1
                    stats["entries_created"] += created
                if created > 0:
                    week_start = current_date - datetime.timedelta(days=current_date.weekday())
                    weeks_with_entries[active_range_idx].add(week_start)

        current_date += datetime.timedelta(days=1)

    # Submit weekly approval requests
    weeks_to_approve = [  # flat ordered list of (range_idx, monday) pairs to submit
        (i, week_start)
        for i in range(len(config.date_ranges))
        for week_start in sorted(weeks_with_entries[i])
    ]
    if weeks_to_approve:
        print(f"\n{'Would submit' if dry_run else 'Submitting'} approvals...")
        for i, week_start in weeks_to_approve:
            if dry_run:
                print(f"  • Week of {week_start}")
                continue
            try:
                client.submit_approval_request(week_start)
                print(f"  ✓ Week of {week_start}")
                stats["approvals_submitted"] += 1
            except ClockifyError as e:
                print(f"  ✗ Week of {week_start} — {e}")

    print(f"\n{'─' * 48}")
    print(f"Done!{' (dry run)' if dry_run else ''}")
    print(f"  Days filled:      {stats['days_filled']}")
    print(f"  Entries created:  {stats['entries_created']}")
    print(f"  Days skipped:     {stats['days_skipped']}  (already had entries)")
    print(f"  Off days:         {stats['days_off']}")
    print(f"  Approvals submitted: {stats['approvals_submitted']}")
