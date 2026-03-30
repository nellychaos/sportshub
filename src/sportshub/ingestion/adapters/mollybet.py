"""Mollybet ingestion adapter.

Fetches upcoming events from the Mollybet multi-bookie aggregator for a single sport.
Three instances are registered (fb → FOOTBALL, basket → NBA, esports → LOL).

Competition IDs are pre-seeded in data/mollybet_competitions.json — run
scripts/discover_mollybet_competitions.py once to populate real IDs.

Mollybet API docs: https://api.mollybet.com/docs/api/contents/
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog

from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport
from sportshub.models.source import RawEvent, SourceReliability

logger = structlog.get_logger()

_COMPETITIONS_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "data"
    / "mollybet_competitions.json"
)

# Fallback window when no cached competitions are available
_FETCH_WINDOW_DAYS = 14


def _load_competition_ids(mollybet_sport: str) -> list[dict]:
    """Return seeded competition entries for this sport (only those with an ID)."""
    try:
        data = json.loads(_COMPETITIONS_FILE.read_text())
        return [
            e for e in data
            if e.get("mollybet_sport") == mollybet_sport
            and e.get("mollybet_competition_id")
        ]
    except Exception as exc:
        logger.warning("mollybet_competitions_load_failed", error=str(exc))
        return []


def _parse_kickoff(raw: Any) -> datetime | None:
    """Parse Mollybet kickoff time to an aware UTC datetime."""
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        # ISO 8601 string
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class MollybetAdapter(SourceAdapter):
    """Ingestion adapter for one Mollybet sport (fb / basket / esports)."""

    def __init__(
        self,
        username: str,
        password: str,
        sport: Sport,
        mollybet_sport: str,
        api_url: str = "https://api.mollybet.com",
    ) -> None:
        super().__init__()
        self._username = username
        self._password = password
        self._sport = sport
        self._mollybet_sport = mollybet_sport
        self._api_url = api_url.rstrip("/")

        self._session_token: str | None = None
        self._session_expires_at: datetime | None = None

    # ── SourceAdapter properties ──────────────────────────────────────────

    @property
    def source_id(self) -> str:
        return f"mollybet_{self._mollybet_sport}"

    @property
    def sport(self) -> Sport:
        return self._sport

    @property
    def reliability(self) -> SourceReliability:
        return SourceReliability.ESTABLISHED  # multi-bookie verified

    @property
    def source_timezone(self) -> str:
        return "UTC"

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            timeout=30.0,
            headers={"User-Agent": "Sportshub/0.1"},
            follow_redirects=True,
        )

    # ── Auth ─────────────────────────────────────────────────────────────

    def _session_expired(self) -> bool:
        if self._session_expires_at is None:
            return True
        return datetime.utcnow() >= self._session_expires_at

    async def _ensure_session(self) -> bool:
        """Authenticate (or re-authenticate) with Mollybet. Returns True on success."""
        if self._session_token and not self._session_expired():
            return True

        if not self._client:
            await self.initialize()

        try:
            resp = await self._client.post(  # type: ignore[union-attr]
                "/v1/sessions/",
                json={"username": self._username, "password": self._password},
            )
            resp.raise_for_status()
            data = resp.json()
            self._session_token = data.get("session_id") or data.get("token")
            # Session is valid 24 h; refresh after 23 h to be safe
            self._session_expires_at = datetime.utcnow() + timedelta(hours=23)
            logger.info(
                "mollybet_session_created",
                source_id=self.source_id,
                expires_in_hours=23,
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.error(
                "mollybet_auth_failed",
                source_id=self.source_id,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            self._session_token = None
            return False
        except Exception as exc:
            logger.error("mollybet_auth_error", source_id=self.source_id, error=str(exc))
            self._session_token = None
            return False

    def _auth_headers(self) -> dict[str, str]:
        return {"Session": self._session_token or ""}

    # ── Fetch ─────────────────────────────────────────────────────────────

    async def fetch_upcoming(self) -> list[RawEvent]:
        try:
            if not await self._ensure_session():
                self._record_failure("authentication failed")
                return []

            competitions = _load_competition_ids(self._mollybet_sport)

            if not competitions:
                logger.warning(
                    "mollybet_no_competitions_seeded",
                    source_id=self.source_id,
                    hint="Run scripts/discover_mollybet_competitions.py to populate IDs",
                )
                self._record_success()
                return []

            now = datetime.utcnow()
            date_from = now.strftime("%Y-%m-%d")
            date_to = (now + timedelta(days=_FETCH_WINDOW_DAYS)).strftime("%Y-%m-%d")

            raw_events: list[RawEvent] = []
            seen_event_ids: set[str] = set()

            for comp_entry in competitions:
                comp_id = comp_entry["mollybet_competition_id"]
                short_name = comp_entry.get("sportshub_short_name", comp_id)

                try:
                    events = await self._fetch_competition_events(
                        comp_id, date_from, date_to
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 401:
                        # Token expired — refresh and retry once
                        self._session_token = None
                        if not await self._ensure_session():
                            break
                        events = await self._fetch_competition_events(
                            comp_id, date_from, date_to
                        )
                    else:
                        logger.warning(
                            "mollybet_competition_fetch_failed",
                            source_id=self.source_id,
                            competition_id=comp_id,
                            status=exc.response.status_code,
                        )
                        continue

                for ev in events:
                    event_id = str(ev.get("event_id") or ev.get("id") or "")
                    if not event_id or event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event_id)

                    home_team = ev.get("home_team") or ev.get("home") or ""
                    away_team = ev.get("away_team") or ev.get("away") or ""
                    kickoff = _parse_kickoff(
                        ev.get("kickoff_time") or ev.get("start_time") or ev.get("event_start")
                    )

                    if not home_team or not away_team or kickoff is None:
                        continue

                    # Normalise to naive UTC for pipeline compatibility
                    scheduled_at = kickoff.replace(tzinfo=None)

                    raw_events.append(RawEvent(
                        source_id=self.source_id,
                        source_event_id=event_id,
                        sport=self._sport,
                        raw_home_team=home_team,
                        raw_away_team=away_team,
                        raw_competition=short_name,
                        scheduled_at=scheduled_at,
                        venue=None,
                        source_timezone="UTC",
                        raw_metadata={
                            "mollybet_event_id": event_id,
                            "mollybet_competition_id": comp_id,
                            "mollybet_sport": self._mollybet_sport,
                            "competition_country": comp_entry.get("country", ""),
                            "raw": ev,
                        },
                    ))

            self._record_success()
            logger.info(
                "mollybet_fetch_complete",
                source_id=self.source_id,
                event_count=len(raw_events),
                competitions_checked=len(competitions),
            )
            return raw_events

        except Exception as exc:
            self._record_failure(str(exc))
            logger.error(
                "mollybet_fetch_error",
                source_id=self.source_id,
                error=str(exc),
            )
            return []

    async def _fetch_competition_events(
        self, competition_id: str, date_from: str, date_to: str
    ) -> list[dict]:
        """GET /v1/orders/filters/events/ for one competition, with retry on token expiry."""
        resp = await self._client.get(  # type: ignore[union-attr]
            "/v1/orders/filters/events/",
            params={
                "competition_id": competition_id,
                "event_start_from": date_from,
                "event_start_to": date_to,
            },
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        # Mollybet returns a list directly or wrapped in a key
        if isinstance(data, list):
            return data
        return data.get("events") or data.get("results") or []
