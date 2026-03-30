"""Dynamic source reliability scoring based on historical accuracy data.

Replaces hardcoded SOURCE_PRIORITY values with data-driven scores computed
from the source_accuracy_records table (populated by reconciliation).
"""

import math
from datetime import datetime, timedelta

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.cache.client import RedisClient
from sportshub.db.tables import source_accuracy_records_table

logger = structlog.get_logger()

# Hardcoded fallback priorities (same as matcher.py SOURCE_PRIORITY)
FALLBACK_PRIORITIES: dict[str, dict[str, float]] = {
    "nba": {
        "nbacom_cdn": 10.0,
        "espn_nba": 8.0,
        "balldontlie_nba": 5.0,
    },
    "lol": {
        "lolesports": 10.0,
        "pandascore_lol": 8.0,
        "liquipedia_lol": 5.0,
    },
    "football": {
        "fifa_api": 10.0,
        "espn_fifa": 8.0,
        "footballdata_wc": 5.0,
    },
}

# All known sources across sports
ALL_SOURCES: dict[str, list[str]] = {
    "nba": ["nbacom_cdn", "espn_nba", "balldontlie_nba"],
    "lol": ["lolesports", "pandascore_lol", "liquipedia_lol"],
    "football": ["fifa_api", "espn_fifa", "footballdata_wc"],
}

# Exponential decay parameters
HALF_LIFE_DAYS = 14.0
DECAY_LAMBDA = math.log(2) / HALF_LIFE_DAYS

# Cache TTL in seconds (1 hour)
CACHE_TTL = 3600

# Minimum samples before trusting dynamic scores
DEFAULT_MIN_SAMPLES = 10

# Scale factor: dynamic accuracy (0.0-1.0) gets mapped to priority scale (0-10)
PRIORITY_SCALE = 10.0


class DynamicReliabilityScorer:
    """Computes source reliability scores from historical accuracy data.

    Scores are based on exponential-decay-weighted accuracy rates from
    the source_accuracy_records table. When insufficient data is available
    (<min_samples records in the last 30 days), falls back to hardcoded
    priorities.
    """

    def __init__(
        self,
        session: AsyncSession,
        cache: RedisClient | None = None,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        lookback_days: int = 30,
    ) -> None:
        self._session = session
        self._cache = cache
        self._min_samples = min_samples
        self._lookback_days = lookback_days

    async def get_source_priority(
        self,
        source_id: str,
        sport: str,
        field: str | None = None,
    ) -> float:
        """Get the reliability priority score for a source.

        Args:
            source_id: The source adapter identifier.
            sport: Sport key (nba, lol, football).
            field: Optional field name (time, teams, venue) for field-specific
                   scoring. If None, returns overall accuracy.

        Returns:
            Priority score on a 0-10 scale.
        """
        cache_key = f"reliability:{source_id}:{sport}:{field or 'overall'}"

        # Try cache first
        if self._cache:
            try:
                cached = await self._cache.get_json(cache_key)
                if cached is not None:
                    return float(cached)
            except Exception:
                logger.debug("reliability_cache_read_failed", key=cache_key)

        # Compute from database
        score = await self._compute_score(source_id, sport, field)

        # Cache the result
        if self._cache:
            try:
                await self._cache.set_json(cache_key, score, ttl=CACHE_TTL)
            except Exception:
                logger.debug("reliability_cache_write_failed", key=cache_key)

        return score

    async def get_field_priority(
        self,
        source_id: str,
        sport: str,
        field: str,
    ) -> float:
        """Get field-specific reliability score.

        Args:
            source_id: The source adapter identifier.
            sport: Sport key.
            field: One of 'time', 'teams', 'venue'.

        Returns:
            Priority score on a 0-10 scale.
        """
        if field not in ("time", "teams", "venue"):
            raise ValueError(f"Invalid field: {field}. Must be 'time', 'teams', or 'venue'.")
        return await self.get_source_priority(source_id, sport, field=field)

    async def get_all_priorities(self, sport: str) -> dict[str, float]:
        """Get priority map for all sources for a given sport.

        Args:
            sport: Sport key (nba, lol, football).

        Returns:
            Dict mapping source_id to priority score (0-10 scale).
        """
        sources = ALL_SOURCES.get(sport, [])
        fallback = FALLBACK_PRIORITIES.get(sport, {})

        priority_map: dict[str, float] = {}
        for source_id in sources:
            try:
                score = await self.get_source_priority(source_id, sport)
                priority_map[source_id] = score
            except Exception:
                logger.warning(
                    "reliability_score_failed",
                    source_id=source_id,
                    sport=sport,
                )
                priority_map[source_id] = fallback.get(source_id, 0.0)

        return priority_map

    async def get_detailed_reliability(self, sport: str | None = None) -> list[dict]:
        """Get detailed reliability info for all sources, suitable for dashboard display.

        Returns a list of dicts with dynamic score, fallback score, per-field
        breakdown, sample count, and whether dynamic or fallback is in use.
        """
        sports = [sport] if sport else list(ALL_SOURCES.keys())
        results: list[dict] = []

        for sp in sports:
            sources = ALL_SOURCES.get(sp, [])
            fallback = FALLBACK_PRIORITIES.get(sp, {})

            for source_id in sources:
                raw = await self._fetch_accuracy_records(source_id, sp)
                sample_count = len(raw)
                using_dynamic = sample_count >= self._min_samples

                # Overall dynamic score
                if using_dynamic:
                    overall_accuracy = self._weighted_accuracy(raw, field=None)
                    dynamic_score = round(overall_accuracy * PRIORITY_SCALE, 2)
                else:
                    dynamic_score = None

                # Per-field breakdown
                field_breakdown = {}
                for field in ("time", "teams", "venue"):
                    if using_dynamic:
                        acc = self._weighted_accuracy(raw, field=field)
                        field_breakdown[field] = {
                            "accuracy": round(acc, 4),
                            "score": round(acc * PRIORITY_SCALE, 2),
                        }
                    else:
                        field_breakdown[field] = {
                            "accuracy": None,
                            "score": None,
                        }

                results.append({
                    "source_id": source_id,
                    "sport": sp,
                    "sample_count": sample_count,
                    "min_samples": self._min_samples,
                    "using_dynamic": using_dynamic,
                    "dynamic_score": dynamic_score,
                    "fallback_score": fallback.get(source_id, 0.0),
                    "active_score": dynamic_score if using_dynamic else fallback.get(source_id, 0.0),
                    "field_breakdown": field_breakdown,
                })

        return results

    async def precompute_and_cache(self) -> dict[str, dict[str, float]]:
        """Pre-compute and cache all reliability scores. Used by the scheduled job.

        Returns:
            Nested dict: {sport: {source_id: score}}.
        """
        all_scores: dict[str, dict[str, float]] = {}

        for sport, sources in ALL_SOURCES.items():
            sport_scores: dict[str, float] = {}
            for source_id in sources:
                try:
                    score = await self._compute_score(source_id, sport, field=None)
                    sport_scores[source_id] = score

                    # Cache overall score
                    cache_key = f"reliability:{source_id}:{sport}:overall"
                    if self._cache:
                        await self._cache.set_json(cache_key, score, ttl=CACHE_TTL)

                    # Cache per-field scores
                    for field in ("time", "teams", "venue"):
                        field_score = await self._compute_score(source_id, sport, field=field)
                        field_key = f"reliability:{source_id}:{sport}:{field}"
                        if self._cache:
                            await self._cache.set_json(field_key, field_score, ttl=CACHE_TTL)

                except Exception:
                    logger.exception(
                        "reliability_precompute_error",
                        source_id=source_id,
                        sport=sport,
                    )

            all_scores[sport] = sport_scores

        return all_scores

    async def _compute_score(
        self,
        source_id: str,
        sport: str,
        field: str | None,
    ) -> float:
        """Compute the reliability score from database records.

        Uses exponential decay weighting so recent results count more.
        Falls back to hardcoded priority when insufficient data.
        """
        records = await self._fetch_accuracy_records(source_id, sport)

        if len(records) < self._min_samples:
            fallback = FALLBACK_PRIORITIES.get(sport, {}).get(source_id, 0.0)
            logger.debug(
                "reliability_fallback",
                source_id=source_id,
                sport=sport,
                sample_count=len(records),
                min_samples=self._min_samples,
                fallback_score=fallback,
            )
            return fallback

        accuracy = self._weighted_accuracy(records, field=field)
        return round(accuracy * PRIORITY_SCALE, 2)

    async def _fetch_accuracy_records(
        self,
        source_id: str,
        sport: str,
    ) -> list[dict]:
        """Fetch raw accuracy records from the database."""
        sar = source_accuracy_records_table
        since = datetime.utcnow() - timedelta(days=self._lookback_days)

        stmt = (
            sa.select(
                sar.c.time_accurate,
                sar.c.teams_correct,
                sar.c.venue_correct,
                sar.c.recorded_at,
            )
            .where(
                sa.and_(
                    sar.c.source_id == source_id,
                    sar.c.sport == sport,
                    sar.c.recorded_at >= since,
                )
            )
            .order_by(sar.c.recorded_at.desc())
        )

        try:
            result = await self._session.execute(stmt)
            rows = result.all()
        except Exception:
            logger.exception(
                "reliability_db_query_failed",
                source_id=source_id,
                sport=sport,
            )
            return []

        now = datetime.utcnow()
        records = []
        for row in rows:
            recorded_at = row.recorded_at
            # Handle timezone-aware datetimes
            if recorded_at.tzinfo is not None:
                recorded_at = recorded_at.replace(tzinfo=None)
            age_days = max((now - recorded_at).total_seconds() / 86400.0, 0.0)
            records.append({
                "time_accurate": row.time_accurate,
                "teams_correct": row.teams_correct,
                "venue_correct": row.venue_correct,
                "age_days": age_days,
            })

        return records

    @staticmethod
    def _weighted_accuracy(records: list[dict], field: str | None) -> float:
        """Compute exponential-decay-weighted accuracy rate.

        Args:
            records: List of accuracy record dicts with age_days and field booleans.
            field: 'time', 'teams', 'venue', or None for overall.

        Returns:
            Weighted accuracy rate between 0.0 and 1.0.
        """
        if not records:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for rec in records:
            weight = math.exp(-DECAY_LAMBDA * rec["age_days"])

            if field is None:
                # Overall: average of all three fields
                correct_count = sum([
                    rec["time_accurate"],
                    rec["teams_correct"],
                    rec["venue_correct"],
                ])
                accuracy = correct_count / 3.0
            elif field == "time":
                accuracy = 1.0 if rec["time_accurate"] else 0.0
            elif field == "teams":
                accuracy = 1.0 if rec["teams_correct"] else 0.0
            elif field == "venue":
                accuracy = 1.0 if rec["venue_correct"] else 0.0
            else:
                accuracy = 0.0

            weighted_sum += weight * accuracy
            total_weight += weight

        if total_weight == 0.0:
            return 0.0

        return weighted_sum / total_weight
