# clockify-automaton

A Python CLI tool that automatically fills [Clockify](https://app.clockify.me/) timesheets by distributing working hours across multiple projects based on configurable weights.

## Features

- Fills working days across a date range with time entries per project
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
| `date_range.from` | Yes | Start date `YYYY-MM-DD` |
| `date_range.to` | Yes | End date `YYYY-MM-DD` (inclusive) |
| `working_days.days` | Yes | Days of the week to fill (e.g. `["monday", ..., "friday"]`) |
| `working_days.start_time` | Yes | Work day start time (`HH:MM`) |
| `working_days.end_time` | Yes | Work day end time (`HH:MM`) |
| `working_days.number_of_worked_hours` | No | Actual worked hours per day (`H:MM`). If less than the start/end span, the difference is treated as a lunch break starting at 12:00. Defaults to the full span. |
| `off_days` | No | List of dates or ranges to mark as off. Each entry is either a `"YYYY-MM-DD"` string or a `{"from": "...", "to": "..."}` range object |
| `projects` | Yes | List of `{"name": "...", "weight": N}` objects. Hours are distributed proportionally by weight |

### Example

```json
{
  "api_key": "your_clockify_api_key_here",
  "timezone": "Europe/Paris",
  "date_range": {
    "from": "2025-03-12",
    "to": "2025-04-26"
  },
  "working_days": {
    "days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
    "start_time": "09:00",
    "end_time": "18:00",
    "number_of_worked_hours": "7:30"
  },
  "off_days": [
    "2025-04-18",
    { "from": "2025-04-07", "to": "2025-04-11" }
  ],
  "projects": [
    { "name": "Project Alpha", "weight": 3 },
    { "name": "Project Beta", "weight": 1 }
  ]
}
```

In this example, each working day has 7h30 of work (09:00–12:00 morning, 13:30–18:00 afternoon). Project Alpha gets 75% of those hours, Project Beta 25%.

## Usage

```bash
# Activate the virtual environment first
source .venv/bin/activate       # bash/zsh
source .venv/bin/activate.fish  # fish

# Run
python -m clockify_automaton my_config.json
```

The tool is safe to re-run: any day where a project already has an entry is skipped.
