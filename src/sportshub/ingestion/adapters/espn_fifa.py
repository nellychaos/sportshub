"""ESPN FIFA World Cup adapter — undocumented public API, no auth required."""

from datetime import date, datetime, timedelta, timezone

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

# ESPN uses "fifa.world" slug for the FIFA World Cup
BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


class ESPNFIFAAdapter(SourceAdapter):
    @property
    def source_id(self) -> str:
        return "espn_fifa"

    @property
    def sport(self) -> Sport:
        return Sport.FOOTBALL

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming FIFA World Cup matches from ESPN.

        Scans the World Cup 2026 tournament window (June 11 – July 19)
        plus 14 days from today for any pre-tournament matches.
        """
        events: list[RawEvent] = []
        today = datetime.now(timezone.utc).date()

        # Build date ranges to scan
        dates_to_scan: list[date] = []

        # Always scan next 14 days
        for i in range(15):
            dates_to_scan.append(today + timedelta(days=i))

        # Also scan WC tournament window (June 11 – July 19, 2026)
        wc_start = date(2026, 6, 11)
        wc_end = date(2026, 7, 20)
        current = wc_start
        while current <= wc_end:
            dates_to_scan.append(current)
            current += timedelta(days=1)

        # Deduplicate and sort
        dates_to_scan = sorted(set(dates_to_scan))

        try:
            for scan_date in dates_to_scan:
                date_str = scan_date.strftime("%Y%m%d")

                data = await self._get_json(BASE_URL, params={"dates": date_str})
                await self._rate_limit(1.0)  # 1 req per second (public API)

                for espn_event in data.get("events", []):
                    try:
                        raw = self._parse_event(espn_event)
                        if raw:
                            events.append(raw)
                    except Exception as e:
                        logger.warning(
                            "espn_fifa_parse_error",
                            event_id=espn_event.get("id", "unknown"),
                            error=str(e),
                        )

            self._record_success()
            logger.info("espn_fifa_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("espn_fifa_fetch_failed", error=str(e))

        return events

    def _parse_event(self, espn_event: dict) -> RawEvent | None:
        """Parse a single ESPN FIFA event into a RawEvent."""
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

        # Skip TBD/placeholder matches (includes knockout stage placeholders)
        placeholder_keywords = ["TBD", "Winner", "Loser", "Group", "Third Place",
                                "Playoff Path", "Round of", "Quarterfinal", "Semifinal"]
        if not home_name or not away_name:
            return None
        if any(kw in home_name for kw in placeholder_keywords) or \
           any(kw in away_name for kw in placeholder_keywords):
            return None

        # ESPN dates are ISO 8601 UTC
        scheduled_str = espn_event.get("date", "")
        if not scheduled_str:
            return None

        scheduled_at = datetime.fromisoformat(scheduled_str.replace("Z", "+00:00"))

        venue_data = competition.get("venue", {})
        venue = venue_data.get("fullName") if venue_data else None
        venue_city = venue_data.get("address", {}).get("city") if venue_data else None

        # Extract group/stage info from competition notes
        stage = ""
        notes = competition.get("notes", [])
        if notes:
            stage = notes[0].get("headline", "")

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(espn_event["id"]),
            sport=Sport.FOOTBALL,
            raw_home_team=home_name,
            raw_away_team=away_name,
            raw_competition="FIFA World Cup 2026",
            scheduled_at=scheduled_at,
            venue=venue,
            raw_metadata={
                "home_abbreviation": home.get("team", {}).get("abbreviation", ""),
                "away_abbreviation": away.get("team", {}).get("abbreviation", ""),
                "status": espn_event.get("status", {}).get("type", {}).get("name", ""),
                "venue_city": venue_city,
                "stage": stage,
            },
        )
