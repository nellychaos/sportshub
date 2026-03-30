"""Dashboard data aggregation service."""

import time
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.tables import (
    competitions_table,
    events_table,
    ingestion_runs_table,
    llm_resolutions_table,
    reconciliation_results_table,
    source_accuracy_records_table,
    source_records_table,
    teams_table,
)
from sportshub.resolution.reliability import DynamicReliabilityScorer

logger = structlog.get_logger()

# Known source adapters and their sports
SOURCE_ADAPTERS = [
    ("espn_nba", "nba"),
    ("nbacom_cdn", "nba"),
    ("balldontlie_nba", "nba"),
    ("lolesports", "lol"),
    ("pandascore_lol", "lol"),
    ("liquipedia_lol", "lol"),
    ("espn_fifa", "football"),
    ("fifa_api", "football"),
    ("footballdata_wc", "football"),
]

# Expected cadences in hours (for stale detection)
SOURCE_CADENCES = {
    "espn_nba": 1,
    "nbacom_cdn": 2,
    "balldontlie_nba": 6,
    "lolesports": 1,
    "pandascore_lol": 2,
    "liquipedia_lol": 24,
    "espn_fifa": 1,
    "fifa_api": 2,
    "footballdata_wc": 3,
}

HAIKU_INPUT_COST_PER_TOKEN = 0.80 / 1_000_000
HAIKU_OUTPUT_COST_PER_TOKEN = 4.00 / 1_000_000
# Approximate: ~80% input tokens
HAIKU_AVG_COST_PER_TOKEN = (
    0.80 * HAIKU_INPUT_COST_PER_TOKEN + 0.20 * HAIKU_OUTPUT_COST_PER_TOKEN
)


class DashboardService:
    def __init__(self, session: AsyncSession, start_time: float | None = None) -> None:
        self._session = session
        self._start_time = start_time or time.time()

    async def get_system_overview(self) -> dict:
        """Overall system status and counts."""
        # Events by sport
        stmt = sa.select(
            events_table.c.sport, sa.func.count().label("count")
        ).group_by(events_table.c.sport)
        result = await self._session.execute(stmt)
        events_by_sport = {row.sport: row.count for row in result.all()}
        total_events = sum(events_by_sport.values())

        # Team count
        result = await self._session.execute(
            sa.select(sa.func.count()).select_from(teams_table)
        )
        total_teams = result.scalar_one()

        # Competition count
        result = await self._session.execute(
            sa.select(sa.func.count()).select_from(competitions_table)
        )
        total_competitions = result.scalar_one()

        # Last successful ingestion
        stmt = (
            sa.select(ingestion_runs_table.c.completed_at)
            .where(ingestion_runs_table.c.status == "success")
            .order_by(ingestion_runs_table.c.completed_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        last_success_row = result.first()
        last_successful = last_success_row.completed_at if last_success_row else None

        # Check for failures in last hour
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        stmt = (
            sa.select(sa.func.count())
            .select_from(ingestion_runs_table)
            .where(
                sa.and_(
                    ingestion_runs_table.c.status == "failed",
                    ingestion_runs_table.c.started_at >= one_hour_ago,
                )
            )
        )
        result = await self._session.execute(stmt)
        recent_failures = result.scalar_one()

        status = "healthy"
        if recent_failures > 0:
            status = "degraded"
        if recent_failures >= 3:
            status = "unhealthy"

        return {
            "status": status,
            "total_events": total_events,
            "events_by_sport": events_by_sport,
            "total_teams": total_teams,
            "total_competitions": total_competitions,
            "last_successful_ingestion": last_successful,
            "uptime_seconds": time.time() - self._start_time,
        }

    async def get_source_statuses(self, circuit_breaker=None) -> list[dict]:
        """Per-adapter status cards."""
        # Get latest run per source
        stmt = sa.text("""
            SELECT DISTINCT ON (source_id)
                id, source_id, started_at, completed_at, status,
                records_fetched, records_new, records_updated, records_matched,
                error_message
            FROM ingestion_runs
            ORDER BY source_id, started_at DESC
        """)
        result = await self._session.execute(stmt)
        latest_runs = {row.source_id: row._mapping for row in result.all()}

        # Get last success per source
        stmt = sa.text("""
            SELECT DISTINCT ON (source_id)
                source_id, completed_at
            FROM ingestion_runs
            WHERE status = 'success'
            ORDER BY source_id, completed_at DESC
        """)
        result = await self._session.execute(stmt)
        last_successes = {row.source_id: row.completed_at for row in result.all()}

        statuses = []
        for source_id, sport in SOURCE_ADAPTERS:
            run = latest_runs.get(source_id, {})
            cb_status = {}
            if circuit_breaker:
                cb_status = circuit_breaker.get_status(source_id)

            statuses.append({
                "source_id": source_id,
                "sport": sport,
                "last_run_status": run.get("status"),
                "last_run_at": run.get("started_at"),
                "last_success_at": last_successes.get(source_id),
                "records_fetched_last": run.get("records_fetched", 0),
                "consecutive_failures": cb_status.get("consecutive_failures", 0),
                "circuit_breaker_open": cb_status.get("is_open", False),
                "circuit_breaker_until": cb_status.get("open_until"),
            })
        return statuses

    async def get_ingestion_timeline(self, hours: int = 24) -> list[dict]:
        """Recent ingestion runs."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = (
            sa.select(ingestion_runs_table)
            .where(ingestion_runs_table.c.started_at >= since)
            .order_by(ingestion_runs_table.c.started_at.desc())
            .limit(100)
        )
        result = await self._session.execute(stmt)
        runs = []
        for row in result.all():
            m = row._mapping
            duration = None
            if m["completed_at"] and m["started_at"]:
                duration = (m["completed_at"] - m["started_at"]).total_seconds()
            runs.append({
                "id": str(m["id"]),
                "source_id": m["source_id"],
                "started_at": m["started_at"],
                "completed_at": m["completed_at"],
                "status": m["status"],
                "duration_seconds": duration,
                "records_fetched": m["records_fetched"],
                "records_new": m["records_new"],
                "records_updated": m["records_updated"],
                "records_matched": m["records_matched"],
                "error_message": m["error_message"],
            })
        return runs

    async def get_data_quality(self) -> dict:
        """Confidence distribution, source coverage, unmatched counts."""
        # Confidence distribution (scheduled events only)
        t = events_table
        bucket = sa.case(
            (t.c.confidence_score < 0.5, "low"),
            (t.c.confidence_score < 0.7, "medium"),
            (t.c.confidence_score < 0.9, "high"),
            else_="very_high",
        ).label("bucket")
        stmt = (
            sa.select(bucket, sa.func.count().label("count"))
            .where(t.c.status == "scheduled")
            .group_by(bucket)
        )
        result = await self._session.execute(stmt)
        confidence_dist = [
            {"bucket": r.bucket, "count": r.count} for r in result.all()
        ]

        # Source coverage
        coverage_label = sa.case(
            (t.c.source_count >= 3, "3+"),
            else_=sa.cast(t.c.source_count, sa.String),
        ).label("sources")
        stmt = (
            sa.select(coverage_label, sa.func.count().label("count"))
            .where(t.c.status == "scheduled")
            .group_by(coverage_label)
        )
        result = await self._session.execute(stmt)
        source_coverage = [
            {"sources": r.sources, "count": r.count} for r in result.all()
        ]

        # Unmatched by source
        sr = source_records_table
        stmt = (
            sa.select(sr.c.source_id, sa.func.count().label("count"))
            .where(sr.c.event_id.is_(None))
            .group_by(sr.c.source_id)
        )
        result = await self._session.execute(stmt)
        unmatched = [
            {"source_id": r.source_id, "count": r.count} for r in result.all()
        ]
        total_unmatched = sum(u["count"] for u in unmatched)

        return {
            "confidence_distribution": confidence_dist,
            "source_coverage": source_coverage,
            "unmatched_by_source": unmatched,
            "total_unmatched": total_unmatched,
        }

    async def get_alerts(self, circuit_breaker=None) -> list[dict]:
        """Active alerts and issues."""
        alerts: list[dict] = []
        now = datetime.now(timezone.utc)

        # Failed ingestion runs in last 24h
        since_24h = now - timedelta(hours=24)
        stmt = (
            sa.select(
                ingestion_runs_table.c.source_id,
                sa.func.count().label("fail_count"),
                sa.func.max(ingestion_runs_table.c.started_at).label("last_failure"),
            )
            .where(
                sa.and_(
                    ingestion_runs_table.c.status == "failed",
                    ingestion_runs_table.c.started_at >= since_24h,
                )
            )
            .group_by(ingestion_runs_table.c.source_id)
        )
        result = await self._session.execute(stmt)
        for row in result.all():
            severity = "critical" if row.fail_count >= 3 else "warning"
            alerts.append({
                "severity": severity,
                "alert_type": "ingestion_failure",
                "message": (
                    f"{row.source_id}: {row.fail_count} failed ingestion(s) in last 24h"
                ),
                "timestamp": row.last_failure,
                "details": {"source_id": row.source_id, "count": row.fail_count},
            })

        # Open circuit breakers
        if circuit_breaker:
            for cb_status in circuit_breaker.get_all_statuses():
                if cb_status.get("is_open"):
                    alerts.append({
                        "severity": "critical",
                        "alert_type": "circuit_breaker",
                        "message": (
                            f"{cb_status['source_id']}: circuit breaker OPEN "
                            f"until {cb_status.get('open_until', 'unknown')}"
                        ),
                        "timestamp": now,
                        "details": cb_status,
                    })

        # Low-confidence events
        stmt = (
            sa.select(sa.func.count())
            .select_from(events_table)
            .where(
                sa.and_(
                    events_table.c.confidence_score < 0.5,
                    events_table.c.status == "scheduled",
                )
            )
        )
        result = await self._session.execute(stmt)
        low_conf_count = result.scalar_one()
        if low_conf_count > 0:
            alerts.append({
                "severity": "warning",
                "alert_type": "low_confidence",
                "message": f"{low_conf_count} event(s) with confidence < 0.5",
                "timestamp": now,
                "details": {"count": low_conf_count},
            })

        # Stale sources
        for source_id, cadence_hours in SOURCE_CADENCES.items():
            stmt = sa.select(
                sa.func.max(ingestion_runs_table.c.completed_at)
            ).where(
                sa.and_(
                    ingestion_runs_table.c.source_id == source_id,
                    ingestion_runs_table.c.status == "success",
                )
            )
            result = await self._session.execute(stmt)
            last = result.scalar_one_or_none()
            if last and (now - last).total_seconds() > cadence_hours * 7200:  # 2x cadence
                alerts.append({
                    "severity": "warning",
                    "alert_type": "stale_source",
                    "message": (
                        f"{source_id}: no successful ingestion in "
                        f"{int((now - last).total_seconds() / 3600)}h "
                        f"(expected every {cadence_hours}h)"
                    ),
                    "timestamp": last,
                    "details": {
                        "source_id": source_id,
                        "expected_hours": cadence_hours,
                    },
                })

        # Sort: critical first, then by timestamp
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        alerts.sort(
            key=lambda a: (
                severity_order.get(a["severity"], 9),
                a.get("timestamp") or now,
            ),
        )
        return alerts

    async def get_constraint_violations(self) -> list[dict]:
        """Query events that have constraint violations stored in metadata."""
        # Use JSONB containment to find events with non-empty violations arrays
        t = events_table
        home = teams_table.alias("home_team")
        away = teams_table.alias("away_team")

        stmt = (
            sa.select(
                t.c.id,
                t.c.sport,
                t.c.scheduled_at,
                t.c.confidence_score,
                t.c.metadata,
                home.c.name.label("home_team_name"),
                away.c.name.label("away_team_name"),
            )
            .select_from(
                t
                .join(home, t.c.home_team_id == home.c.id)
                .join(away, t.c.away_team_id == away.c.id)
            )
            .where(
                sa.and_(
                    t.c.metadata["violations"] != sa.cast("[]", sa.dialects.postgresql.JSONB),
                    t.c.metadata.has_key("violations"),
                )
            )
            .order_by(t.c.scheduled_at.desc())
            .limit(100)
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        violations_list = []
        for row in rows:
            meta = row.metadata or {}
            event_violations = meta.get("violations", [])
            for v in event_violations:
                violations_list.append({
                    "event_id": str(row.id),
                    "sport": row.sport,
                    "home_team": row.home_team_name,
                    "away_team": row.away_team_name,
                    "scheduled_at": row.scheduled_at,
                    "confidence_score": row.confidence_score,
                    "constraint_name": v.get("constraint_name", "unknown"),
                    "severity": v.get("severity", "warning"),
                    "message": v.get("message", ""),
                    "details": v.get("details", {}),
                })

        # Sort: errors first, then by scheduled_at desc
        severity_order = {"error": 0, "warning": 1}
        violations_list.sort(
            key=lambda x: (severity_order.get(x["severity"], 9), x.get("scheduled_at")),
        )
        return violations_list

    async def get_llm_effectiveness(self, hours: int = 168) -> dict:
        """LLM resolver effectiveness metrics."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        t = llm_resolutions_table

        # Aggregate stats
        stmt = (
            sa.select(
                sa.func.count().label("total"),
                sa.func.count().filter(t.c.accepted.is_(True)).label("accepted"),
                sa.func.count().filter(t.c.accepted.is_(False)).label("rejected"),
                sa.func.count()
                .filter(t.c.alias_created.is_(True))
                .label("aliases_created"),
                sa.func.coalesce(sa.func.sum(t.c.tokens_used), 0).label(
                    "total_tokens"
                ),
                sa.func.coalesce(sa.func.avg(t.c.latency_ms), 0).label(
                    "avg_latency_ms"
                ),
            )
            .where(t.c.created_at >= since)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if not row:
            return self._empty_llm_response()

        m = row._mapping
        total = m["total"] or 0
        accepted = m["accepted"] or 0
        aliases = m["aliases_created"] or 0
        total_tokens = m["total_tokens"] or 0

        acceptance_rate = accepted / total if total > 0 else 0.0
        self_healing_rate = aliases / accepted if accepted > 0 else 0.0
        estimated_cost = total_tokens * HAIKU_AVG_COST_PER_TOKEN

        # P95 latency
        try:
            stmt = sa.select(
                sa.func.percentile_cont(0.95).within_group(t.c.latency_ms)
            ).where(t.c.created_at >= since)
            result = await self._session.execute(stmt)
            p95 = result.scalar_one_or_none() or 0
        except Exception:
            p95 = 0

        # Confidence distribution
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
        result = await self._session.execute(stmt)
        conf_dist = [
            {"bucket": r.bucket, "count": r.count} for r in result.all()
        ]

        # Daily trend
        day = sa.func.date_trunc("day", t.c.created_at).label("day")
        stmt = (
            sa.select(
                day,
                sa.func.count().filter(t.c.accepted.is_(True)).label("accepted"),
                sa.func.count().filter(t.c.accepted.is_(False)).label("rejected"),
            )
            .where(t.c.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        result = await self._session.execute(stmt)
        daily_trend = [
            {
                "day": str(r._mapping["day"]),
                "accepted": r._mapping["accepted"],
                "rejected": r._mapping["rejected"],
            }
            for r in result.all()
        ]

        # Recent decisions
        stmt = (
            sa.select(t)
            .where(t.c.created_at >= since)
            .order_by(t.c.created_at.desc())
            .limit(20)
        )
        result = await self._session.execute(stmt)
        recent = []
        for r in result.all():
            rm = r._mapping
            recent.append({
                "id": str(rm["id"]),
                "input_text": rm["input_text"],
                "resolved_id": str(rm["resolved_id"]) if rm["resolved_id"] else None,
                "confidence": rm["confidence"],
                "accepted": rm["accepted"],
                "acceptance_reason": rm["acceptance_reason"],
                "alias_created": rm["alias_created"],
                "latency_ms": rm["latency_ms"],
                "created_at": str(rm["created_at"]),
                "llm_output": rm["llm_output"],
            })

        return {
            "total_calls": total,
            "accepted": accepted,
            "rejected": m["rejected"] or 0,
            "acceptance_rate": round(acceptance_rate, 3),
            "aliases_created": aliases,
            "self_healing_rate": round(self_healing_rate, 3),
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(estimated_cost, 4),
            "avg_latency_ms": round(float(m["avg_latency_ms"]), 1),
            "p95_latency_ms": round(float(p95), 1),
            "confidence_distribution": conf_dist,
            "daily_trend": daily_trend,
            "recent_decisions": recent,
        }

    async def get_reconciliation_stats(self, days: int = 30) -> dict:
        """Aggregate reconciliation accuracy rates by source and sport."""
        sar = source_accuracy_records_table
        rr = reconciliation_results_table
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Overall reconciliation summary
        stmt = sa.select(
            sa.func.count().label("total"),
            sa.func.count().filter(rr.c.event_occurred.is_(True)).label("events_occurred"),
            sa.func.count().filter(rr.c.teams_correct.is_(True)).label("teams_correct"),
            sa.func.count().filter(rr.c.venue_correct.is_(True)).label("venue_correct"),
            sa.func.coalesce(sa.func.avg(rr.c.time_diff_seconds), 0).label("avg_time_diff"),
        ).where(rr.c.reconciled_at >= since)
        result = await self._session.execute(stmt)
        summary_row = result.first()

        total_reconciled = summary_row.total if summary_row else 0
        summary = {
            "total_reconciled": total_reconciled,
            "events_occurred_rate": (
                round(summary_row.events_occurred / total_reconciled, 3)
                if total_reconciled > 0
                else 0.0
            ),
            "teams_accuracy_rate": (
                round(summary_row.teams_correct / total_reconciled, 3)
                if total_reconciled > 0
                else 0.0
            ),
            "venue_accuracy_rate": (
                round(summary_row.venue_correct / total_reconciled, 3)
                if total_reconciled > 0
                else 0.0
            ),
            "avg_time_diff_seconds": (
                round(float(summary_row.avg_time_diff), 1)
                if summary_row
                else 0.0
            ),
        }

        # Per source+sport breakdown
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
        by_source = []
        for r in result.all():
            t = r.total or 0
            by_source.append({
                "source_id": r.source_id,
                "sport": r.sport,
                "total": t,
                "time_accuracy_rate": round(r.time_accurate_count / t, 3) if t else 0.0,
                "teams_accuracy_rate": round(r.teams_correct_count / t, 3) if t else 0.0,
                "venue_accuracy_rate": round(r.venue_correct_count / t, 3) if t else 0.0,
                "avg_time_diff_seconds": round(float(r.avg_time_diff), 1),
            })

        # Recent reconciliation reports
        stmt = (
            sa.select(rr)
            .order_by(rr.c.reconciled_at.desc())
            .limit(10)
        )
        result = await self._session.execute(stmt)
        recent = []
        for r in result.all():
            m = r._mapping
            recent.append({
                "event_id": str(m["event_id"]),
                "event_occurred": m["event_occurred"],
                "time_diff_seconds": m["time_diff_seconds"],
                "teams_correct": m["teams_correct"],
                "venue_correct": m["venue_correct"],
                "reconciled_at": m["reconciled_at"],
            })

        return {
            "summary": summary,
            "by_source": by_source,
            "recent_reports": recent,
        }

    async def get_source_reliability(self) -> list[dict]:
        """Source reliability data showing dynamic vs hardcoded priority scores.

        For each source, returns:
        - Dynamic priority score (if sufficient data)
        - Hardcoded fallback score
        - Per-field accuracy breakdown (time, teams, venue)
        - Sample count
        - Whether dynamic or fallback is being used
        """
        scorer = DynamicReliabilityScorer(self._session)
        try:
            return await scorer.get_detailed_reliability()
        except Exception:
            logger.exception("reliability_dashboard_query_failed")
            return []

    def _empty_llm_response(self) -> dict:
        return {
            "total_calls": 0,
            "accepted": 0,
            "rejected": 0,
            "acceptance_rate": 0.0,
            "aliases_created": 0,
            "self_healing_rate": 0.0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "avg_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "confidence_distribution": [],
            "daily_trend": [],
            "recent_decisions": [],
        }
