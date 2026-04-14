import datetime

import requests

BASE_URL = "https://api.clockify.me/api/v1"


class ClockifyError(Exception):
    pass


class ClockifyClient:
    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers.update({
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        })
        self._user_id: str | None = None           # lazily populated by _init_user()
        self._workspace_id: str | None = None      # lazily populated by _init_user()
        self._projects_cache: list | None = None   # lazily populated by get_projects(), avoids repeated API calls

    def _request(self, method: str, path: str, **kwargs) -> object:
        url = f"{BASE_URL}{path}"
        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.RequestException as e:
            raise ClockifyError(f"Network error: {e}") from e
        if not resp.ok:
            raise ClockifyError(
                f"Clockify API error {resp.status_code} on {method} {path}: {resp.text}"
            )
        return resp.json()

    def _init_user(self) -> None:
        if self._user_id is None:
            data = self._request("GET", "/user")
            self._user_id = data["id"]
            self._workspace_id = data["defaultWorkspace"]

    def get_user_id(self) -> str:
        self._init_user()
        return self._user_id

    def get_workspace_id(self) -> str:
        self._init_user()
        return self._workspace_id

    def get_projects(self) -> list:
        if self._projects_cache is None:
            wid = self.get_workspace_id()
            self._projects_cache = self._request(
                "GET",
                f"/workspaces/{wid}/projects",
                params={"page-size": 500},
            )
        return self._projects_cache

    def resolve_project_id(self, name_or_id: str) -> str:
        """Resolve a project name (case-insensitive) or ID to a Clockify project ID."""
        projects = self.get_projects()
        # Try name match first
        for p in projects:
            if p["name"].lower() == name_or_id.lower():
                return p["id"]
        # Fallback: treat value as a literal ID
        for p in projects:
            if p["id"] == name_or_id:
                return p["id"]
        raise ValueError(
            f"Project not found: {name_or_id!r}. "
            f"Available projects: {[p['name'] for p in projects]}"
        )

    def get_entries_for_day(self, date: datetime.date, tz) -> list:
        """Fetch all time entries for a given day (in the user's timezone)."""
        wid = self.get_workspace_id()
        uid = self.get_user_id()
        day_start = datetime.datetime(
            date.year, date.month, date.day, 0, 0, 0, tzinfo=tz
        )
        day_end = day_start + datetime.timedelta(days=1)
        return self._request(
            "GET",
            f"/workspaces/{wid}/user/{uid}/time-entries",
            params={
                "start": _to_clockify_time(day_start),
                "end": _to_clockify_time(day_end),
                "page-size": 500,
            },
        )

    def create_entry(
        self, project_id: str, start: datetime.datetime, end: datetime.datetime
    ) -> dict:
        """Create a time entry for the given project and time range."""
        wid = self.get_workspace_id()
        return self._request(
            "POST",
            f"/workspaces/{wid}/time-entries",
            json={
                "projectId": project_id,
                "start": _to_clockify_time(start),
                "end": _to_clockify_time(end),
            },
        )

    def submit_approval_request(self, week_start: datetime.date) -> dict:
        """Submit a weekly approval request for the week starting on the given Monday."""
        wid = self.get_workspace_id()
        period_start = datetime.datetime(
            week_start.year, week_start.month, week_start.day,
            0, 0, 0, tzinfo=datetime.timezone.utc,
        )
        return self._request(
            "POST",
            f"/workspaces/{wid}/approval-requests",
            json={
                "period": "WEEKLY",
                "periodStart": period_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
        )


def _to_clockify_time(dt: datetime.datetime) -> str:
    """Format a datetime as Clockify's expected ISO 8601 UTC string."""
    if dt.tzinfo is not None:
        utc = dt.astimezone(datetime.timezone.utc)
    else:
        utc = dt.replace(tzinfo=datetime.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
