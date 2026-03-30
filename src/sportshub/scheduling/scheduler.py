"""APScheduler setup and job registration."""

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from sportshub.cache.client import RedisClient
from sportshub.ingestion.registry import AdapterRegistry
from sportshub.scheduling.circuit_breaker import CircuitBreaker
from sportshub.ingestion.confirmation.registry import ConfirmationRegistry
from sportshub.scheduling.jobs import (
    run_confirmation_job,
    run_entity_resolution_job,
    run_health_check_job,
    run_ingestion_job,
    run_reconciliation_job,
    run_reliability_refresh_job,
    run_stale_cleanup_job,
    run_validation_sweep_job,
)

logger = structlog.get_logger()


class SportshubScheduler:
    """Manages all scheduled jobs for the application."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._circuit_breaker = CircuitBreaker()

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker

    def configure(
        self,
        registry: AdapterRegistry,
        session_factory: async_sessionmaker[AsyncSession],
        cache: RedisClient,
        confirmation_registry: ConfirmationRegistry | None = None,
    ) -> None:
        """Register all scheduled jobs."""
        # Ingestion jobs
        cadences = {
            "espn_nba": IntervalTrigger(hours=1),
            "nbacom_cdn": IntervalTrigger(hours=2),
            "balldontlie_nba": IntervalTrigger(hours=6),
            "lolesports": IntervalTrigger(hours=1),
            "pandascore_lol": IntervalTrigger(hours=2),
            "liquipedia_lol": CronTrigger(hour=8),  # Daily 08:00 UTC
            "fifa_api": IntervalTrigger(hours=2),
            "footballdata_wc": IntervalTrigger(hours=3),
            "espn_fifa": IntervalTrigger(hours=1),
        }

        for adapter in registry.get_all():
            trigger = cadences.get(adapter.source_id)
            if not trigger:
                logger.warning("no_cadence_defined", source_id=adapter.source_id)
                continue

            self._scheduler.add_job(
                run_ingestion_job,
                trigger=trigger,
                kwargs={
                    "adapter": adapter,
                    "session_factory": session_factory,
                    "circuit_breaker": self._circuit_breaker,
                    "cache": cache,
                },
                id=f"ingest_{adapter.source_id}",
                name=f"Ingest {adapter.source_id}",
                replace_existing=True,
            )

        # Entity resolution (every hour, offset by 15 min)
        self._scheduler.add_job(
            run_entity_resolution_job,
            trigger=IntervalTrigger(hours=1, minutes=15),
            kwargs={"session_factory": session_factory, "cache": cache},
            id="entity_resolution",
            name="Entity Resolution",
            replace_existing=True,
        )

        # Health check (every hour)
        self._scheduler.add_job(
            run_health_check_job,
            trigger=IntervalTrigger(hours=1),
            kwargs={"adapters": registry.get_all()},
            id="health_check",
            name="Health Check",
            replace_existing=True,
        )

        # Stale event cleanup (daily 03:00 UTC)
        self._scheduler.add_job(
            run_stale_cleanup_job,
            trigger=CronTrigger(hour=3),
            kwargs={"session_factory": session_factory},
            id="stale_cleanup",
            name="Stale Event Cleanup",
            replace_existing=True,
        )

        # Validation sweep (every 4 hours)
        self._scheduler.add_job(
            run_validation_sweep_job,
            trigger=CronTrigger(hour="*/4"),
            kwargs={"session_factory": session_factory},
            id="validation_sweep",
            name="Schedule Validation Sweep",
            replace_existing=True,
        )

        # Post-event reconciliation (every 2 hours)
        self._scheduler.add_job(
            run_reconciliation_job,
            trigger=IntervalTrigger(hours=2),
            kwargs={
                "registry": registry,
                "session_factory": session_factory,
            },
            id="reconciliation",
            name="Post-Event Reconciliation",
            replace_existing=True,
        )

        # Reliability score refresh (daily at 04:00 UTC)
        self._scheduler.add_job(
            run_reliability_refresh_job,
            trigger=CronTrigger(hour=4),
            kwargs={
                "session_factory": session_factory,
                "cache": cache,
            },
            id="reliability_refresh",
            name="Reliability Score Refresh",
            replace_existing=True,
        )

        # External confirmation checks (every 6 hours)
        if confirmation_registry is not None:
            self._scheduler.add_job(
                run_confirmation_job,
                trigger=IntervalTrigger(hours=6),
                kwargs={
                    "confirmation_registry": confirmation_registry,
                    "session_factory": session_factory,
                },
                id="confirmation_check",
                name="External Confirmation Check",
                replace_existing=True,
            )

        logger.info("scheduler_configured", job_count=len(self._scheduler.get_jobs()))

    def start(self) -> None:
        self._scheduler.start()
        logger.info("scheduler_started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler_shutdown")

    def get_jobs(self) -> list[dict]:
        """Get info about all scheduled jobs for diagnostics."""
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": str(job.next_run_time) if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]
