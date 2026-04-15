"""Dashboard data aggregation service."""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
from sportshub.providers.registry import ProviderRegistry
from sportshub.resolution.reliability import DynamicReliabilityScorer
from sportshub.scripts.io import DATA_DIR

logger = structlog.get_logger()

# Lazily-loaded provider registry (shared across all service instances)
_registry = ProviderRegistry()

# Expected cadence multiplier for stale detection: alert if no success in N * cadence
_STALE_MULTIPLIER = 2.0

# Default cadences by provider type when rate_limit_seconds is unavailable
_DEFAULT_CADENCES_HOURS = {
    "api": 2,
    "cdn": 4,
    "web_scrape": 72,       # manually triggered
    "api_websocket": None,  # skip stale detection
    "csv_github": None,     # skip stale detection
}

HAIKU_INPUT_COST_PER_TOKEN = 0.80 / 1_000_000
HAIKU_OUTPUT_COST_PER_TOKEN = 4.00 / 1_000_000
# Approximate: ~80% input tokens
HAIKU_AVG_COST_PER_TOKEN = (
    0.80 * HAIKU_INPUT_COST_PER_TOKEN + 0.20 * HAIKU_OUTPUT_COST_PER_TOKEN
)

# Known primary collection keys for data files (filename -> dict key holding the list)
_COLLECTION_KEYS = {
    "nba_player_stats.json": "players",
    "providers.json": "providers",
    "provider_id_mappings.json": None,  # complex structure, count nba_teams.mappings
}


def _get_cadence_hours(provider) -> float | None:
    """Determine expected ingestion cadence in hours for stale detection."""
    if provider.rate_limit_seconds:
        # For API sources, expect runs at roughly 2x the rate limit interval
        # (rate_limit is per-request; a full run takes many requests)
        # Use a sensible minimum of 1 hour
        return max(1.0, provider.rate_limit_seconds / 60)

    return _DEFAULT_CADENCES_HOURS.get(provider.type)


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
        """Per-provider status cards with rich metadata from ProviderRegistry."""
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
        for provider in _registry.get_all_providers():
            source_id = provider.source_id
            run = latest_runs.get(source_id, {})
            cb_status = {}
            if circuit_breaker:
                cb_status = circuit_breaker.get_status(source_id)

            statuses.append({
                "source_id": source_id,
                "display_name": provider.display_name,
                "sport": provider.sport,
                "provider_type": provider.type,
                "reliability": provider.reliability,
                "priority": provider.priority,
                "rate_limit_seconds": provider.rate_limit_seconds,
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

        # Stale sources (derived from ProviderRegistry)
        for provider in _registry.get_all_providers():
            cadence = _get_cadence_hours(provider)
            if cadence is None:
                continue  # skip sources without expected cadence

            stmt = sa.select(
                sa.func.max(ingestion_runs_table.c.completed_at)
            ).where(
                sa.and_(
                    ingestion_runs_table.c.source_id == provider.source_id,
                    ingestion_runs_table.c.status == "success",
                )
            )
            result = await self._session.execute(stmt)
            last = result.scalar_one_or_none()
            threshold = cadence * 3600 * _STALE_MULTIPLIER
            if last and (now - last).total_seconds() > threshold:
                alerts.append({
                    "severity": "warning",
                    "alert_type": "stale_source",
                    "message": (
                        f"{provider.source_id}: no successful ingestion in "
                        f"{int((now - last).total_seconds() / 3600)}h "
                        f"(expected every {cadence:.0f}h)"
                    ),
                    "timestamp": last,
                    "details": {
                        "source_id": provider.source_id,
                        "expected_hours": cadence,
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
        """Source reliability data showing dynamic vs hardcoded priority scores."""
        scorer = DynamicReliabilityScorer(self._session)
        try:
            return await scorer.get_detailed_reliability()
        except Exception:
            logger.exception("reliability_dashboard_query_failed")
            return []

    # ── New: Reference Data Inventory ─────────────────────────────────

    def get_reference_data_inventory(self) -> dict:
        """Scan data/ directory and return file inventory with schema status.

        Synchronous -- reads local files, no DB needed.
        """
        from sportshub.validation.data_schemas import validate_data_file

        files = []
        for path in sorted(DATA_DIR.glob("*.json")):
            if path.name == "script_activity.json":
                continue  # skip the activity log itself

            stat = path.stat()
            size = stat.st_size
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

            # Count records
            record_count = self._count_records(path)

            # Categorize
            category = self._categorize_file(path.name)

            # Schema validation
            errors = validate_data_file(path.name)
            has_schema = self._has_schema(path.name)

            files.append({
                "name": path.name,
                "category": category,
                "record_count": record_count,
                "size_bytes": size,
                "size_display": self._format_size(size),
                "modified_at": modified.isoformat(),
                "modified_display": modified.strftime("%Y-%m-%d %H:%M"),
                "has_schema": has_schema,
                "schema_valid": has_schema and len(errors) == 0,
                "validation_errors": errors if has_schema else [],
            })

        # Totals
        total_size = sum(f["size_bytes"] for f in files)
        schemas_defined = sum(1 for f in files if f["has_schema"])
        schemas_passing = sum(1 for f in files if f["schema_valid"])

        return {
            "files": files,
            "totals": {
                "file_count": len(files),
                "total_size_bytes": total_size,
                "total_size_display": self._format_size(total_size),
                "schemas_defined": schemas_defined,
                "schemas_passing": schemas_passing,
            },
        }

    def _count_records(self, path: Path) -> int | None:
        """Count the primary collection in a JSON file."""
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        if isinstance(data, list):
            return len(data)

        if isinstance(data, dict):
            # Known nested structures
            name = path.name
            if name in _COLLECTION_KEYS:
                key = _COLLECTION_KEYS[name]
                if key is None and name == "provider_id_mappings.json":
                    # Count mappings in nba_teams section
                    return len(data.get("nba_teams", {}).get("mappings", {}))
                if key and key in data:
                    val = data[key]
                    return len(val) if isinstance(val, (list, dict)) else None

            # Try common collection keys
            for key in ("players", "teams", "providers", "mappings", "competitions"):
                if key in data and isinstance(data[key], (list, dict)):
                    return len(data[key])

        return None

    def _has_schema(self, filename: str) -> bool:
        """Check if a schema file exists for the given data file."""
        stem = Path(filename).stem
        return (DATA_DIR / "schemas" / f"{stem}.schema.json").exists()

    @staticmethod
    def _categorize_file(name: str) -> str:
        """Categorize a data file by its name prefix."""
        if name.startswith("nba_") or name.startswith("bref_"):
            return "nba"
        if name.startswith("teamrankings_"):
            return "teamrankings"
        if name.startswith("lol_"):
            return "lol"
        if name.startswith("fifa_") or name.startswith("wc_"):
            return "football"
        if name.startswith("f1_"):
            return "f1"
        if name in ("providers.json", "provider_id_mappings.json",
                     "competitions.json", "venue_timezones.json"):
            return "system"
        return "other"

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size for display."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"

    # ── New: Script Activity ──────────────────────────────────────────

    def get_script_activity(self, limit: int = 50) -> list[dict]:
        """Return recent script activity from the file-based log.

        Synchronous -- reads data/script_activity.json.
        """
        from sportshub.scripts.activity_log import get_recent_activity
        return get_recent_activity(limit=limit)

    # ── New: Data Completeness ────────────────────────────────────────

    async def get_data_completeness(self) -> dict:
        """Compute data completeness metrics across player stats, team data, and events."""
        player_stats = self._compute_player_completeness()
        team_data = self._compute_team_completeness()
        event_enrichment = await self._compute_event_enrichment()

        return {
            "player_stats": player_stats,
            "team_data": team_data,
            "event_enrichment": event_enrichment,
            "f1_schedule": self._f1_schedule_completeness(),
            "f1_circuits": self._f1_circuit_completeness(),
        }

    def _compute_player_completeness(self) -> dict:
        """Count player stat coverage across all sports."""
        return {
            "nba": self._nba_player_completeness(),
            "lol": self._lol_player_completeness(),
            "football": self._football_player_completeness(),
            "f1": self._f1_driver_completeness(),
        }

    def _nba_player_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "nba_player_stats.json") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"total": 0, "tiers": {}, "pct": {}}

        players = data.get("players", [])
        total = len(players)
        if total == 0:
            return {"total": 0, "tiers": {}, "pct": {}}

        tiers = {
            "base_stats": sum(1 for p in players if p.get("stats")),
            "advanced": sum(1 for p in players if p.get("advanced")),
            "play_by_play": sum(1 for p in players if p.get("play_by_play")),
            "adjusted_shooting": sum(1 for p in players if p.get("adjusted_shooting")),
        }
        tiers["fully_enriched"] = sum(
            1 for p in players
            if p.get("advanced") and p.get("play_by_play") and p.get("adjusted_shooting")
        )
        pct = {k: round(v / total * 100, 1) for k, v in tiers.items()}
        return {"total": total, "tiers": tiers, "pct": pct}

    def _lol_player_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "lol_players.json") as f:
                roster = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            roster = []

        try:
            with open(DATA_DIR / "lol_player_stats.json") as f:
                stats_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            stats_data = {}

        players = stats_data.get("players", [])
        roster_count = len(roster) if isinstance(roster, list) else 0
        stats_count = len(players)

        tiers = {
            "roster": roster_count,
            "kda_stats": sum(1 for p in players if p.get("stats", {}).get("kda") is not None),
            "champion_pool": sum(1 for p in players if p.get("champion_pool")),
        }
        total = max(roster_count, stats_count, 1)
        pct = {k: round(v / total * 100, 1) for k, v in tiers.items()}
        return {"total": roster_count, "tiers": tiers, "pct": pct}

    def _football_player_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "fifa_wc_players.json") as f:
                players = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            players = []

        if not isinstance(players, list):
            players = []

        total = len(players)
        if total == 0:
            return {"total": 0, "tiers": {}, "pct": {}}

        tiers = {
            "identity": sum(1 for p in players if p.get("name") and p.get("metadata", {}).get("date_of_birth")),
            "cross_provider_ids": sum(1 for p in players if len(p.get("aliases", [])) >= 5),
            "club_stats": sum(1 for p in players if p.get("metadata", {}).get("club_stats_2025_26")),
            "espn_stats": sum(1 for p in players if p.get("metadata", {}).get("club_goals_2025_26") is not None
                            or p.get("metadata", {}).get("club_appearances_2025_26") is not None),
        }
        pct = {k: round(v / total * 100, 1) for k, v in tiers.items()}
        return {"total": total, "tiers": tiers, "pct": pct}

    def _f1_driver_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "f1_drivers_2026.json") as f:
                drivers = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            drivers = []
        if not isinstance(drivers, list):
            drivers = []
        total = len(drivers)
        if total == 0:
            return {"total": 0, "tiers": {}, "pct": {}}
        tiers = {
            "identity": sum(1 for d in drivers if d.get("name") and d.get("date_of_birth") and d.get("nationality")),
            "team_assignment": sum(1 for d in drivers if d.get("team_id")),
            "headshot": sum(1 for d in drivers if d.get("headshot_url")),
            "championship_data": sum(1 for d in drivers if d.get("championship_position")),
        }
        pct = {k: round(v / total * 100, 1) for k, v in tiers.items()}
        return {"total": total, "tiers": tiers, "pct": pct}

    def _f1_constructor_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "f1_constructors_2026.json") as f:
                constructors = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            constructors = []
        if not isinstance(constructors, list):
            constructors = []
        total = len(constructors)
        if total == 0:
            return {"total": 0, "tiers": {}}
        return {
            "total": total,
            "with_colour": sum(1 for c in constructors if c.get("team_colour")),
            "with_driver_pair": sum(1 for c in constructors if len(c.get("drivers", [])) >= 2),
            "with_metadata": sum(1 for c in constructors if c.get("metadata", {}).get("team_principal") and c.get("metadata", {}).get("headquarters")),
        }

    def _f1_schedule_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "f1_schedule_2026.json") as f:
                races = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            races = []
        if not isinstance(races, list):
            races = []
        total = len(races)
        if total == 0:
            return {"total": 0, "tiers": {}}
        return {
            "total": total,
            "with_session_times": sum(
                1 for r in races
                if r.get("sessions", {}).get("FirstPractice", {}).get("date")
                and r.get("sessions", {}).get("Qualifying", {}).get("date")
            ),
            "sprint_weekends": sum(1 for r in races if r.get("is_sprint_weekend")),
            "with_circuit": sum(1 for r in races if r.get("circuit_id")),
        }

    def _f1_circuit_completeness(self) -> dict:
        try:
            with open(DATA_DIR / "f1_circuits.json") as f:
                circuits = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            circuits = []
        if not isinstance(circuits, list):
            circuits = []
        total = len(circuits)
        if total == 0:
            return {"total": 0, "tiers": {}}
        return {
            "total": total,
            "with_coordinates": sum(1 for c in circuits if (c.get("track_layout") or {}).get("x")),
            "with_corners": sum(1 for c in circuits if (c.get("track_layout") or {}).get("corners")),
            "with_svg": sum(1 for c in circuits if c.get("svg_track_map_url")),
            "with_type": sum(1 for c in circuits if c.get("circuit_type")),
        }

    def _compute_team_completeness(self) -> dict:
        """Count team data coverage across all sports."""
        def _load_list(filename: str) -> list:
            try:
                with open(DATA_DIR / filename) as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except (FileNotFoundError, json.JSONDecodeError):
                return []

        nba_teams = _load_list("nba_teams.json")
        lol_teams = _load_list("lol_teams.json")
        fifa_teams = _load_list("fifa_wc_teams.json")

        return {
            "nba": {
                "total": len(nba_teams),
                "with_power_ratings": len(_load_list("teamrankings_power_ratings_2026.json")),
                "with_ats_trends": len(_load_list("teamrankings_ats_trends_2026.json")),
            },
            "lol": {
                "total": len(lol_teams),
                "with_records": sum(1 for t in lol_teams if t.get("metadata", {}).get("wins") is not None),
                "with_standings": sum(1 for t in lol_teams if t.get("metadata", {}).get("region_standing") is not None),
            },
            "football": {
                "total": len(fifa_teams),
                "with_historical": sum(1 for t in fifa_teams if t.get("metadata", {}).get("historical_wc_stats")),
                "with_tournament": sum(1 for t in fifa_teams if t.get("metadata", {}).get("tournament_stats")),
            },
            "f1": self._f1_constructor_completeness(),
        }

    def get_thesis_scorecard(self) -> dict:
        """Compute the MVP thesis validation metrics.

        Answers the core questions:
        1. Is multi-source aggregation accurate?
        2. Can AI handle entity resolution cheaply?
        3. How deep is data coverage vs paid providers?
        4. Is it self-healing?
        """
        # Count providers and sports from registry
        providers = _registry.get_all_providers()
        sports = set()
        for p in providers:
            if p.sport:
                sports.add(p.sport)

        # Data depth scores per sport
        nba = self._nba_player_completeness()
        lol = self._lol_player_completeness()
        football = self._football_player_completeness()
        f1 = self._f1_driver_completeness()

        nba_depth = len([v for v in nba.get("pct", {}).values() if v > 80])
        lol_depth = len([v for v in lol.get("pct", {}).values() if v > 30])
        football_depth = len([v for v in football.get("pct", {}).values() if v > 30])
        f1_depth = len([v for v in f1.get("pct", {}).values() if v > 80])

        # Reference data totals
        total_records = 0
        for fn in ["nba_players.json", "nba_player_stats.json", "nba_teams.json",
                    "lol_players.json", "lol_teams.json",
                    "fifa_wc_players.json", "fifa_wc_teams.json",
                    "f1_drivers_2026.json", "f1_constructors_2026.json",
                    "f1_schedule_2026.json", "f1_circuits.json"]:
            try:
                with open(DATA_DIR / fn) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    total_records += len(data)
                elif isinstance(data, dict) and "players" in data:
                    total_records += len(data["players"])
            except (FileNotFoundError, json.JSONDecodeError):
                pass

        return {
            "provider_count": len(providers),
            "sport_count": len(sports),
            "sports": sorted(sports),
            "total_reference_records": total_records,
            "depth_scores": {
                "nba": {"score": nba_depth, "max": 5, "label": "NBA",
                        "players": nba.get("total", 0)},
                "lol": {"score": lol_depth, "max": 3, "label": "LoL Esports",
                        "players": lol.get("total", 0)},
                "football": {"score": football_depth, "max": 4, "label": "FIFA WC 2026",
                             "players": football.get("total", 0)},
                "f1": {"score": f1_depth, "max": 4, "label": "F1 2026",
                       "players": f1.get("total", 0)},
            },
        }

    async def get_betting_coverage(self) -> dict:
        """Compute betting/odds coverage metrics from Cloudbet and Mollybet sources."""
        sr = source_records_table

        # Per-source event counts and match rates
        stmt = (
            sa.select(
                sr.c.source_id,
                sr.c.sport,
                sa.func.count().label("total_events"),
                sa.func.count(sr.c.event_id).label("matched_events"),
            )
            .where(
                sa.or_(
                    sr.c.source_id.like("cloudbet_%"),
                    sr.c.source_id.like("mollybet_%"),
                )
            )
            .group_by(sr.c.source_id, sr.c.sport)
        )
        result = await self._session.execute(stmt)
        source_stats = [
            {
                "source_id": r.source_id,
                "sport": r.sport,
                "total_events": r.total_events,
                "matched_events": r.matched_events,
                "match_rate": round(r.matched_events / r.total_events * 100, 1)
                if r.total_events > 0
                else 0.0,
            }
            for r in result.all()
        ]

        # Odds coverage per sport (Cloudbet only -- Mollybet is event-discovery)
        stmt = (
            sa.select(
                sr.c.sport,
                sa.func.count().label("total"),
                sa.func.count()
                .filter(sr.c.raw_data.has_key("odds"))
                .label("with_odds"),
            )
            .where(sr.c.source_id.like("cloudbet_%"))
            .group_by(sr.c.sport)
        )
        result = await self._session.execute(stmt)
        odds_by_sport = {}
        for r in result.all():
            odds_by_sport[r.sport] = {
                "total": r.total,
                "with_odds": r.with_odds,
                "pct": round(r.with_odds / r.total * 100, 1) if r.total > 0 else 0.0,
            }

        # Cross-source overlap: events matched to same canonical event
        cb = sr.alias("cb")
        mb = sr.alias("mb")
        try:
            stmt = (
                sa.select(
                    cb.c.sport,
                    sa.func.count(sa.func.distinct(cb.c.event_id)).label("overlap"),
                )
                .select_from(
                    cb.join(
                        mb,
                        sa.and_(
                            cb.c.event_id == mb.c.event_id,
                            cb.c.event_id.isnot(None),
                        ),
                    )
                )
                .where(
                    sa.and_(
                        cb.c.source_id.like("cloudbet_%"),
                        mb.c.source_id.like("mollybet_%"),
                    )
                )
                .group_by(cb.c.sport)
            )
            result = await self._session.execute(stmt)
            cross_source = {r.sport: r.overlap for r in result.all()}
        except Exception:
            cross_source = {}

        # Summary
        total_betting_events = sum(s["total_events"] for s in source_stats)
        total_with_odds = sum(v["with_odds"] for v in odds_by_sport.values())
        total_cloudbet = sum(
            s["total_events"] for s in source_stats if "cloudbet" in s["source_id"]
        )
        total_mollybet = sum(
            s["total_events"] for s in source_stats if "mollybet" in s["source_id"]
        )

        # Configured betting providers from registry
        providers = _registry.get_all_providers()
        betting_providers = [
            p
            for p in providers
            if p.source_id.startswith("cloudbet_") or p.source_id.startswith("mollybet_")
        ]

        return {
            "source_stats": source_stats,
            "odds_by_sport": odds_by_sport,
            "cross_source_overlap": cross_source,
            "total_betting_events": total_betting_events,
            "total_with_odds": total_with_odds,
            "total_cloudbet": total_cloudbet,
            "total_mollybet": total_mollybet,
            "providers_configured": len(betting_providers),
            "providers": [
                {
                    "source_id": p.source_id,
                    "display_name": p.display_name,
                    "sport": p.sport,
                    "type": p.type,
                }
                for p in betting_providers
            ],
        }

    async def _compute_event_enrichment(self) -> dict:
        """Count event enrichment coverage from the database."""
        t = events_table

        # Total scheduled events
        stmt = sa.select(sa.func.count()).select_from(t).where(t.c.status == "scheduled")
        result = await self._session.execute(stmt)
        total = result.scalar_one()

        if total == 0:
            return {"total_scheduled": 0, "with_rosters": 0, "with_team_stats": 0, "with_injuries": 0}

        # Events with enrichment data (metadata.teams is not null/empty)
        stmt = (
            sa.select(sa.func.count())
            .select_from(t)
            .where(
                sa.and_(
                    t.c.status == "scheduled",
                    t.c.metadata.has_key("teams"),
                )
            )
        )
        result = await self._session.execute(stmt)
        with_teams = result.scalar_one()

        return {
            "total_scheduled": total,
            "with_enrichment": with_teams,
            "enrichment_pct": round(with_teams / total * 100, 1) if total > 0 else 0.0,
        }

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
