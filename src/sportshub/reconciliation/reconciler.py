"""Reconcile predicted event data against observed post-event results."""

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from sportshub.reconciliation.collector import ObservedResult

logger = structlog.get_logger()

# Events within 15 minutes of scheduled time are considered time-accurate.
TIME_ACCURACY_THRESHOLD_SECONDS = 900


@dataclass
class ReconciliationReport:
    """Summary of reconciliation between predicted and observed event data."""

    event_id: str  # UUID as string
    event_occurred: bool
    time_diff_seconds: int | None
    teams_correct: bool
    venue_correct: bool
    reconciled_at: datetime
    per_source_accuracy: list[dict] = field(default_factory=list)


class EventReconciler:
    """Compare predicted event data against observed results from multiple sources."""

    async def reconcile(
        self,
        event: dict,
        observed: list[ObservedResult],
    ) -> ReconciliationReport:
        """Reconcile a single event against observed results.

        Args:
            event: dict with keys: id, scheduled_at, home_team_id, away_team_id, venue, sport.
            observed: list of ObservedResult from various sources.

        Returns:
            ReconciliationReport summarising accuracy.
        """
        now = datetime.utcnow()

        if not observed:
            # No results available -- assume the event occurred (it's past schedule)
            # but we have no data to validate against.
            return ReconciliationReport(
                event_id=str(event["id"]),
                event_occurred=True,
                time_diff_seconds=None,
                teams_correct=True,
                venue_correct=True,
                reconciled_at=now,
                per_source_accuracy=[],
            )

        # Determine if the event occurred: majority vote on status
        status_votes = [o.event_status for o in observed]
        completed_count = sum(1 for s in status_votes if s == "completed")
        postponed_count = sum(1 for s in status_votes if s == "postponed")
        cancelled_count = sum(1 for s in status_votes if s == "cancelled")

        if cancelled_count > completed_count and cancelled_count > postponed_count:
            event_occurred = False
        elif postponed_count > completed_count:
            event_occurred = False
        else:
            event_occurred = True

        # Calculate aggregate time difference (median of available actual start times)
        scheduled_at = event["scheduled_at"]
        time_diffs: list[int] = []
        for o in observed:
            if o.actual_start_time is not None and scheduled_at is not None:
                diff = abs(int((o.actual_start_time - scheduled_at).total_seconds()))
                time_diffs.append(diff)

        aggregate_time_diff: int | None = None
        if time_diffs:
            time_diffs.sort()
            mid = len(time_diffs) // 2
            aggregate_time_diff = time_diffs[mid]

        # Team correctness: all sources confirming teams is good
        teams_correct = all(
            o.home_team_confirmed and o.away_team_confirmed for o in observed
        )

        # Venue correctness: at least one source confirming venue is sufficient
        venue_correct = any(o.venue_confirmed for o in observed)

        # Per-source accuracy details
        per_source: list[dict] = []
        for o in observed:
            source_time_diff: int | None = None
            time_accurate = True
            if o.actual_start_time is not None and scheduled_at is not None:
                source_time_diff = abs(
                    int((o.actual_start_time - scheduled_at).total_seconds())
                )
                time_accurate = source_time_diff <= TIME_ACCURACY_THRESHOLD_SECONDS

            per_source.append({
                "source_id": o.source_id,
                "time_diff_seconds": source_time_diff,
                "time_accurate": time_accurate,
                "teams_correct": o.home_team_confirmed and o.away_team_confirmed,
                "venue_correct": o.venue_confirmed,
                "score": o.score,
                "event_status": o.event_status,
            })

        return ReconciliationReport(
            event_id=str(event["id"]),
            event_occurred=event_occurred,
            time_diff_seconds=aggregate_time_diff,
            teams_correct=teams_correct,
            venue_correct=venue_correct,
            reconciled_at=now,
            per_source_accuracy=per_source,
        )
