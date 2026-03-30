"""LLM Resolution repository."""

import sqlalchemy as sa
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.base import BaseRepository
from sportshub.db.tables import llm_resolutions_table
from sportshub.models.llm_resolution import LLMResolution


class LLMResolutionRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, resolution: LLMResolution) -> LLMResolution:
        stmt = sa.insert(llm_resolutions_table).values(
            id=resolution.id,
            source_record_id=resolution.source_record_id,
            resolution_type=resolution.resolution_type,
            input_text=resolution.input_text,
            candidates=resolution.candidates,
            llm_output=resolution.llm_output,
            resolved_id=resolution.resolved_id,
            confidence=resolution.confidence,
            accepted=resolution.accepted,
            acceptance_reason=resolution.acceptance_reason,
            alias_created=resolution.alias_created,
            latency_ms=resolution.latency_ms,
            model=resolution.model,
            tokens_used=resolution.tokens_used,
        ).returning(llm_resolutions_table)
        row = await self._fetch_one(stmt)
        return LLMResolution(**self._row_to_dict(row))

    async def get_recent(self, limit: int = 20) -> list[LLMResolution]:
        stmt = (
            sa.select(llm_resolutions_table)
            .order_by(llm_resolutions_table.c.created_at.desc())
            .limit(limit)
        )
        rows = await self._fetch_all(stmt)
        return [LLMResolution(**self._row_to_dict(r)) for r in rows]

    async def get_stats_since(self, since: datetime) -> dict:
        """Aggregate stats for dashboard."""
        t = llm_resolutions_table
        stmt = sa.select(
            sa.func.count().label("total"),
            sa.func.count().filter(t.c.accepted == True).label("accepted"),  # noqa: E712
            sa.func.count().filter(t.c.accepted == False).label("rejected"),  # noqa: E712
            sa.func.count().filter(t.c.alias_created == True).label("aliases_created"),  # noqa: E712
            sa.func.coalesce(sa.func.sum(t.c.tokens_used), 0).label("total_tokens"),
            sa.func.coalesce(sa.func.avg(t.c.latency_ms), 0).label("avg_latency_ms"),
            sa.func.coalesce(
                sa.func.percentile_cont(0.95).within_group(t.c.latency_ms), 0
            ).label("p95_latency_ms"),
        ).where(t.c.created_at >= since)
        row = await self._fetch_one(stmt)
        if not row:
            return {
                "total": 0,
                "accepted": 0,
                "rejected": 0,
                "aliases_created": 0,
                "total_tokens": 0,
                "avg_latency_ms": 0,
                "p95_latency_ms": 0,
            }
        m = row._mapping
        return dict(m)

    async def get_confidence_distribution(self, since: datetime) -> list[dict]:
        """Bucket confidence scores for histogram."""
        t = llm_resolutions_table
        bucket = sa.case(
            (t.c.confidence < 0.5, "low"),
            (t.c.confidence < 0.7, "medium"),
            (t.c.confidence < 0.9, "high"),
            else_="very_high",
        ).label("bucket")
        stmt = (
            sa.select(bucket, sa.func.count().label("count"))
            .where(t.c.created_at >= since)
            .group_by(bucket)
        )
        rows = await self._fetch_all(stmt)
        return [{"bucket": r._mapping["bucket"], "count": r._mapping["count"]} for r in rows]

    async def get_daily_counts(self, since: datetime) -> list[dict]:
        """Daily accepted/rejected counts for trend chart."""
        t = llm_resolutions_table
        day = sa.func.date_trunc("day", t.c.created_at).label("day")
        stmt = (
            sa.select(
                day,
                sa.func.count().filter(t.c.accepted == True).label("accepted"),  # noqa: E712
                sa.func.count().filter(t.c.accepted == False).label("rejected"),  # noqa: E712
            )
            .where(t.c.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        rows = await self._fetch_all(stmt)
        return [
            {
                "day": str(r._mapping["day"]),
                "accepted": r._mapping["accepted"],
                "rejected": r._mapping["rejected"],
            }
            for r in rows
        ]
