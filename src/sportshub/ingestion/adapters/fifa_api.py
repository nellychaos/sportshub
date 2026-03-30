"""FIFA official API adapter — FIFA+ / FIFA.com match data.

FIFA publishes match data via their public-facing APIs for major tournaments.
This adapter targets the FIFA World Cup 2026 schedule endpoints.
"""

from datetime import datetime

import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

# FIFA's public API for World Cup data (API used by FIFA+ web app)
BASE_URL = "https://api.fifa.com/api/v3"
# Competition ID for FIFA World Cup 2026 (men's)
WC_COMPETITION_ID = "17"
WC_SEASON_ID = "255711"


class FIFAApiAdapter(SourceAdapter):
    @property
    def source_id(self) -> str:
        return "fifa_api"

    @property
    def sport(self) -> Sport:
        return Sport.FOOTBALL

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.OFFICIAL

    @property
    def source_timezone(self) -> str:
        return "UTC"

    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming FIFA World Cup matches from the official FIFA API.

        Uses the calendar/matches endpoint filtered to the World Cup competition.
        """
        events: list[RawEvent] = []

        try:
            data = await self._get_json(
                f"{BASE_URL}/calendar/matches",
                params={
                    "idCompetition": WC_COMPETITION_ID,
                    "idSeason": WC_SEASON_ID,
                    "count": 100,
                    "language": "en",
                },
            )
            await self._rate_limit(3.0)

            results = data.get("Results", [])
            for match in results:
                try:
                    raw = self._parse_match(match)
                    if raw:
                        events.append(raw)
                except Exception as e:
                    logger.warning(
                        "fifa_api_parse_error",
                        match_id=match.get("IdMatch", "unknown"),
                        error=str(e),
                    )

            self._record_success()
            logger.info("fifa_api_fetch_complete", events_count=len(events))

        except Exception as e:
            self._record_failure(str(e))
            logger.error("fifa_api_fetch_failed", error=str(e))

        return events

    def _parse_match(self, match: dict) -> RawEvent | None:
        """Parse a single FIFA API match into a RawEvent."""
        # FIFA API uses nested team objects
        home_team = match.get("Home", {})
        away_team = match.get("Away", {})

        # Team names are in a localized Description list
        home_name = self._extract_team_name(home_team)
        away_name = self._extract_team_name(away_team)

        # Skip placeholder matches (TBD opponents in knockout rounds)
        if not home_name or not away_name:
            return None

        # Date field format: "/Date(1718100000000)/" or ISO string
        date_str = match.get("Date", "")
        scheduled_at = self._parse_fifa_date(date_str)
        if not scheduled_at:
            return None

        # Only return future/scheduled matches
        match_status = match.get("MatchStatus", 0)
        # Status 1 = Scheduled/Upcoming, skip completed matches
        if match_status not in (0, 1):
            return None

        # Venue info
        stadium = match.get("Stadium", {})
        venue_name = self._extract_description(stadium, "Name")
        venue_city = self._extract_description(stadium, "CityName")
        venue = f"{venue_name}, {venue_city}" if venue_name and venue_city else venue_name

        # Stage info
        stage_name = self._extract_description(match.get("StageName", {}))
        group_name = self._extract_description(match.get("GroupName", {}))

        return RawEvent(
            source_id=self.source_id,
            source_event_id=str(match.get("IdMatch", "")),
            sport=Sport.FOOTBALL,
            raw_home_team=home_name,
            raw_away_team=away_name,
            raw_competition="FIFA World Cup 2026",
            scheduled_at=scheduled_at,
            venue=venue,
            raw_metadata={
                "home_abbreviation": home_team.get("Abbreviation", ""),
                "away_abbreviation": away_team.get("Abbreviation", ""),
                "stage": stage_name or "",
                "group": group_name or "",
                "match_number": match.get("MatchNumber"),
                "stadium_id": stadium.get("IdStadium", ""),
                "venue_city": venue_city or "",
            },
        )

    @staticmethod
    def _extract_team_name(team_obj: dict) -> str:
        """Extract the English team name from FIFA's localized team object."""
        # Try TeamName first, then ShortClubName
        for field in ("TeamName", "ShortClubName"):
            name_list = team_obj.get(field, [])
            if isinstance(name_list, list):
                for entry in name_list:
                    if entry.get("Locale", "") == "en":
                        return entry.get("Description", "")
            elif isinstance(name_list, dict):
                return name_list.get("Description", "")
        # Fallback: Abbreviation
        return team_obj.get("Abbreviation", "")

    @staticmethod
    def _extract_description(obj: dict | list, field: str = "") -> str:
        """Extract a localized description string from FIFA's response objects."""
        if not obj:
            return ""
        target = obj.get(field, []) if field else obj
        if isinstance(target, list):
            for entry in target:
                if entry.get("Locale", "") == "en":
                    return entry.get("Description", "")
        elif isinstance(target, dict):
            return target.get("Description", "")
        elif isinstance(target, str):
            return target
        return ""

    @staticmethod
    def _parse_fifa_date(date_str: str) -> datetime | None:
        """Parse FIFA's date format which can be '/Date(millis)/' or ISO 8601."""
        if not date_str:
            return None

        # Handle /Date(1718100000000)/ format
        if date_str.startswith("/Date(") and date_str.endswith(")/"):
            try:
                millis_str = date_str[6:-2]
                # Handle timezone offset in millis string e.g. /Date(1718100000000+0000)/
                if "+" in millis_str:
                    millis_str = millis_str.split("+")[0]
                elif "-" in millis_str and millis_str.count("-") > 0:
                    millis_str = millis_str.rsplit("-", 1)[0]
                millis = int(millis_str)
                return datetime.utcfromtimestamp(millis / 1000)
            except (ValueError, OverflowError):
                return None

        # Handle ISO 8601
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return None
