"""Registry that manages multiple confirmation signal sources."""

from __future__ import annotations

from uuid import UUID

import structlog

from .base import ConfirmationResult, ConfirmationSource

logger = structlog.get_logger()


class ConfirmationRegistry:
    """Holds all registered confirmation sources and fans out checks to them."""

    def __init__(self) -> None:
        self._sources: dict[str, ConfirmationSource] = {}

    def register(self, source: ConfirmationSource) -> None:
        """Register a confirmation source."""
        self._sources[source.source_id] = source
        logger.info("confirmation_source_registered", source_id=source.source_id)

    def get(self, source_id: str) -> ConfirmationSource | None:
        return self._sources.get(source_id)

    def get_all(self) -> list[ConfirmationSource]:
        return list(self._sources.values())

    async def check_all(
        self,
        events: list[dict],
    ) -> dict[str, dict[UUID, ConfirmationResult]]:
        """Run all registered sources against the given events.

        Returns ``{source_id: {event_uuid: ConfirmationResult, ...}, ...}``.
        Each source is called independently; a failure in one source does not
        prevent the others from completing.
        """
        all_results: dict[str, dict[UUID, ConfirmationResult]] = {}

        for source in self._sources.values():
            try:
                results = await source.check_batch(events)
                all_results[source.source_id] = results
                logger.info(
                    "confirmation_check_complete",
                    source_id=source.source_id,
                    events_checked=len(results),
                    confirmed=sum(1 for r in results.values() if r.exists),
                )
            except Exception as exc:
                logger.error(
                    "confirmation_check_failed",
                    source_id=source.source_id,
                    error=str(exc),
                )
                all_results[source.source_id] = {}

        return all_results
