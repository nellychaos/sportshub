"""Venue-aware timezone normalization for incoming event datetimes.

Ensures all scheduled_at values are stored as UTC, resolving source-local
timezones and optionally inferring timezone from known venue locations.
"""

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from sportshub.models.common import Sport

logger = structlog.get_logger()

# Minimum similarity ratio for fuzzy venue matching
_VENUE_MATCH_THRESHOLD = 0.85

# Path to the venue timezone data file (relative to project root)
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
if not _DATA_DIR.is_dir():
    _DATA_DIR = Path("/app/data")
_DATA_FILE = _DATA_DIR / "venue_timezones.json"


class VenueTimezoneResolver:
    """Loads venue-to-IANA-timezone mappings and provides timezone resolution."""

    def __init__(self, data_path: Path | None = None) -> None:
        self._data_path = data_path or _DATA_FILE
        self._venues: dict[str, str] = {}
        self._venues_lower: dict[str, tuple[str, str]] = {}
        self._load()

    def _load(self) -> None:
        """Load venue timezone data from JSON file."""
        try:
            with open(self._data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._venues = data.get("venues", {})
            # Build a lowercase lookup for faster exact-insensitive matching
            self._venues_lower = {
                name.lower(): (name, tz) for name, tz in self._venues.items()
            }
            logger.info("venue_timezones_loaded", count=len(self._venues))
        except FileNotFoundError:
            logger.warning("venue_timezones_file_not_found", path=str(self._data_path))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("venue_timezones_load_error", error=str(e))

    def resolve_venue_timezone(self, venue: str | None, sport: Sport) -> str | None:
        """Resolve a venue string to an IANA timezone identifier.

        Performs exact case-insensitive lookup first, then falls back to fuzzy
        matching using SequenceMatcher. Returns None if no match is found above
        the similarity threshold.

        Args:
            venue: The venue name from the source data (may be None).
            sport: The sport enum, used for logging context.

        Returns:
            IANA timezone string (e.g. "America/New_York") or None.
        """
        if not venue:
            return None

        venue_stripped = venue.strip()
        venue_key = venue_stripped.lower()

        # 1. Exact case-insensitive match
        if venue_key in self._venues_lower:
            _, tz = self._venues_lower[venue_key]
            return tz

        # 2. Fuzzy match against all known venues
        best_ratio = 0.0
        best_tz: str | None = None
        best_venue: str | None = None

        for known_lower, (known_name, tz) in self._venues_lower.items():
            ratio = SequenceMatcher(None, venue_key, known_lower).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_tz = tz
                best_venue = known_name

        if best_ratio >= _VENUE_MATCH_THRESHOLD and best_tz is not None:
            logger.debug(
                "venue_fuzzy_match",
                input_venue=venue_stripped,
                matched_venue=best_venue,
                ratio=round(best_ratio, 3),
                timezone=best_tz,
                sport=sport.value,
            )
            return best_tz

        logger.debug(
            "venue_timezone_unresolved",
            venue=venue_stripped,
            sport=sport.value,
            best_ratio=round(best_ratio, 3) if best_ratio > 0 else None,
        )
        return None


def normalize_to_utc(dt: datetime, source_tz: str) -> datetime:
    """Ensure a datetime is in UTC.

    If the datetime is naive (no tzinfo), it is interpreted as being in the
    given source timezone, then converted to UTC. If the datetime is already
    timezone-aware, it is simply converted to UTC regardless of source_tz.

    Args:
        dt: The datetime to normalize.
        source_tz: IANA timezone string for the source (e.g. "America/New_York").

    Returns:
        A timezone-aware datetime in UTC.
    """
    if dt.tzinfo is not None:
        # Already aware — just convert to UTC
        return dt.astimezone(timezone.utc)

    # Naive datetime — localize to source timezone, then convert to UTC
    source_zone = ZoneInfo(source_tz)
    localized = dt.replace(tzinfo=source_zone)
    return localized.astimezone(timezone.utc)
