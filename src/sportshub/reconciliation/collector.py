"""Collect observed results from source adapters for completed events."""

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from sportshub.ingestion.base import SourceAdapter

logger = structlog.get_logger()


@dataclass
class ObservedResult:
    """Result observed from a single source for a completed event."""

    source_id: str
    actual_start_time: datetime | None = None
    home_team_confirmed: bool = False
    away_team_confirmed: bool = False
    venue_confirmed: bool = False
    score: str | None = None
    event_status: str = "completed"  # completed / postponed / cancelled


class ResultCollector:
    """Collect post-event results from all adapters that contributed source records."""

    async def collect_results(
        self,
        event: dict,
        source_records: list[dict],
        adapters: dict[str, SourceAdapter],
    ) -> list[ObservedResult]:
        """Collect results from adapters for source records linked to an event.

        Args:
            event: dict with event data (id, home_team_id, away_team_id, venue, scheduled_at, sport).
            source_records: list of dicts with source_id, source_event_id, raw_home_team, raw_away_team, venue.
            adapters: mapping of source_id -> SourceAdapter.

        Returns:
            List of ObservedResult, one per source that returned data.
        """
        results: list[ObservedResult] = []

        for sr in source_records:
            source_id = sr["source_id"]
            source_event_id = sr["source_event_id"]

            adapter = adapters.get(source_id)
            if adapter is None:
                logger.debug(
                    "reconciliation_adapter_not_found",
                    source_id=source_id,
                    event_id=str(event["id"]),
                )
                continue

            try:
                result_data = await adapter.fetch_result(source_event_id)
            except Exception as exc:
                logger.warning(
                    "reconciliation_fetch_result_error",
                    source_id=source_id,
                    source_event_id=source_event_id,
                    error=str(exc),
                )
                continue

            if result_data is None:
                # Adapter does not support fetch_result or event not found.
                # Build a minimal result from the source record itself.
                results.append(
                    ObservedResult(
                        source_id=source_id,
                        home_team_confirmed=True,
                        away_team_confirmed=True,
                        venue_confirmed=sr.get("venue") is not None,
                        event_status="completed",
                    )
                )
                continue

            # Parse adapter response
            actual_start = result_data.get("actual_start_time")
            if isinstance(actual_start, str):
                try:
                    actual_start = datetime.fromisoformat(actual_start.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    actual_start = None

            status = result_data.get("status", "completed")
            if status not in ("completed", "postponed", "cancelled"):
                status = "completed"

            results.append(
                ObservedResult(
                    source_id=source_id,
                    actual_start_time=actual_start,
                    home_team_confirmed=result_data.get("home_team_confirmed", True),
                    away_team_confirmed=result_data.get("away_team_confirmed", True),
                    venue_confirmed=result_data.get("venue_confirmed", False),
                    score=result_data.get("score"),
                    event_status=status,
                )
            )

        return results
