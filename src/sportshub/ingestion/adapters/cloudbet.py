"""Cloudbet ingestion adapter.

Fetches upcoming events and odds from the Cloudbet Feed API.
Three instances are registered (basketball -> NBA, soccer -> FOOTBALL,
league-of-legends -> LOL).

Uses the public REST API:
  GET /v2/odds/events?sport={sport_key}
  Auth via X-API-Key header (free affiliate key, no deposit required).

Cloudbet API docs: https://www.cloudbet.com/api/
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

# Maps our Sport enum to Cloudbet sport keys
SPORT_KEY_MAP: dict[Sport, str] = {
    Sport.NBA: "basketball",
    Sport.FOOTBALL: "soccer",
    Sport.LOL: "league-of-legends",
}

# Markets to request per sport.
# Cloudbet uses "1x2" (3-way) not "moneyline" for basketball pre-match.
SPORT_MARKETS: dict[Sport, list[str]] = {
    Sport.NBA: [
        "basketball.1x2",
        "basketball.moneyline",
        "basketball.handicap",
        "basketball.totals",
        "basketball.player_points",
        "basketball.player_rebounds",
        "basketball.player_assists",
        "basketball.player_three_point_field_goals",
    ],
    Sport.FOOTBALL: [
        "soccer.match_odds",
        "soccer.asian_handicap",
        "soccer.total_goals",
        "soccer.both_teams_to_score",
        "soccer.correct_score",
    ],
    Sport.LOL: [
        "league_of_legends.winner",
        "league_of_legends.handicap",
        "league_of_legends.totals",
    ],
}

# Competitions we care about (filter out minor leagues)
NBA_COMPETITIONS = {"nba", "nba-playoffs", "nba-finals"}
FOOTBALL_COMPETITIONS = {"fifa-world-cup", "world-cup"}
LOL_COMPETITIONS = {"lol-worlds", "lck", "lpl", "lec", "lcs", "msi"}


def _parse_cloudbet_time(raw: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp from Cloudbet to naive UTC datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _parse_param(raw: Any) -> float | None:
    """Parse a Cloudbet selection param like 'handicap=-1.5' or '220.5' to float."""
    if raw is None:
        return None
    s = str(raw)
    # Strip key= prefix if present (e.g. "handicap=-1.5" -> "-1.5")
    if "=" in s:
        s = s.split("=", 1)[1]
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _extract_odds(markets: dict, sport: Sport) -> dict:
    """Pull key odds from Cloudbet market selections into a flat dict.

    Returns a dict like:
      {
        "moneyline": {"home": 1.95, "away": 1.85},
        "spread": {"home": {"line": -3.5, "price": 1.90}, ...},
        "total": {"over": {"line": 220.5, "price": 1.90}, ...},
        "player_props": [...],
      }
    """
    odds: dict[str, Any] = {}

    for market_key, market_data in markets.items():
        if not isinstance(market_data, dict):
            continue

        submarkets = market_data.get("submarkets", {})
        if not submarkets:
            continue

        for sub_key, sub_data in submarkets.items():
            selections = sub_data.get("selections", [])
            if not selections:
                continue

            # Moneyline / Match Winner / 1x2
            if "moneyline" in market_key or "match_odds" in market_key or "winner" in market_key or "1x2" in market_key:
                ml: dict[str, float] = {}
                for sel in selections:
                    outcome = sel.get("outcome", "").lower()
                    price = sel.get("price")
                    if price and float(price) > 0 and outcome in ("home", "away", "draw", "one", "two"):
                        ml[outcome] = float(price)
                if ml:
                    odds["moneyline"] = ml

            # Spread / Handicap
            elif "handicap" in market_key:
                spread: dict[str, dict] = {}
                for sel in selections:
                    outcome = sel.get("outcome", "").lower()
                    price = sel.get("price")
                    if price and float(price) > 0 and outcome in ("home", "away", "one", "two"):
                        spread[outcome] = {
                            "line": _parse_param(sel.get("params")),
                            "price": float(price),
                        }
                if spread:
                    odds["spread"] = spread

            # Totals
            elif "total" in market_key:
                totals: dict[str, dict] = {}
                for sel in selections:
                    outcome = sel.get("outcome", "").lower()
                    price = sel.get("price")
                    if price and float(price) > 0 and outcome in ("over", "under"):
                        totals[outcome] = {
                            "line": _parse_param(sel.get("params")),
                            "price": float(price),
                        }
                if totals:
                    odds["total"] = totals

            # Player props
            elif "player_" in market_key:
                props = []
                for sel in selections:
                    price = sel.get("price")
                    if not price:
                        continue
                    props.append({
                        "market": market_key,
                        "player": sel.get("params", ""),
                        "outcome": sel.get("outcome", ""),
                        "price": float(price),
                        "min_stake": sel.get("minStake"),
                        "max_stake": sel.get("maxStake"),
                    })
                if props:
                    odds.setdefault("player_props", []).extend(props)

            # BTTS
            elif "both_teams_to_score" in market_key:
                btts: dict[str, float] = {}
                for sel in selections:
                    outcome = sel.get("outcome", "").lower()
                    price = sel.get("price")
                    if price and outcome in ("yes", "no"):
                        btts[outcome] = float(price)
                if btts:
                    odds["btts"] = btts

    return odds


class CloudbetAdapter(SourceAdapter):
    """Ingestion adapter for one Cloudbet sport.

    Fetches upcoming events via the Cloudbet Feed REST API with odds
    for the configured sport. Each sport gets its own adapter instance.
    """

    def __init__(
        self,
        api_key: str,
        sport: Sport,
        api_url: str = "https://sports-api.cloudbet.com/pub",
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._sport = sport
        self._api_url = api_url.rstrip("/")
        self._sport_key = SPORT_KEY_MAP[sport]

    # -- SourceAdapter properties ------------------------------------------

    @property
    def source_id(self) -> str:
        return f"cloudbet_{self._sport_key.replace('-', '_')}"

    @property
    def sport(self) -> Sport:
        return self._sport

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED

    @property
    def source_timezone(self) -> str:
        return "UTC"

    # -- Lifecycle ---------------------------------------------------------

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            timeout=30.0,
            headers={
                "X-API-Key": self._api_key,
                "Accept": "application/json",
                "User-Agent": "Sportshub/0.1",
            },
            follow_redirects=True,
        )

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- Fetch -------------------------------------------------------------

    async def fetch_upcoming(self) -> list[RawEvent]:
        try:
            events = await self._fetch_events()
            self._record_success()
            logger.info(
                "cloudbet_fetch_complete",
                source_id=self.source_id,
                event_count=len(events),
            )
            return events
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code}"
            if exc.response.status_code == 401:
                msg += " -- check SPORTSHUB_CLOUDBET_API_KEY"
            self._record_failure(msg)
            logger.error(
                "cloudbet_fetch_error",
                source_id=self.source_id,
                status=exc.response.status_code,
                body=exc.response.text[:300],
            )
            return []
        except Exception as exc:
            self._record_failure(str(exc))
            logger.error(
                "cloudbet_fetch_error",
                source_id=self.source_id,
                error=str(exc),
            )
            return []

    async def _fetch_events(self) -> list[RawEvent]:
        """GET /v2/odds/events?sport={key}&from={epoch}&to={epoch} with markets."""
        if not self._client:
            raise RuntimeError(f"Adapter {self.source_id} not initialized")

        markets = SPORT_MARKETS.get(self._sport, [])
        now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
        to_epoch = now_epoch + 14 * 86400  # 14 days ahead

        resp = await self._client.get(
            "/v2/odds/events",
            params=[("sport", self._sport_key),
                    ("from", str(now_epoch)),
                    ("to", str(to_epoch))]
            + [("markets", m) for m in markets],
        )
        resp.raise_for_status()
        data = resp.json()

        raw_events: list[RawEvent] = []
        now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        cutoff = now_utc + timedelta(days=14)

        competitions = data.get("competitions", [])
        if not competitions and isinstance(data, dict):
            # Flat event list format
            competitions = [{"events": data.get("events", [])}]

        for comp in competitions:
            comp_key = comp.get("key", "")
            comp_name = comp.get("name", "")
            comp_category = comp.get("category", {})
            comp_country = (
                comp_category.get("name", "")
                if isinstance(comp_category, dict)
                else ""
            )

            events = comp.get("events", [])
            for ev in events:
                event_id = str(ev.get("id", ""))
                if not event_id:
                    continue

                status = ev.get("status", "")
                # Only pre-match and trading events
                if status not in ("PRE_TRADING", "TRADING", ""):
                    continue

                # Parse teams from event name ("Home vs Away")
                event_name = ev.get("name", "")
                home_team = ev.get("home", {}).get("name", "")
                away_team = ev.get("away", {}).get("name", "")

                if not home_team or not away_team:
                    # Fallback: parse from event name
                    if " vs " in event_name:
                        parts = event_name.split(" vs ", 1)
                        home_team = parts[0].strip()
                        away_team = parts[1].strip()
                    elif " v " in event_name:
                        parts = event_name.split(" v ", 1)
                        home_team = parts[0].strip()
                        away_team = parts[1].strip()
                    else:
                        continue

                scheduled_at = _parse_cloudbet_time(ev.get("cutoffTime"))
                if scheduled_at is None:
                    continue

                # Only upcoming events within 14 days
                if scheduled_at < now_utc - timedelta(hours=3):
                    continue
                if scheduled_at > cutoff:
                    continue

                # Extract odds from markets
                markets_data = ev.get("markets", {})
                odds = _extract_odds(markets_data, self._sport) if markets_data else {}

                raw_events.append(
                    RawEvent(
                        source_id=self.source_id,
                        source_event_id=event_id,
                        sport=self._sport,
                        raw_home_team=home_team,
                        raw_away_team=away_team,
                        raw_competition=comp_name or comp_key,
                        scheduled_at=scheduled_at,
                        venue=None,
                        source_timezone="UTC",
                        raw_metadata={
                            "cloudbet_event_id": event_id,
                            "cloudbet_competition_key": comp_key,
                            "cloudbet_competition_name": comp_name,
                            "cloudbet_competition_country": comp_country,
                            "cloudbet_sport_key": self._sport_key,
                            "cloudbet_status": status,
                            "event_name": event_name,
                            "odds": odds,
                        },
                    )
                )

        return raw_events
