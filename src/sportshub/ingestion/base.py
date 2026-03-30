"""Base source adapter interface and shared utilities."""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime

import httpx
import structlog

from sportshub.models.common import Sport
from sportshub.models.source import AdapterHealth, RawEvent, SourceReliability

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "Sportshub/0.1 (https://sportshub.dev; contact@sportshub.dev)"


class SourceAdapter(ABC):
    """Every data source implements this interface."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._health = AdapterHealth(
            is_healthy=True,
            last_success=None,
            last_error=None,
            consecutive_failures=0,
        )

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this source, e.g. 'espn_nba'."""

    @property
    @abstractmethod
    def sport(self) -> Sport:
        """Which sport this adapter covers."""

    @property
    @abstractmethod
    def reliability(self) -> SourceReliability:
        """OFFICIAL, ESTABLISHED, or COMMUNITY."""

    @property
    @abstractmethod
    def source_timezone(self) -> str:
        """IANA timezone the source reports datetimes in, e.g. 'UTC' or 'America/New_York'."""

    @abstractmethod
    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch upcoming events from this source. Returns list of RawEvent."""

    async def initialize(self) -> None:
        """Set up HTTP client and any auth. Called once at startup."""
        self._client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
        )

    async def shutdown(self) -> None:
        """Tear down resources."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch_result(self, source_event_id: str) -> dict | None:
        """Fetch final result for a completed event. Override if supported."""
        return None

    async def health_check(self) -> AdapterHealth:
        """Return current health status."""
        return self._health

    def _record_success(self) -> None:
        """Mark a successful fetch."""
        self._health.is_healthy = True
        self._health.last_success = datetime.utcnow()
        self._health.consecutive_failures = 0

    def _record_failure(self, error: str) -> None:
        """Mark a failed fetch."""
        self._health.last_error = error
        self._health.consecutive_failures += 1
        if self._health.consecutive_failures >= 5:
            self._health.is_healthy = False

    async def _rate_limit(self, seconds: float) -> None:
        """Simple rate limiter: sleep between requests."""
        await asyncio.sleep(seconds)

    async def _get_json(self, url: str, **kwargs) -> dict:
        """GET request returning JSON with error handling."""
        if not self._client:
            raise RuntimeError(f"Adapter {self.source_id} not initialized")
        response = await self._client.get(url, **kwargs)
        response.raise_for_status()
        return response.json()
