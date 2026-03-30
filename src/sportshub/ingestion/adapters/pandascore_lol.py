"""PandaScore LoL adapter — professional esports data provider."""

from datetime import datetime

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

BASE_URL = "https://api.pandascore.co"

MATCH_FORMAT_MAP = {
    1: "single",
    3: "bo3",
    5: "bo5",
}


class PandaScoreLoLAdapter(SourceAdapter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    @property
    def source_id(self) -> str:
        return "pandascore_lol"

    @property
    def sport(self) -> Sport:
        return Sport.LOL

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def initialize(self) -> None:
        await super().initialize()
        if self._client:
            self._client.headers["Authorization"] = f"Bearer {self._token}"

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming LoL matches with header-based pagination."""
        events: list[RawEvent] = []

        try:
            page = 1
            while True:
                data = await self._get_json(
                    f"{BASE_URL}/lol/matches/upcoming",
                    params={"per_page": 100, "page": page},
                )

                if not data:
                    break

                for match in data:
                    try:
                        raw = self._parse_match(match)
                        if raw:
                            events.append(raw)
                    except Exception as e:
                        logger.warning(
                            "pandascore_parse_error",
                            match_id=match.get("id", "unknown"),
                            error=str(e),
                        )

                # PandaScore returns empty list when no more pages
                if len(data) < 100:
                    break

                page += 1
                await self._rate_limit(1.0)

            self._record_success()
            logger.info("pandascore_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("pandascore_fetch_failed", error=str(e))

        return events

    def _parse_match(self, match: dict) -> RawEvent | None:
        """Parse a single PandaScore match."""
        opponents = match.get("opponents", [])

        # Skip TBD bracket matches
        if len(opponents) < 2:
            return None
        if not opponents[0].get("opponent") or not opponents[1].get("opponent"):
            return None

        scheduled_str = match.get("scheduled_at")
        if not scheduled_str:
            return None

        scheduled_at = datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))

        team1 = opponents[0]["opponent"]
        team2 = opponents[1]["opponent"]

        num_games = match.get("number_of_games", 1)
        match_format = MATCH_FORMAT_MAP.get(num_games, "single")

        tournament = match.get("tournament", {})
        league = match.get("league", {})

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(match["id"]),
            sport=Sport.LOL,
            raw_home_team=team1.get("name", ""),
            raw_away_team=team2.get("name", ""),
            raw_competition=league.get("name", tournament.get("name", "Unknown")),
            scheduled_at=scheduled_at,
            venue=None,
            raw_metadata={
                "team1_acronym": team1.get("acronym", ""),
                "team2_acronym": team2.get("acronym", ""),
                "match_format": match_format,
                "match_type": match.get("match_type", ""),
                "tournament_name": tournament.get("name", ""),
                "serie_name": match.get("serie", {}).get("name", ""),
            },
        )
