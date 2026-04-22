"""Diagnose low match rate between Cloudbet and Mollybet providers.

Runs a sequence of queries against the configured database to identify where
cross-provider event matching is losing records. Output highlights the likely
bottleneck (team alias coverage, sport/time mismatch, or candidate shortage).

Usage:
    python -m scripts.diagnose_cloudbet_mollybet_match
    DATABASE_URL=postgresql+asyncpg://... python -m scripts.diagnose_cloudbet_mollybet_match
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import sqlalchemy as sa

from sportshub.db.engine import close_db, get_session_factory, init_db
from sportshub.db.tables import (
    events_table,
    source_records_table,
    team_aliases_table,
    teams_table,
)

CLOUDBET_IDS = ["cloudbet_basketball", "cloudbet_soccer", "cloudbet_league_of_legends"]
MOLLYBET_IDS = ["mollybet_basket", "mollybet_fb", "mollybet_esports"]
ALL_IDS = CLOUDBET_IDS + MOLLYBET_IDS


def section(title: str) -> None:
    print("\n" + "═" * 70)
    print(f" {title}")
    print("═" * 70)


def row(label: str, value: object, width: int = 38) -> None:
    print(f"  {label:<{width}} {value}")


async def q1_record_counts(session) -> dict[str, dict[str, int]]:
    """Total records and matched/unmatched counts per provider source_id."""
    section("1. Record counts per provider")
    stmt = (
        sa.select(
            source_records_table.c.source_id,
            source_records_table.c.sport,
            sa.func.count().label("total"),
            sa.func.sum(
                sa.case((source_records_table.c.event_id.is_not(None), 1), else_=0)
            ).label("matched"),
        )
        .where(source_records_table.c.source_id.in_(ALL_IDS))
        .group_by(source_records_table.c.source_id, source_records_table.c.sport)
        .order_by(source_records_table.c.source_id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    counts: dict[str, dict[str, int]] = {}
    print(f"  {'source_id':<32} {'sport':<10} {'total':>8} {'matched':>9} {'rate':>8}")
    print("  " + "─" * 68)
    for r in rows:
        sid, sport, total, matched = r.source_id, r.sport, r.total, r.matched or 0
        rate = (matched / total * 100) if total else 0.0
        print(f"  {sid:<32} {sport:<10} {total:>8} {matched:>9} {rate:>7.1f}%")
        counts.setdefault(sid, {})[sport] = {"total": total, "matched": matched}

    if not rows:
        print("  (no records found — has ingestion been running?)")
    return counts


async def q2_alias_coverage(session) -> dict[str, int]:
    """Count team aliases registered per source_id."""
    section("2. Team alias coverage (per source_id)")
    stmt = (
        sa.select(
            team_aliases_table.c.source_id,
            sa.func.count().label("alias_count"),
            sa.func.count(sa.distinct(team_aliases_table.c.team_id)).label("teams_covered"),
        )
        .where(team_aliases_table.c.source_id.in_(ALL_IDS))
        .group_by(team_aliases_table.c.source_id)
        .order_by(team_aliases_table.c.source_id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    counts: dict[str, int] = {}
    print(f"  {'source_id':<32} {'aliases':>9} {'teams':>8}")
    print("  " + "─" * 52)
    for r in rows:
        print(f"  {r.source_id:<32} {r.alias_count:>9} {r.teams_covered:>8}")
        counts[r.source_id] = r.alias_count

    for sid in ALL_IDS:
        if sid not in counts:
            print(f"  {sid:<32} {'0':>9} {'0':>8}  ← NO ALIASES REGISTERED")
            counts[sid] = 0

    return counts


async def q3_unmatched_samples(session) -> None:
    """Sample unmatched raw team names to see what's failing resolution."""
    section("3. Sample unmatched records (10 per provider)")
    for sid in ALL_IDS:
        stmt = (
            sa.select(
                source_records_table.c.raw_home_team,
                source_records_table.c.raw_away_team,
                source_records_table.c.sport,
                source_records_table.c.scheduled_at,
                source_records_table.c.raw_competition,
            )
            .where(
                source_records_table.c.source_id == sid,
                source_records_table.c.event_id.is_(None),
            )
            .order_by(source_records_table.c.created_at.desc())
            .limit(10)
        )
        result = await session.execute(stmt)
        rows = result.all()
        if not rows:
            continue
        print(f"\n  ── {sid} ({len(rows)} samples) ──")
        for r in rows:
            print(f"    [{r.sport}] {r.raw_home_team!r:<35} vs {r.raw_away_team!r:<35}  @ {r.scheduled_at}  ({r.raw_competition})")


async def q4_alias_lookup_test(session) -> None:
    """Test whether raw team names from unmatched records exist in team_aliases."""
    section("4. Alias lookup test — are unmatched team names resolvable?")
    for sid in ALL_IDS:
        unmatched_teams_stmt = (
            sa.select(source_records_table.c.raw_home_team)
            .where(
                source_records_table.c.source_id == sid,
                source_records_table.c.event_id.is_(None),
            )
            .distinct()
            .limit(50)
        )
        unmatched = [r[0] for r in (await session.execute(unmatched_teams_stmt)).all()]
        if not unmatched:
            continue

        # Check how many of these appear as aliases (any source_id) in team_aliases
        any_alias_stmt = sa.select(team_aliases_table.c.alias_normalized).where(
            sa.func.lower(team_aliases_table.c.alias).in_([t.lower() for t in unmatched])
        )
        resolvable_any = {r[0] for r in (await session.execute(any_alias_stmt)).all()}

        # Check how many appear keyed by THIS source_id specifically
        scoped_alias_stmt = sa.select(team_aliases_table.c.alias_normalized).where(
            team_aliases_table.c.source_id == sid,
            sa.func.lower(team_aliases_table.c.alias).in_([t.lower() for t in unmatched]),
        )
        resolvable_scoped = {r[0] for r in (await session.execute(scoped_alias_stmt)).all()}

        print(f"\n  ── {sid} ──")
        row("  Unmatched unique team names sampled", len(unmatched))
        row("  Resolvable via ANY source alias", len(resolvable_any))
        row(f"  Resolvable via '{sid}' alias specifically", len(resolvable_scoped))
        unresolvable_pct = (1 - len(resolvable_any) / len(unmatched)) * 100 if unmatched else 0
        row("  Unresolvable by any alias", f"{unresolvable_pct:.1f}% of sampled")


async def q5_cross_provider_overlap(session) -> None:
    """Events that have records from BOTH cloudbet AND mollybet."""
    section("5. Cross-provider overlap (events with both providers)")

    # Get matched event_ids per provider family
    cloudbet_events_stmt = (
        sa.select(source_records_table.c.event_id, source_records_table.c.sport)
        .where(
            source_records_table.c.source_id.in_(CLOUDBET_IDS),
            source_records_table.c.event_id.is_not(None),
        )
        .distinct()
    )
    mollybet_events_stmt = (
        sa.select(source_records_table.c.event_id, source_records_table.c.sport)
        .where(
            source_records_table.c.source_id.in_(MOLLYBET_IDS),
            source_records_table.c.event_id.is_not(None),
        )
        .distinct()
    )
    cb = {(r.event_id, r.sport) for r in (await session.execute(cloudbet_events_stmt)).all()}
    mb = {(r.event_id, r.sport) for r in (await session.execute(mollybet_events_stmt)).all()}

    cb_by_sport: dict[str, set] = defaultdict(set)
    mb_by_sport: dict[str, set] = defaultdict(set)
    for eid, sport in cb:
        cb_by_sport[sport].add(eid)
    for eid, sport in mb:
        mb_by_sport[sport].add(eid)

    sports = sorted(set(cb_by_sport) | set(mb_by_sport))
    print(f"  {'sport':<10} {'cb events':>10} {'mb events':>10} {'overlap':>8} {'cb-only':>8} {'mb-only':>8}")
    print("  " + "─" * 62)
    for sport in sports:
        cb_ids, mb_ids = cb_by_sport[sport], mb_by_sport[sport]
        overlap = cb_ids & mb_ids
        print(
            f"  {sport:<10} {len(cb_ids):>10} {len(mb_ids):>10} {len(overlap):>8} "
            f"{len(cb_ids - mb_ids):>8} {len(mb_ids - cb_ids):>8}"
        )


async def q6_scheduled_at_precision(session) -> None:
    """How precise are the scheduled_at timestamps? Sub-minute drift breaks joins."""
    section("6. scheduled_at precision per provider")
    stmt = (
        sa.select(
            source_records_table.c.source_id,
            sa.func.count().label("total"),
            sa.func.sum(
                sa.case(
                    (sa.func.extract("second", source_records_table.c.scheduled_at) == 0, 1),
                    else_=0,
                )
            ).label("whole_minute"),
            sa.func.sum(
                sa.case(
                    (
                        sa.and_(
                            sa.func.extract("minute", source_records_table.c.scheduled_at) == 0,
                            sa.func.extract("second", source_records_table.c.scheduled_at) == 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("whole_hour"),
        )
        .where(source_records_table.c.source_id.in_(ALL_IDS))
        .group_by(source_records_table.c.source_id)
        .order_by(source_records_table.c.source_id)
    )
    result = await session.execute(stmt)
    print(f"  {'source_id':<32} {'total':>8} {'min=00s':>10} {':00:00':>10}")
    print("  " + "─" * 62)
    for r in result.all():
        total = r.total or 0
        wm = r.whole_minute or 0
        wh = r.whole_hour or 0
        print(f"  {r.source_id:<32} {total:>8} {wm:>9} ({(wm/total*100 if total else 0):>3.0f}%) {wh:>5} ({(wh/total*100 if total else 0):>3.0f}%)")


async def q7_time_drift(session) -> None:
    """For overlapping sport coverage, measure time difference between provider records."""
    section("7. Time drift between Cloudbet and Mollybet on same teams")
    # Find records where the same raw team names appear in both providers
    # and check scheduled_at difference
    cb_alias = source_records_table.alias("cb")
    mb_alias = source_records_table.alias("mb")
    stmt = (
        sa.select(
            cb_alias.c.sport,
            cb_alias.c.raw_home_team.label("cb_home"),
            cb_alias.c.raw_away_team.label("cb_away"),
            mb_alias.c.raw_home_team.label("mb_home"),
            mb_alias.c.raw_away_team.label("mb_away"),
            cb_alias.c.scheduled_at.label("cb_time"),
            mb_alias.c.scheduled_at.label("mb_time"),
        )
        .select_from(
            cb_alias.join(
                mb_alias,
                sa.and_(
                    cb_alias.c.sport == mb_alias.c.sport,
                    sa.func.abs(
                        sa.extract("epoch", cb_alias.c.scheduled_at - mb_alias.c.scheduled_at)
                    ) < 86400,  # within 24h
                    sa.func.lower(cb_alias.c.raw_home_team) == sa.func.lower(mb_alias.c.raw_home_team),
                ),
            )
        )
        .where(
            cb_alias.c.source_id.in_(CLOUDBET_IDS),
            mb_alias.c.source_id.in_(MOLLYBET_IDS),
        )
        .limit(20)
    )
    try:
        result = await session.execute(stmt)
        rows = result.all()
    except Exception as exc:
        print(f"  query failed: {exc}")
        return

    if not rows:
        print("  No records found where home team names match exactly between providers.")
        print("  → strongly suggests naming differs (e.g., 'LA Lakers' vs 'Los Angeles Lakers')")
        return

    print(f"  {len(rows)} pairs with matching home team name:")
    for r in rows[:10]:
        delta = abs((r.cb_time - r.mb_time).total_seconds())
        print(f"    [{r.sport}] {r.cb_home} vs {r.cb_away}  Δ={delta:.0f}s  cb={r.cb_time} mb={r.mb_time}")


async def main() -> None:
    await init_db()
    factory = get_session_factory()
    try:
        async with factory() as session:
            counts = await q1_record_counts(session)
            aliases = await q2_alias_coverage(session)
            await q3_unmatched_samples(session)
            await q4_alias_lookup_test(session)
            await q5_cross_provider_overlap(session)
            await q6_scheduled_at_precision(session)
            await q7_time_drift(session)

        # ── Summary ─────────────────────────────────────────────────────
        section("Summary / likely bottleneck")
        zero_alias_providers = [sid for sid in ALL_IDS if aliases.get(sid, 0) == 0]
        if zero_alias_providers:
            print("  ⚠ Providers with ZERO team aliases registered:")
            for sid in zero_alias_providers:
                print(f"      - {sid}")
            print()
            print("  → This is almost certainly the dominant failure.")
            print("    Every raw team name from these providers will fail TeamResolver.resolve()")
            print("    and the record will be dropped before matching attempts.")
            print()
            print("    Fix: seed team_aliases for each provider. Either manually via")
            print("    `python -m scripts.manual_reconcile --add-alias <team_id> <alias> <source_id>`")
            print("    or write a one-off fuzzy-match pass using the existing teams table.")
        else:
            print("  All providers have some alias coverage. Check q4 (unresolvable %)")
            print("  and q7 (time drift) to narrow down further.")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
