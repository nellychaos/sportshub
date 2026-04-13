"""SourceRecord and IngestionRun repository."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories.base import BaseRepository
from sportshub.db.tables import ingestion_runs_table, source_records_table
from sportshub.models import IngestionRun, SourceRecord, Sport


class SourceRecordRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def upsert(self, record: SourceRecord) -> SourceRecord:
        """Insert or update a source record (ON CONFLICT by source_id + source_event_id)."""
        stmt = pg_insert(source_records_table).values(
            id=record.id,
            event_id=record.event_id,
            source_id=record.source_id,
            source_event_id=record.source_event_id,
            sport=record.sport.value,
            raw_home_team=record.raw_home_team,
            raw_away_team=record.raw_away_team,
            raw_competition=record.raw_competition,
            scheduled_at=record.scheduled_at,
            venue=record.venue,
            raw_data=record.raw_data,
            match_confidence=record.match_confidence,
            ingestion_run_id=record.ingestion_run_id,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_source_records_source_event",
            set_={
                "scheduled_at": stmt.excluded.scheduled_at,
                "venue": stmt.excluded.venue,
                "raw_data": stmt.excluded.raw_data,
                "raw_home_team": stmt.excluded.raw_home_team,
                "raw_away_team": stmt.excluded.raw_away_team,
                "raw_competition": stmt.excluded.raw_competition,
                "ingestion_run_id": stmt.excluded.ingestion_run_id,
            },
        ).returning(source_records_table)
        row = await self._fetch_one(stmt)
        return SourceRecord(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def get_by_event(self, event_id: UUID) -> list[SourceRecord]:
        stmt = sa.select(source_records_table).where(
            source_records_table.c.event_id == event_id
        )
        rows = await self._fetch_all(stmt)
        return [SourceRecord(**self._row_to_dict(r)) for r in rows]

    async def get_by_source_event(self, source_id: str, source_event_id: str) -> SourceRecord | None:
        """Look up a source record by its source-specific identifiers."""
        stmt = sa.select(source_records_table).where(
            sa.and_(
                source_records_table.c.source_id == source_id,
                source_records_table.c.source_event_id == source_event_id,
            )
        )
        row = await self._fetch_one(stmt)
        if row is None:
            return None
        return SourceRecord(**self._row_to_dict(row))

    async def get_unmatched(self, sport: Sport | None = None) -> list[SourceRecord]:
        """Get source records not yet linked to a canonical event."""
        stmt = sa.select(source_records_table).where(
            source_records_table.c.event_id.is_(None)
        )
        if sport:
            stmt = stmt.where(source_records_table.c.sport == sport.value)
        stmt = stmt.order_by(source_records_table.c.created_at.asc())
        rows = await self._fetch_all(stmt)
        return [SourceRecord(**self._row_to_dict(r)) for r in rows]

    async def update_event_link(
        self, record_id: UUID, event_id: UUID, confidence: float
    ) -> None:
        """Link a source record to a canonical event after resolution."""
        stmt = (
            sa.update(source_records_table)
            .where(source_records_table.c.id == record_id)
            .values(event_id=event_id, match_confidence=confidence)
        )
        await self._execute(stmt)

    # ─── Ingestion Runs ───────────────────────────────────────────────────

    async def create_ingestion_run(self, run: IngestionRun) -> IngestionRun:
        stmt = sa.insert(ingestion_runs_table).values(
            id=run.id,
            source_id=run.source_id,
            started_at=run.started_at,
            status=run.status,
            metadata=run.metadata,
        ).returning(ingestion_runs_table)
        row = await self._fetch_one(stmt)
        return IngestionRun(**self._row_to_dict(row))  # type: ignore[arg-type]

    async def complete_ingestion_run(
        self,
        run_id: UUID,
        status: str,
        records_fetched: int = 0,
        records_new: int = 0,
        records_updated: int = 0,
        records_matched: int = 0,
        error_message: str | None = None,
    ) -> None:
        stmt = (
            sa.update(ingestion_runs_table)
            .where(ingestion_runs_table.c.id == run_id)
            .values(
                completed_at=datetime.utcnow(),
                status=status,
                records_fetched=records_fetched,
                records_new=records_new,
                records_updated=records_updated,
                records_matched=records_matched,
                error_message=error_message,
            )
        )
        await self._execute(stmt)

    async def get_latest_runs(
        self, source_id: str | None = None, limit: int = 10
    ) -> list[IngestionRun]:
        stmt = sa.select(ingestion_runs_table).order_by(
            ingestion_runs_table.c.started_at.desc()
        )
        if source_id:
            stmt = stmt.where(ingestion_runs_table.c.source_id == source_id)
        stmt = stmt.limit(limit)
        rows = await self._fetch_all(stmt)
        return [IngestionRun(**self._row_to_dict(r)) for r in rows]
