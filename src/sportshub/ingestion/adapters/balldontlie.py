"""BallDontLie NBA adapter — free API with cursor pagination."""

from datetime import datetime, timedelta, timezone

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

BASE_URL = "https://api.balldontlie.io/v1"


class BallDontLieAdapter(SourceAdapter):
    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key

    @property
    def source_id(self) -> str:
        return "balldontlie_nba"

    @property
    def sport(self) -> Sport:
        return Sport.NBA

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def initialize(self) -> None:
        await super().initialize()
        if self._client:
            self._client.headers["Authorization"] = self._api_key

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming NBA games with cursor pagination."""
        events: list[RawEvent] = []
        today = datetime.utcnow().date()
        end_date = today + timedelta(days=30)

        try:
            cursor: int | None = None

            while True:
                params: dict = {
                    "start_date": today.isoformat(),
                    "end_date": end_date.isoformat(),
                    "per_page": 100,
                }
                if cursor:
                    params["cursor"] = cursor

                data = await self._get_json(f"{BASE_URL}/games", params=params)
                await self._rate_limit(12.0)  # 5 req/min = 12s between requests

                for game in data.get("data", []):
                    try:
                        raw = self._parse_game(game)
                        if raw:
                            events.append(raw)
                    except Exception as e:
                        logger.warning(
                            "balldontlie_parse_error",
                            game_id=game.get("id", "unknown"),
                            error=str(e),
                        )

                meta = data.get("meta", {})
                cursor = meta.get("next_cursor")
                if not cursor:
                    break

            self._record_success()
            logger.info("balldontlie_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("balldontlie_fetch_failed", error=str(e))

        return events

    def _parse_game(self, game: dict) -> RawEvent | None:
        """Parse a BallDontLie game. NOTE: date has no time component."""
        home_team = game.get("home_team", {})
        visitor_team = game.get("visitor_team", {})

        home_name = home_team.get("full_name", "")
        away_name = visitor_team.get("full_name", "")

        if not home_name or not away_name:
            return None

        # BallDontLie only provides date (YYYY-MM-DD), no time
        date_str = game.get("date", "")
        if not date_str:
            return None

        # Parse as midnight UTC (known limitation of this source)
        scheduled_at = datetime.fromisoformat(date_str[:10]).replace(tzinfo=timezone.utc)

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(game["id"]),
            sport=Sport.NBA,
            raw_home_team=home_name,
            raw_away_team=away_name,
            raw_competition=f"NBA {'Postseason' if game.get('postseason') else 'Regular Season'}",
            scheduled_at=scheduled_at,
            venue=None,  # BallDontLie doesn't provide venue
            raw_metadata={
                "home_abbreviation": home_team.get("abbreviation", ""),
                "away_abbreviation": visitor_team.get("abbreviation", ""),
                "season": game.get("season"),
                "postseason": game.get("postseason", False),
            },
        )
