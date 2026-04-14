# clockify-automaton

A Python CLI tool that automatically fills [Clockify](https://app.clockify.me/) timesheets by distributing working hours across multiple projects based on configurable weights.

## Features

- Fills working days across one or more date ranges with time entries per project
- Each date range has its own project composition — useful when your project allocation changes mid-period
- Distributes hours proportionally using a weight system
- Handles lunch breaks (blank gap in the calendar)
- Marks off days (holidays, vacation) with a "Not working" project
- Skips days that already have entries (safe to re-run)

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
| `date_ranges[].projects` | Yes | List of `{"name": "...", "weight": N}` objects for this period. Hours are distributed proportionally by weight. |
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

In this example, each working day has 7h00 of work (09:00–12:00 morning, 13:00–17:00 afternoon). The week of 2026-04-07 to 2026-04-11 is off. The two periods use different project compositions: Alpha/Beta in the first, Alpha/Gamma in the second. Days between the two periods (2026-04-07 to 2026-04-13) are either off days or silently skipped.

## Usage

```bash
# Activate the virtual environment first
source .venv/bin/activate       # bash/zsh
source .venv/bin/activate.fish  # fish

# Run
python -m clockify_automaton my_config.json
```

The tool is safe to re-run: any day where a project already has an entry is skipped.
