"""ESPN NBA adapter — undocumented public API, no auth required."""

from datetime import datetime, timedelta

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"


class ESPNNBAAdapter(SourceAdapter):
    @property
    def source_id(self) -> str:
        return "espn_nba"

    @property
    def sport(self) -> Sport:
        return Sport.NBA

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming NBA games from ESPN for the next 7 days."""
        events: list[RawEvent] = []
        today = datetime.utcnow().date()

        try:
            for day_offset in range(8):
                date = today + timedelta(days=day_offset)
                date_str = date.strftime("%Y%m%d")

                data = await self._get_json(BASE_URL, params={"dates": date_str})
                await self._rate_limit(5.0)  # 1 req per 5 seconds

                for espn_event in data.get("events", []):
                    try:
                        raw = self._parse_event(espn_event)
                        if raw:
                            events.append(raw)
                    except Exception as e:
                        logger.warning(
                            "espn_parse_error",
                            event_id=espn_event.get("id", "unknown"),
                            error=str(e),
                        )

            self._record_success()
            logger.info("espn_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("espn_fetch_failed", error=str(e))

        return events

    def _parse_event(self, espn_event: dict) -> RawEvent | None:
        """Parse a single ESPN event into a RawEvent."""
        competitions = espn_event.get("competitions", [])
        if not competitions:
            return None

        competition = competitions[0]
        competitors = competition.get("competitors", [])
        if len(competitors) < 2:
            return None

        # ESPN marks competitors as home/away
        home = away = None
        for comp in competitors:
            if comp.get("homeAway") == "home":
                home = comp
            else:
                away = comp

        if not home or not away:
            return None

        home_name = home.get("team", {}).get("displayName", "")
        away_name = away.get("team", {}).get("displayName", "")

        # ESPN dates are ISO 8601 UTC
        scheduled_str = espn_event.get("date", "")
        if not scheduled_str:
            return None

        scheduled_at = datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))

        venue_data = competition.get("venue", {})
        venue = venue_data.get("fullName") if venue_data else None

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(espn_event["id"]),
            sport=Sport.NBA,
            raw_home_team=home_name,
            raw_away_team=away_name,
            raw_competition=espn_event.get("season", {}).get("slug", "nba-regular-season"),
            scheduled_at=scheduled_at,
            venue=venue,
            raw_metadata={
                "home_abbreviation": home.get("team", {}).get("abbreviation", ""),
                "away_abbreviation": away.get("team", {}).get("abbreviation", ""),
                "status": espn_event.get("status", {}).get("type", {}).get("name", ""),
            },
        )
