"""Team name normalization and resolution."""

import re
import unicodedata
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.team_repo import TeamRepository
from sportshub.models.common import Sport

logger = structlog.get_logger()


def normalize_team_name(raw_name: str) -> str:
    """Reduce a team name to canonical form for matching.

    Steps:
    1. Lowercase and strip whitespace
    2. Remove accents (Unicode NFKD decomposition)
    3. Remove common suffixes (esports, gaming, team, club, fc, sc)
    4. Remove punctuation
    5. Normalize whitespace
    """
    name = raw_name.strip().lower()
    # Remove accents
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    # Remove common suffixes/prefixes
    name = re.sub(r"\b(esports?|gaming|team|club|fc|sc|national football team|national team|men'?s)\b", "", name)
    # Remove punctuation
    name = re.sub(r"[^\w\s]", "", name)
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()
    return name


class TeamResolver:
    """Resolves raw team names to canonical team UUIDs using a cached alias table.

    Resolution priority:
    1. Exact match in alias table for this specific source
    2. Exact match in alias table for any source (global)
    3. None (triggers manual review; fuzzy matching is done separately)
    """

    def __init__(self, alias_cache: dict[str, UUID]) -> None:
        self._cache = alias_cache

    @classmethod
    async def create(cls, session: AsyncSession, sport: Sport | None = None) -> "TeamResolver":
        """Factory: create resolver with alias cache loaded from database."""
        repo = TeamRepository(session)
        cache = await repo.get_alias_map(sport)
        logger.info("team_resolver_loaded", cache_size=len(cache), sport=sport)
        return cls(cache)

    def resolve(self, raw_name: str, source_id: str, sport: Sport) -> UUID | None:
        """Resolve a raw team name to a canonical team UUID.

        Returns None if no match found (the team is unrecognized).
        """
        normalized = normalize_team_name(raw_name)

        # 1. Source-specific match (highest confidence)
        source_key = f"{normalized}:{source_id}"
        if source_key in self._cache:
            return self._cache[source_key]

        # 2. Global match (any source)
        if normalized in self._cache:
            return self._cache[normalized]

        # 3. Try abbreviation-style match (e.g., "LAL", "T1")
        abbrev = raw_name.strip().upper()
        abbrev_key = abbrev.lower()
        if abbrev_key in self._cache:
            return self._cache[abbrev_key]

        logger.warning(
            "unresolved_team",
            raw_name=raw_name,
            normalized=normalized,
            source_id=source_id,
            sport=sport.value,
        )
        return None

    async def reload(self, session: AsyncSession, sport: Sport | None = None) -> None:
        """Refresh the alias cache from the database."""
        repo = TeamRepository(session)
        self._cache = await repo.get_alias_map(sport)
        logger.info("team_resolver_reloaded", cache_size=len(self._cache))
