# clockify-automaton

A Python CLI tool that automatically fills [Clockify](https://app.clockify.me/) timesheets by distributing working hours across multiple projects based on configurable weights.

## Features

- Fills working days across one or more date ranges with time entries per project
- Each date range has its own project composition — useful when your project allocation changes mid-period
- Or replays one of your Clockify **timesheet templates** week after week (different projects/hours per weekday)
- Distributes hours proportionally using a weight system
- Handles lunch breaks (blank gap in the calendar)
- Marks off days (holidays, vacation) with a "Not working" project
- Skips days that already have entries (safe to re-run)
- Submits a weekly approval request for every week it filled
- `--dry-run` mode to preview everything before writing
- Retries automatically when Clockify rate-limits a request (HTTP 429)

## Prerequisites

- Python 3.9 or higher
- A [Clockify](https://app.clockify.me/) account and API key
- A project named **"Not working"** in your Clockify workspace (used for off days)

## Installation

```bash
git clone <repo-url>
cd clockify_automaton
bash install.sh
```

The script creates a `.venv/` virtual environment and installs all dependencies.

## Configuration

Copy the example config and edit it:

```bash
cp config.example.json my_config.json
```

| Field | Required | Description |
|---|---|---|
| `api_key` | Yes | Your Clockify API key (found in Profile Settings) |
| `timezone` | No | Your timezone (e.g. `"Europe/Paris"`). Defaults to `"UTC"` |
| `date_ranges` | Yes | Non-empty list of period objects (see below). Periods must not overlap. Days between periods are silently skipped. |
| `date_ranges[].from` | Yes | Period start date `YYYY-MM-DD` |
| `date_ranges[].to` | Yes | Period end date `YYYY-MM-DD` (inclusive) |
| `date_ranges[].projects` | One of | List of `{"name": "...", "weight": N}` objects for this period. Hours are distributed proportionally by weight. |
| `date_ranges[].template` | One of | Name (or ID) of a Clockify timesheet template to replay on every week of the period. Each range needs exactly one of `projects` or `template` (see below). |
| `working_days.days` | Yes | Days of the week to fill (e.g. `["monday", ..., "friday"]`) |
| `working_days.start_time` | Yes | Work day start time (`HH:MM`) |
| `working_days.end_time` | Yes | Work day end time (`HH:MM`) |
| `working_days.number_of_worked_hours` | No | Actual worked hours per day (`H:MM`). If less than the start/end span, the difference is treated as a lunch break starting at 12:00. Defaults to the full span. |
| `off_days` | No | List of dates or ranges to mark as off (applies across all periods). Each entry is either a `"YYYY-MM-DD"` string or a `{"from": "...", "to": "..."}` range object. |

### Example

```json
{
  "api_key": "your_clockify_api_key_here",
  "timezone": "Europe/Paris",
  "date_ranges": [
    {
      "from": "2026-03-12",
      "to": "2026-04-06",
      "projects": [
        { "name": "Project Alpha", "weight": 3 },
        { "name": "Project Beta", "weight": 1 }
      ]
    },
    {
      "from": "2026-04-14",
      "to": "2026-04-26",
      "projects": [
        { "name": "Project Alpha", "weight": 1 },
        { "name": "Project Gamma", "weight": 2 }
      ]
    },
    {
      "from": "2026-05-04",
      "to": "2026-06-30",
      "template": "Classic week"
    }
  ],
  "working_days": {
    "days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
    "start_time": "09:00",
    "end_time": "17:00",
    "number_of_worked_hours": "7:00"
  },
  "off_days": [
    "2026-04-18",
    "2026-04-21",
    { "from": "2026-04-07", "to": "2026-04-11" }
  ]
}
```

In this example, each working day has 7h00 of work (09:00–12:00 morning, 13:00–17:00 afternoon). The week of 2026-04-07 to 2026-04-11 is off. The two periods use different project compositions: Alpha/Beta in the first, Alpha/Gamma in the second. Days between the two periods (2026-04-07 to 2026-04-13) are either off days or silently skipped. The third period replays the `Classic week` template.

### Template mode

Instead of `projects`, a period can name one of your Clockify **timesheet templates** (Timesheet view → ⋯ → *Save as template*). The template must have been saved from a week with time filled in: the tool reads its entries and replays them on every week of the period, keeping each entry's weekday, start time, duration, project, task, description, tags and billable flag. Hours can therefore differ per weekday and per project, which the weight system cannot express.

In template mode `working_days.days`, `start_time`, `end_time` and `number_of_worked_hours` only matter for `off_days` (the `Not working` entry). Weekdays that have no entry in the template are left untouched. As in project mode, a day where the template's project already has an entry is skipped.

Run with `--dry-run` first: it prints the template's entries (weekday, start–end, duration, project) before anything is written. A template saved from the timesheet view copies the entries exactly as they were, including any odd start time, and the Clockify API does not allow editing a template's entries afterwards (re-save it from a corrected week instead).

## Usage

```bash
# Activate the virtual environment first
source .venv/bin/activate       # bash/zsh
source .venv/bin/activate.fish  # fish

# Preview what would be created (no write to Clockify)
python -m clockify_automaton --dry-run my_config.json

# Run
python -m clockify_automaton my_config.json
```

The tool is safe to re-run: any day where a project already has an entry is skipped. After filling, it submits a weekly approval request for each week in which it created at least one entry.
