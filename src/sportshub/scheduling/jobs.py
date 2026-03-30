"""Individual job definitions for the scheduler."""

from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from sportshub.cache.client import RedisClient
from sportshub.cache.keys import invalidate_events
from sportshub.db.repositories import EventRepository, SourceRecordRepository
from sportshub.db.tables import (
    confirmation_checks_table,
    events_table,
    source_records_table,
    teams_table,
)
from sportshub.ingestion.base import SourceAdapter
from sportshub.ingestion.confirmation.registry import ConfirmationRegistry
from sportshub.ingestion.registry import AdapterRegistry
from sportshub.models import IngestionRun, SourceRecord
from sportshub.models.common import Sport
from sportshub.reconciliation.collector import ResultCollector
from sportshub.reconciliation.reconciler import EventReconciler
from sportshub.reconciliation.repository import ReconciliationRepository
from sportshub.resolution.normalizer import TeamResolver
from sportshub.resolution.pipeline import ResolutionPipeline
from sportshub.resolution.reliability import DynamicReliabilityScorer
from sportshub.scheduling.circuit_breaker import CircuitBreaker
from sportshub.validation.engine import ConstraintEngine

logger = structlog.get_logger()


async def run_ingestion_job(
    adapter: SourceAdapter,
    session_factory: async_sessionmaker[AsyncSession],
    circuit_breaker: CircuitBreaker,
    cache: RedisClient,
) -> None:
    """Run a single ingestion job for one adapter."""
    source_id = adapter.source_id

    # Check circuit breaker
    if circuit_breaker.is_open(source_id):
        logger.info("ingestion_skipped_circuit_open", source_id=source_id)
        return

    run = IngestionRun(
        source_id=source_id,
        started_at=datetime.utcnow(),
    )

    async with session_factory() as session:
        source_repo = SourceRecordRepository(session)
        run = await source_repo.create_ingestion_run(run)
        await session.commit()

    records_new = 0
    records_updated = 0

    try:
        raw_events = await adapter.fetch_upcoming()

        async with session_factory() as session:
            source_repo = SourceRecordRepository(session)

            for raw in raw_events:
                record = SourceRecord(
                    source_id=raw.source_id,
                    source_event_id=raw.source_event_id,
                    sport=raw.sport,
                    raw_home_team=raw.raw_home_team,
                    raw_away_team=raw.raw_away_team,
                    raw_competition=raw.raw_competition,
                    scheduled_at=raw.scheduled_at,
                    venue=raw.venue,
                    raw_data=raw.raw_metadata,
                    ingestion_run_id=run.id,
                )
                result = await source_repo.upsert(record)
                # Simple heuristic: if created_at is very recent, it's new
                if result.created_at and (datetime.utcnow() - result.created_at.replace(tzinfo=None)).seconds < 5:
                    records_new += 1
                else:
                    records_updated += 1

            await source_repo.complete_ingestion_run(
                run_id=run.id,
                status="success",
                records_fetched=len(raw_events),
                records_new=records_new,
                records_updated=records_updated,
            )
            await session.commit()

        circuit_breaker.record_success(source_id)
        await invalidate_events(cache)

        logger.info(
            "ingestion_complete",
            source_id=source_id,
            fetched=len(raw_events),
            new=records_new,
            updated=records_updated,
        )

    except Exception as e:
        circuit_breaker.record_failure(source_id)

        async with session_factory() as session:
            source_repo = SourceRecordRepository(session)
            await source_repo.complete_ingestion_run(
                run_id=run.id,
                status="failed",
                error_message=str(e),
            )
            await session.commit()

        logger.error("ingestion_failed", source_id=source_id, error=str(e))


async def run_entity_resolution_job(
    session_factory: async_sessionmaker[AsyncSession],
    cache: RedisClient,
) -> None:
    """Process unmatched source records through entity resolution."""
    async with session_factory() as session:
        source_repo = SourceRecordRepository(session)
        unmatched = await source_repo.get_unmatched()

        if not unmatched:
            logger.debug("resolution_no_unmatched_records")
            return

        logger.info("resolution_starting", unmatched_count=len(unmatched))

        resolver = await TeamResolver.create(session)
        reliability_scorer = DynamicReliabilityScorer(session, cache=cache)

        from sportshub.config import get_settings
        from sportshub.resolution.llm_resolver import LLMEventResolver

        settings = get_settings()
        llm_resolver = None
        if settings.llm_resolution_enabled and settings.anthropic_api_key:
            llm_resolver = LLMEventResolver(
                api_key=settings.anthropic_api_key,
                session=session,
                model=settings.llm_model,
                acceptance_threshold=settings.llm_acceptance_threshold,
                cooldown_minutes=settings.llm_cooldown_minutes,
            )
            logger.info("llm_resolver_enabled", model=settings.llm_model)

        pipeline = ResolutionPipeline(
            session, resolver,
            reliability_scorer=reliability_scorer,
            llm_resolver=llm_resolver,
        )
        result = await pipeline.process_source_records(unmatched)
        await session.commit()

    await invalidate_events(cache)

    logger.info(
        "resolution_job_complete",
        matched=result.matched,
        created=result.created,
        unresolved=result.unresolved,
    )


async def run_health_check_job(
    adapters: list[SourceAdapter],
) -> None:
    """Run health checks on all adapters."""
    for adapter in adapters:
        health = await adapter.health_check()
        logger.info(
            "adapter_health",
            source_id=adapter.source_id,
            is_healthy=health.is_healthy,
            consecutive_failures=health.consecutive_failures,
        )


async def run_stale_cleanup_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Log events with scheduled_at in the past that are still marked as scheduled."""
    async with session_factory() as session:
        import sqlalchemy as sa
        from sportshub.db.tables import events_table

        stmt = (
            sa.select(sa.func.count())
            .select_from(events_table)
            .where(events_table.c.status == "scheduled")
            .where(events_table.c.scheduled_at < datetime.utcnow())
        )
        result = await session.execute(stmt)
        count = result.scalar_one()

        if count > 0:
            logger.info("stale_events_found", count=count)


async def run_confirmation_job(
    confirmation_registry: ConfirmationRegistry,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Check upcoming events (next 48 hours) against external confirmation sources."""
    now = datetime.utcnow()
    horizon = now + timedelta(hours=48)

    async with session_factory() as session:
        # Fetch upcoming scheduled events with their team names
        home_team = teams_table.alias("home_team")
        away_team = teams_table.alias("away_team")

        stmt = (
            sa.select(
                events_table.c.id,
                events_table.c.sport,
                home_team.c.name.label("home_team_name"),
                away_team.c.name.label("away_team_name"),
                events_table.c.scheduled_at,
            )
            .select_from(
                events_table
                .join(home_team, events_table.c.home_team_id == home_team.c.id)
                .join(away_team, events_table.c.away_team_id == away_team.c.id)
            )
            .where(events_table.c.status == "scheduled")
            .where(events_table.c.scheduled_at >= now)
            .where(events_table.c.scheduled_at <= horizon)
        )
        result = await session.execute(stmt)
        rows = result.fetchall()

    if not rows:
        logger.debug("confirmation_no_upcoming_events")
        return

    # Build event dicts for the confirmation registry
    event_dicts = [
        {
            "id": row.id,
            "sport": Sport(row.sport),
            "home_team": row.home_team_name,
            "away_team": row.away_team_name,
            "scheduled_at": row.scheduled_at,
        }
        for row in rows
    ]

    logger.info("confirmation_job_starting", event_count=len(event_dicts))

    all_results = await confirmation_registry.check_all(event_dicts)

    # Persist results
    async with session_factory() as session:
        total_saved = 0
        for source_id, source_results in all_results.items():
            for event_id, cr in source_results.items():
                # Upsert: insert or update on conflict
                insert_stmt = sa.dialects.postgresql.insert(
                    confirmation_checks_table
                ).values(
                    event_id=event_id,
                    source_id=source_id,
                    exists=cr.exists,
                    confidence_boost=cr.confidence_boost,
                    raw_data=cr.raw_data,
                    checked_at=cr.checked_at,
                )
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    constraint="uq_confirmation_checks_event_source",
                    set_={
                        "exists": insert_stmt.excluded.exists,
                        "confidence_boost": insert_stmt.excluded.confidence_boost,
                        "raw_data": insert_stmt.excluded.raw_data,
                        "checked_at": insert_stmt.excluded.checked_at,
                    },
                )
                await session.execute(upsert_stmt)
                total_saved += 1

        await session.commit()

    logger.info(
        "confirmation_job_complete",
        events_checked=len(event_dicts),
        results_saved=total_saved,
    )


async def run_validation_sweep_job(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Re-validate all scheduled events against the constraint engine."""
    engine = ConstraintEngine()

    async with session_factory() as session:
        # Fetch all scheduled event IDs
        stmt = (
            sa.select(events_table.c.id)
            .where(events_table.c.status == "scheduled")
            .order_by(events_table.c.scheduled_at)
        )
        result = await session.execute(stmt)
        event_ids = [row.id for row in result.all()]

        if not event_ids:
            logger.debug("validation_sweep_no_events")
            return

        logger.info("validation_sweep_starting", event_count=len(event_ids))

        results = await engine.validate_batch(event_ids, session)

        total_violations = sum(len(v) for v in results.values())
        error_count = sum(
            1 for vs in results.values()
            for v in vs
            if v.severity == "error"
        )
        warning_count = total_violations - error_count

        await session.commit()

    logger.info(
        "validation_sweep_complete",
        events_checked=len(event_ids),
        total_violations=total_violations,
        errors=error_count,
        warnings=warning_count,
    )


async def run_reconciliation_job(
    registry: AdapterRegistry,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reconcile events that should have completed (scheduled_at < now - 3 hours).

    For each past-due event:
    1. Collect observed results from source adapters.
    2. Reconcile predicted vs observed data.
    3. Persist the reconciliation report.
    4. Update the event status to 'completed'.
    """
    cutoff = datetime.utcnow() - timedelta(hours=3)
    collector = ResultCollector()
    reconciler = EventReconciler()

    async with session_factory() as session:
        # Find events past their scheduled time that are still marked 'scheduled'
        home_team = teams_table.alias("home_team")
        away_team = teams_table.alias("away_team")

        stmt = (
            sa.select(
                events_table.c.id,
                events_table.c.sport,
                events_table.c.home_team_id,
                events_table.c.away_team_id,
                events_table.c.scheduled_at,
                events_table.c.venue,
                home_team.c.name.label("home_team_name"),
                away_team.c.name.label("away_team_name"),
            )
            .select_from(
                events_table
                .join(home_team, events_table.c.home_team_id == home_team.c.id)
                .join(away_team, events_table.c.away_team_id == away_team.c.id)
            )
            .where(events_table.c.status == "scheduled")
            .where(events_table.c.scheduled_at < cutoff)
            .order_by(events_table.c.scheduled_at)
            .limit(100)
        )
        result = await session.execute(stmt)
        events = result.fetchall()

    if not events:
        logger.debug("reconciliation_no_events_due")
        return

    logger.info("reconciliation_job_starting", event_count=len(events))

    # Build adapter lookup
    adapter_map: dict[str, SourceAdapter] = {
        a.source_id: a for a in registry.get_all()
    }

    reconciled_count = 0
    error_count = 0

    for event_row in events:
        event_dict = {
            "id": event_row.id,
            "sport": event_row.sport,
            "home_team_id": event_row.home_team_id,
            "away_team_id": event_row.away_team_id,
            "scheduled_at": event_row.scheduled_at,
            "venue": event_row.venue,
        }

        try:
            async with session_factory() as session:
                # Get source records linked to this event
                sr_stmt = (
                    sa.select(
                        source_records_table.c.source_id,
                        source_records_table.c.source_event_id,
                        source_records_table.c.raw_home_team,
                        source_records_table.c.raw_away_team,
                        source_records_table.c.venue,
                    )
                    .where(source_records_table.c.event_id == event_row.id)
                )
                sr_result = await session.execute(sr_stmt)
                source_records = [
                    {
                        "source_id": r.source_id,
                        "source_event_id": r.source_event_id,
                        "raw_home_team": r.raw_home_team,
                        "raw_away_team": r.raw_away_team,
                        "venue": r.venue,
                    }
                    for r in sr_result.fetchall()
                ]

                # Collect observed results from adapters
                observed = await collector.collect_results(
                    event_dict, source_records, adapter_map
                )

                # Reconcile
                report = await reconciler.reconcile(event_dict, observed)

                # Enrich per-source accuracy with sport info
                for ps in report.per_source_accuracy:
                    ps["sport"] = event_row.sport

                # Persist report
                repo = ReconciliationRepository(session)
                await repo.save_report(report)

                # Update event status to 'completed'
                new_status = "completed"
                if not report.event_occurred:
                    # Check majority vote from observed results
                    statuses = [o.event_status for o in observed]
                    if statuses:
                        cancelled = sum(1 for s in statuses if s == "cancelled")
                        postponed = sum(1 for s in statuses if s == "postponed")
                        if cancelled > len(statuses) // 2:
                            new_status = "cancelled"
                        elif postponed > len(statuses) // 2:
                            new_status = "postponed"

                await session.execute(
                    sa.update(events_table)
                    .where(events_table.c.id == event_row.id)
                    .values(status=new_status, updated_at=datetime.utcnow())
                )
                await session.commit()

                reconciled_count += 1

        except Exception as exc:
            error_count += 1
            logger.error(
                "reconciliation_event_error",
                event_id=str(event_row.id),
                error=str(exc),
            )

    logger.info(
        "reconciliation_job_complete",
        reconciled=reconciled_count,
        errors=error_count,
    )


async def run_reliability_refresh_job(
    session_factory: async_sessionmaker[AsyncSession],
    cache: RedisClient,
) -> None:
    """Pre-compute and cache dynamic reliability scores for all source/sport combinations.

    Runs daily to keep cached scores warm. The DynamicReliabilityScorer queries
    source_accuracy_records, applies exponential decay weighting, and stores
    the computed priority scores in Redis.
    """
    async with session_factory() as session:
        scorer = DynamicReliabilityScorer(session, cache=cache)

        try:
            all_scores = await scorer.precompute_and_cache()
        except Exception:
            logger.exception("reliability_refresh_failed")
            return

    # Log computed scores for observability
    for sport, scores in all_scores.items():
        for source_id, score in scores.items():
            logger.info(
                "reliability_score_computed",
                source_id=source_id,
                sport=sport,
                priority_score=score,
            )

    total_sources = sum(len(s) for s in all_scores.values())
    logger.info(
        "reliability_refresh_complete",
        sports=list(all_scores.keys()),
        total_sources=total_sources,
    )
