"""NBA.com CDN adapter — official league schedule, static JSON file."""

from datetime import datetime, timezone

import httpx
import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"


class NBAComCDNAdapter(SourceAdapter):
    @property
    def source_id(self) -> str:
        return "nbacom_cdn"

    @property
    def sport(self) -> Sport:
        return Sport.NBA

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.OFFICIAL

    @property
    def source_timezone(self) -> str:
        return "America/New_York"

    async def initialize(self) -> None:
        """Override with longer timeout and browser-like headers — CDN file is ~8MB."""
        self._client = httpx.AsyncClient(
            timeout=90.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nba.com/schedule",
            },
            follow_redirects=True,
        )

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch full NBA season schedule, filter to upcoming games."""
        events: list[RawEvent] = []
        now = datetime.now(timezone.utc)

        try:
            data = await self._get_json(SCHEDULE_URL)

            league_schedule = data.get("leagueSchedule", {})
            game_dates = league_schedule.get("gameDates", [])

            for game_date in game_dates:
                for game in game_date.get("games", []):
                    try:
                        raw = self._parse_game(game, now)
                        if raw:
                            events.append(raw)
                    except Exception as e:
                        logger.warning(
                            "nbacom_parse_error",
                            game_id=game.get("gameId", "unknown"),
                            error=str(e),
                        )

            self._record_success()
            logger.info("nbacom_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("nbacom_fetch_failed", error=str(e))

        return events

    def _parse_game(self, game: dict, now: datetime) -> RawEvent | None:
        """Parse a single NBA.com game into a RawEvent. Returns None for past games."""
        scheduled_str = game.get("gameDateTimeUTC", "")
        if not scheduled_str:
            return None

        scheduled_at = datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))

        # Filter: only upcoming games
        if scheduled_at < now:
            return None

        home = game.get("homeTeam", {})
        away = game.get("awayTeam", {})

        home_name = home.get("teamName", "")
        away_name = away.get("teamName", "")

        if not home_name or not away_name:
            return None

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(game.get("gameId", "")),
            sport=Sport.NBA,
            raw_home_team=home_name,
            raw_away_team=away_name,
            raw_competition="NBA Regular Season",
            scheduled_at=scheduled_at,
            venue=game.get("arenaName"),
            raw_metadata={
                "home_tricode": home.get("teamTricode", ""),
                "away_tricode": away.get("teamTricode", ""),
                "series_text": game.get("seriesText", ""),
                "game_status": game.get("gameStatus", 0),
            },
        )
