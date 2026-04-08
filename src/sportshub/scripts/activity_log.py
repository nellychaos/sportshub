"""Script activity logging.

Provides a lightweight logging mechanism for manual script runs (merges,
fetches, scrapes). Logs to data/script_activity.json as a simple
append-on-disk JSON array.

Usage::

    from sportshub.scripts.activity_log import log_script_run

    with log_script_run("merge_bref_advanced") as run:
        # ... do work ...
        run.records_processed = 498
        run.summary = "Merged advanced stats for 498/535 players"
"""

from __future__ import annotations

import json
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

_LOG_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "script_activity.json"
_MAX_ENTRIES = 200


@dataclass
class ScriptRun:
    """A single script execution record."""

    script_name: str
    started_at: str = ""
    completed_at: str | None = None
    status: str = "running"  # running, success, failed
    records_processed: int = 0
    summary: str = ""
    error_message: str | None = None


def _read_log() -> list[dict[str, Any]]:
    """Read existing log entries."""
    if not _LOG_FILE.exists():
        return []
    try:
        with open(_LOG_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _write_log(entries: list[dict[str, Any]]) -> None:
    """Write log entries, trimming to max size."""
    trimmed = entries[-_MAX_ENTRIES:]
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG_FILE, "w") as f:
        json.dump(trimmed, f, indent=2, default=str)
        f.write("\n")


@contextmanager
def log_script_run(script_name: str) -> Generator[ScriptRun, None, None]:
    """Context manager that logs a script run to the activity log.

    On enter, creates a "running" entry. On exit, updates to "success"
    or "failed" (if exception). The caller can set fields on the yielded
    ScriptRun object during execution.

    Example::

        with log_script_run("fetch_nba_players") as run:
            players = fetch_players()
            run.records_processed = len(players)
            run.summary = f"Fetched {len(players)} players"
    """
    run = ScriptRun(
        script_name=script_name,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        yield run
        run.status = "success"
    except Exception as exc:
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        run.completed_at = datetime.now(timezone.utc).isoformat()
        entries = _read_log()
        entries.append(asdict(run))
        _write_log(entries)


def get_recent_activity(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent script activity entries, newest first.

    Args:
        limit: Maximum number of entries to return.

    Returns:
        List of ScriptRun dicts, most recent first.
    """
    entries = _read_log()
    entries.reverse()
    return entries[:limit]
