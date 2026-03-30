"""Repository for persisting reconciliation reports and querying accuracy stats."""

import uuid
from datetime import datetime, timedelta

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.tables import reconciliation_results_table, source_accuracy_records_table
from sportshub.reconciliation.reconciler import ReconciliationReport

logger = structlog.get_logger()


class ReconciliationRepository:
    """CRUD operations for reconciliation_results and source_accuracy_records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_report(self, report: ReconciliationReport) -> uuid.UUID:
        """Persist a reconciliation report and its per-source accuracy records.

        Uses upsert on event_id to handle re-reconciliation of the same event.
        Returns the reconciliation_results row id.
        """
        rr = reconciliation_results_table
        sar = source_accuracy_records_table

        recon_id = uuid.uuid4()
        meta = {
            "per_source": report.per_source_accuracy,
        }

        insert_stmt = pg_insert(rr).values(
            id=recon_id,
            event_id=uuid.UUID(report.event_id),
            event_occurred=report.event_occurred,
            time_diff_seconds=report.time_diff_seconds,
            teams_correct=report.teams_correct,
            venue_correct=report.venue_correct,
            reconciled_at=report.reconciled_at,
            metadata=meta,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_reconciliation_results_event_id",
            set_={
                "event_occurred": insert_stmt.excluded.event_occurred,
                "time_diff_seconds": insert_stmt.excluded.time_diff_seconds,
                "teams_correct": insert_stmt.excluded.teams_correct,
                "venue_correct": insert_stmt.excluded.venue_correct,
                "reconciled_at": insert_stmt.excluded.reconciled_at,
                "metadata": insert_stmt.excluded.metadata,
            },
        )
        await self._session.execute(upsert_stmt)

        # Retrieve the actual id (may differ if upserted)
        result = await self._session.execute(
            sa.select(rr.c.id).where(rr.c.event_id == uuid.UUID(report.event_id))
        )
        recon_id = result.scalar_one()

        # Delete existing source accuracy records for this reconciliation (idempotent)
        await self._session.execute(
            sa.delete(sar).where(sar.c.reconciliation_id == recon_id)
        )

        # Insert per-source accuracy records
        for ps in report.per_source_accuracy:
            await self._session.execute(
                sa.insert(sar).values(
                    id=uuid.uuid4(),
                    reconciliation_id=recon_id,
                    source_id=ps["source_id"],
                    sport=ps.get("sport", "nba"),
                    time_diff_seconds=ps.get("time_diff_seconds"),
                    time_accurate=ps.get("time_accurate", True),
                    teams_correct=ps.get("teams_correct", True),
                    venue_correct=ps.get("venue_correct", False),
                    recorded_at=datetime.utcnow(),
                )
            )

        return recon_id

    async def get_accuracy_stats(
        self,
        source_id: str | None = None,
        sport: str | None = None,
        days: int = 30,
    ) -> dict:
        """Aggregate accuracy statistics, optionally filtered by source and sport.

        Returns dict with total, time_accuracy_rate, teams_accuracy_rate, venue_accuracy_rate.
        """
        sar = source_accuracy_records_table
        since = datetime.utcnow() - timedelta(days=days)

        conditions = [sar.c.recorded_at >= since]
        if source_id is not None:
            conditions.append(sar.c.source_id == source_id)
        if sport is not None:
            conditions.append(sar.c.sport == sport)

        where = sa.and_(*conditions) if len(conditions) > 1 else conditions[0]

        stmt = sa.select(
            sa.func.count().label("total"),
            sa.func.count().filter(sar.c.time_accurate.is_(True)).label("time_accurate_count"),
            sa.func.count().filter(sar.c.teams_correct.is_(True)).label("teams_correct_count"),
            sa.func.count().filter(sar.c.venue_correct.is_(True)).label("venue_correct_count"),
            sa.func.coalesce(sa.func.avg(sar.c.time_diff_seconds), 0).label("avg_time_diff"),
        ).where(where)

        result = await self._session.execute(stmt)
        row = result.first()

        if not row or row.total == 0:
            return {
                "total": 0,
                "time_accuracy_rate": 0.0,
                "teams_accuracy_rate": 0.0,
                "venue_accuracy_rate": 0.0,
                "avg_time_diff_seconds": 0.0,
            }

        return {
            "total": row.total,
            "time_accuracy_rate": round(row.time_accurate_count / row.total, 3),
            "teams_accuracy_rate": round(row.teams_correct_count / row.total, 3),
            "venue_accuracy_rate": round(row.venue_correct_count / row.total, 3),
            "avg_time_diff_seconds": round(float(row.avg_time_diff), 1),
        }

    async def get_recent_reports(self, limit: int = 20) -> list[dict]:
        """Return the most recent reconciliation reports."""
        rr = reconciliation_results_table

        stmt = (
            sa.select(rr)
            .order_by(rr.c.reconciled_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        reports = []
        for row in result.all():
            m = row._mapping
            reports.append({
                "id": str(m["id"]),
                "event_id": str(m["event_id"]),
                "event_occurred": m["event_occurred"],
                "time_diff_seconds": m["time_diff_seconds"],
                "teams_correct": m["teams_correct"],
                "venue_correct": m["venue_correct"],
                "reconciled_at": m["reconciled_at"],
                "metadata": m["metadata"],
            })
        return reports

    async def get_accuracy_by_source_and_sport(self, days: int = 30) -> list[dict]:
        """Aggregate accuracy stats grouped by source_id and sport."""
        sar = source_accuracy_records_table
        since = datetime.utcnow() - timedelta(days=days)

        stmt = (
            sa.select(
                sar.c.source_id,
                sar.c.sport,
                sa.func.count().label("total"),
                sa.func.count().filter(sar.c.time_accurate.is_(True)).label("time_accurate_count"),
                sa.func.count().filter(sar.c.teams_correct.is_(True)).label("teams_correct_count"),
                sa.func.count().filter(sar.c.venue_correct.is_(True)).label("venue_correct_count"),
                sa.func.coalesce(sa.func.avg(sar.c.time_diff_seconds), 0).label("avg_time_diff"),
            )
            .where(sar.c.recorded_at >= since)
            .group_by(sar.c.source_id, sar.c.sport)
            .order_by(sar.c.source_id, sar.c.sport)
        )
        result = await self._session.execute(stmt)
        rows = []
        for r in result.all():
            total = r.total or 0
            rows.append({
                "source_id": r.source_id,
                "sport": r.sport,
                "total": total,
                "time_accuracy_rate": round(r.time_accurate_count / total, 3) if total else 0.0,
                "teams_accuracy_rate": round(r.teams_correct_count / total, 3) if total else 0.0,
                "venue_accuracy_rate": round(r.venue_correct_count / total, 3) if total else 0.0,
                "avg_time_diff_seconds": round(float(r.avg_time_diff), 1),
            })
        return rows
