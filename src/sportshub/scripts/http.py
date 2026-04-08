"""Shared HTTP client for data-fetching scripts.

Provides a single fetch_json() implementation and a rate-limiting client
that reads configuration from the ProviderRegistry.

Usage::

    from sportshub.scripts.http import ScriptHttpClient

    client = ScriptHttpClient("espn_nba")
    data = client.get("/teams/9/roster")
    # Automatically applies rate limiting and headers from providers.json
"""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import structlog

from sportshub.providers.registry import ProviderRegistry

logger = structlog.get_logger()

# Default headers for browser-like requests
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict:
    """Fetch JSON from a URL. Single canonical implementation.

    Args:
        url: The URL to fetch.
        headers: Optional headers dict. Merged with DEFAULT_HEADERS.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON as a dict.

    Raises:
        HTTPError: On HTTP error responses.
        URLError: On connection errors.
        json.JSONDecodeError: On invalid JSON.
    """
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    req = Request(url, headers=merged_headers)
    with urlopen(req, timeout=int(timeout)) as resp:
        return json.loads(resp.read())


class ScriptHttpClient:
    """HTTP client for scripts that reads config from the ProviderRegistry.

    Automatically applies rate limiting and builds endpoint URLs
    from the provider's configuration in data/providers.json.

    Usage::

        client = ScriptHttpClient("espn_nba")
        data = client.get_endpoint("roster", team_id="9")
        # Waits rate_limit_seconds between requests automatically
    """

    def __init__(
        self,
        source_id: str,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._registry = registry or ProviderRegistry()
        self._config = self._registry.get_provider(source_id)
        if not self._config:
            raise ValueError(f"Unknown provider: {source_id}")

        self._source_id = source_id
        self._last_request_time: float = 0.0
        self._request_count = 0

        # Build headers from config
        self._headers = dict(DEFAULT_HEADERS)
        if self._config.headers_required:
            self._headers.update(self._config.headers_required)

    @property
    def rate_limit_seconds(self) -> float:
        """Rate limit in seconds from provider config, or 1.0 as default."""
        return self._config.rate_limit_seconds or 1.0

    @property
    def base_url(self) -> str | None:
        """Provider's base URL."""
        return self._config.base_url

    def _rate_limit(self) -> None:
        """Sleep if needed to respect the configured rate limit."""
        if self._request_count == 0:
            self._request_count += 1
            self._last_request_time = time.monotonic()
            return

        elapsed = time.monotonic() - self._last_request_time
        wait = self.rate_limit_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

        self._last_request_time = time.monotonic()
        self._request_count += 1

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Fetch JSON from a URL with rate limiting.

        Args:
            url: Full URL to fetch.
            headers: Additional headers (merged with provider defaults).
            timeout: Override timeout (uses provider default or 30s).
        """
        self._rate_limit()
        merged = {**self._headers, **(headers or {})}
        t = timeout or 30.0
        return fetch_json(url, headers=merged, timeout=t)

    def get_endpoint(
        self,
        endpoint_name: str,
        timeout: float | None = None,
        **kwargs: str,
    ) -> dict:
        """Fetch JSON from a named endpoint in the provider config.

        Builds the URL from providers.json endpoints + base_url, then fetches.

        Args:
            endpoint_name: Key in the provider's endpoints dict.
            timeout: Override timeout.
            **kwargs: Template variables for the URL (e.g., team_id="9").
        """
        url = self._registry.get_endpoint(self._source_id, endpoint_name, **kwargs)
        if not url:
            raise ValueError(
                f"Endpoint '{endpoint_name}' not found for provider '{self._source_id}'"
            )
        return self.get(url, timeout=timeout)

    def translate_abbreviation(self, abbr: str) -> str:
        """Translate a provider abbreviation to our canonical format."""
        return self._registry.translate_abbreviation(abbr, from_source=self._source_id)

    def standard_to_provider(self, abbr: str) -> str:
        """Translate our canonical abbreviation to this provider's format."""
        return self._registry.translate_abbreviation(
            abbr, from_source=self._source_id, to_source=self._source_id
        )
