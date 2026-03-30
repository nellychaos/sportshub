"""Liquipedia LoL adapter — community wiki, supplementary data only.

This adapter is intentionally limited in MVP scope. It provides tournament
metadata and team roster context, NOT match schedules (due to rate limits
and HTML parsing fragility).
"""

from datetime import datetime

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

BASE_URL = "https://liquipedia.net/leagueoflegends/api.php"
USER_AGENT = "Sportshub/0.1 (https://sportshub.dev; contact@sportshub.dev)"


class LiquipediaLoLAdapter(SourceAdapter):
    @property
    def source_id(self) -> str:
        return "liquipedia_lol"

    @property
    def sport(self) -> Sport:
        return Sport.LOL

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.COMMUNITY

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def initialize(self) -> None:
        await super().initialize()
        if self._client:
            # Liquipedia requires a descriptive User-Agent
            self._client.headers["User-Agent"] = USER_AGENT
            self._client.headers["Accept-Encoding"] = "gzip"

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming LoL events from Liquipedia.

        NOTE: For MVP, this adapter returns an empty list. Liquipedia's strict
        rate limits (1 req/2s) and HTML parsing fragility make it unsuitable as
        a primary schedule source. It will be used for supplementary tournament
        metadata and team roster context in future iterations.
        """
        events: list[RawEvent] = []

        try:
            # MVP: Only verify that we can reach Liquipedia
            params = {
                "action": "query",
                "meta": "siteinfo",
                "format": "json",
            }
            data = await self._get_json(BASE_URL, params=params)
            await self._rate_limit(2.0)  # Strict: 1 req per 2 seconds

            if data.get("query", {}).get("general"):
                self._record_success()
                logger.info("liquipedia_health_ok")
            else:
                self._record_failure("Unexpected response format")

        except Exception as e:
            self._record_failure(str(e))
            logger.error("liquipedia_fetch_failed", error=str(e))

        return events
