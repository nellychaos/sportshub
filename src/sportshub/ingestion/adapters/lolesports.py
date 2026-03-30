"""LoL Esports API adapter — official Riot data via public endpoint."""

from datetime import datetime

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

BASE_URL = "https://esports-api.lolesports.com/persisted/gw"

# Major league IDs (discovered via getLeagues endpoint)
MAJOR_LEAGUE_IDS = [
    "98767991299243165",   # LCK
    "98767991314006698",   # LPL
    "98767991302996019",   # LEC
    "98767991310872058",   # LCS
    "98767991325878492",   # MSI
    "98767975604431411",   # Worlds
]

MATCH_FORMAT_MAP = {
    1: "single",
    3: "bo3",
    5: "bo5",
}


class LoLEsportsAdapter(SourceAdapter):
    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key

    @property
    def source_id(self) -> str:
        return "lolesports"

    @property
    def sport(self) -> Sport:
        return Sport.LOL

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.OFFICIAL

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def initialize(self) -> None:
        await super().initialize()
        if self._client:
            self._client.headers["x-api-key"] = self._api_key

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming LoL matches from all major leagues."""
        events: list[RawEvent] = []

        try:
            for league_id in MAJOR_LEAGUE_IDS:
                league_events = await self._fetch_league_schedule(league_id)
                events.extend(league_events)
                await self._rate_limit(5.0)  # 1 req per 5 seconds

            self._record_success()
            logger.info("lolesports_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("lolesports_fetch_failed", error=str(e))

        return events

    async def _fetch_league_schedule(self, league_id: str) -> list[RawEvent]:
        """Fetch schedule for a single league, following pagination."""
        events: list[RawEvent] = []
        page_token: str | None = None

        while True:
            params: dict = {"hl": "en-US", "leagueId": league_id}
            if page_token:
                params["pageToken"] = page_token

            data = await self._get_json(f"{BASE_URL}/getSchedule", params=params)

            schedule = data.get("data", {}).get("schedule", {})
            for event in schedule.get("events", []):
                try:
                    raw = self._parse_event(event)
                    if raw:
                        events.append(raw)
                except Exception as e:
                    logger.warning("lolesports_parse_error", error=str(e))

            # Check for next page
            pages = schedule.get("pages", {})
            older_token = pages.get("older")
            if older_token and older_token != page_token:
                page_token = older_token
                await self._rate_limit(5.0)
            else:
                break

        return events

    def _parse_event(self, event: dict) -> RawEvent | None:
        """Parse a single LoL Esports event."""
        # Skip completed events
        state = event.get("state", "")
        if state == "completed":
            return None

        match = event.get("match", {})
        if not match:
            return None

        teams = match.get("teams", [])
        if len(teams) < 2:
            return None

        # Skip TBD matches
        if any(t.get("name", "").upper() == "TBD" for t in teams):
            return None

        start_time = event.get("startTime", "")
        if not start_time:
            return None

        scheduled_at = datetime.fromisoformat(start_time.replace("Z", "+00:00"))

        # Determine match format
        strategy = match.get("strategy", {})
        count = strategy.get("count", 1)
        match_format = MATCH_FORMAT_MAP.get(count, "single")

        league = event.get("league", {})

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(match.get("id", "")),
            sport=Sport.LOL,
            raw_home_team=teams[0].get("name", ""),
            raw_away_team=teams[1].get("name", ""),
            raw_competition=league.get("name", "Unknown League"),
            scheduled_at=scheduled_at,
            venue=None,  # LoL matches are online
            raw_metadata={
                "team1_code": teams[0].get("code", ""),
                "team2_code": teams[1].get("code", ""),
                "match_format": match_format,
                "block_name": event.get("blockName", ""),
                "league_slug": league.get("slug", ""),
            },
        )
