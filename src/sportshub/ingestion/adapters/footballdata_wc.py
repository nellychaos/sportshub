"""Football-Data.org adapter for FIFA World Cup — free tier, API key required.

Docs: https://www.football-data.org/documentation/api
Free tier: 10 requests/minute. World Cup competition code: WC.
"""

from datetime import datetime

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

BASE_URL = "https://api.football-data.org/v4"


class FootballDataWCAdapter(SourceAdapter):
    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key

    @property
    def source_id(self) -> str:
        return "footballdata_wc"

    @property
    def sport(self) -> Sport:
        return Sport.FOOTBALL

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def initialize(self) -> None:
        """Set up HTTP client with API key authentication."""
        await super().initialize()
        if self._client:
            self._client.headers["X-Auth-Token"] = self._api_key

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming FIFA World Cup matches.

        Uses the /competitions/WC/matches endpoint with status=SCHEDULED filter.
        Football-Data.org returns all scheduled matches in a single paginated response.
        """
        events: list[RawEvent] = []

        try:
            data = await self._get_json(
                f"{BASE_URL}/competitions/WC/matches",
                params={"status": "SCHEDULED"},
            )
            await self._rate_limit(6.0)  # Stay well within 10 req/min

            for match in data.get("matches", []):
                try:
                    raw = self._parse_match(match)
                    if raw:
                        events.append(raw)
                except Exception as e:
                    logger.warning(
                        "footballdata_parse_error",
                        match_id=match.get("id", "unknown"),
                        error=str(e),
                    )

            self._record_success()
            logger.info("footballdata_wc_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("footballdata_wc_fetch_failed", error=str(e))

        return events

    def _parse_match(self, match: dict) -> RawEvent | None:
        """Parse a single Football-Data.org match into a RawEvent."""
        home_team = match.get("homeTeam", {})
        away_team = match.get("awayTeam", {})

        home_name = home_team.get("name", "")
        away_name = away_team.get("name", "")

        # Skip TBD/placeholder matches (knockout rounds before group stage ends)
        if not home_name or not away_name:
            return None

        utc_date = match.get("utcDate", "")
        if not utc_date:
            return None

        scheduled_at = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))

        # Extract stage and group info
        stage = match.get("stage", "")
        group = match.get("group", "")
        matchday = match.get("matchday")

        venue = match.get("venue", "")

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(match["id"]),
            sport=Sport.FOOTBALL,
            raw_home_team=home_name,
            raw_away_team=away_name,
            raw_competition="FIFA World Cup 2026",
            scheduled_at=scheduled_at,
            venue=venue if venue else None,
            raw_metadata={
                "home_tla": home_team.get("tla", ""),
                "away_tla": away_team.get("tla", ""),
                "home_crest": home_team.get("crest", ""),
                "away_crest": away_team.get("crest", ""),
                "stage": stage,
                "group": group,
                "matchday": matchday,
                "competition_id": match.get("competition", {}).get("id"),
            },
        )
