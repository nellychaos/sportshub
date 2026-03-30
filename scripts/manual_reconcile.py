"""CLI tool for manual dedup review and reconciliation.

Usage:
    python -m scripts.manual_reconcile --show-unmatched
    python -m scripts.manual_reconcile --show-low-confidence
    python -m scripts.manual_reconcile --merge EVENT_ID_1 EVENT_ID_2
    python -m scripts.manual_reconcile --add-alias TEAM_ID ALIAS SOURCE_ID
"""

import argparse
import asyncio
from uuid import UUID

from sportshub.db.engine import init_db, get_session_factory, close_db


async def show_unmatched() -> None:
    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        from sportshub.db.repositories import SourceRecordRepository
        repo = SourceRecordRepository(session)
        records = await repo.get_unmatched()
        print(f"\n--- {len(records)} Unmatched Source Records ---\n")
        for r in records:
            print(f"  [{r.source_id}] {r.raw_home_team} vs {r.raw_away_team}")
            print(f"    scheduled_at: {r.scheduled_at}")
            print(f"    competition: {r.raw_competition}")
            print(f"    id: {r.id}\n")
    await close_db()


async def show_low_confidence() -> None:
    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        import sqlalchemy as sa
        from sportshub.db.tables import events_table
        from sportshub.models import Event

        stmt = (
            sa.select(events_table)
            .where(events_table.c.confidence_score < 0.70)
            .order_by(events_table.c.confidence_score.asc())
        )
        result = await session.execute(stmt)
        rows = result.all()
        print(f"\n--- {len(rows)} Low Confidence Events ---\n")
        for row in rows:
            mapping = dict(row._mapping)
            print(f"  Event {mapping['id']}")
            print(f"    confidence: {mapping['confidence_score']}")
            print(f"    sources: {mapping['source_count']}")
            print(f"    scheduled_at: {mapping['scheduled_at']}\n")
    await close_db()


async def add_alias(team_id: str, alias: str, source_id: str) -> None:
    import re
    import unicodedata

    await init_db()
    factory = get_session_factory()
    async with factory() as session:
        from sportshub.db.repositories import TeamRepository
        from sportshub.models import TeamAlias

        repo = TeamRepository(session)
        normalized = alias.strip().lower()
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(c for c in normalized if not unicodedata.combining(c))
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        ta = TeamAlias(
            team_id=UUID(team_id),
            alias=alias,
            alias_normalized=normalized,
            source_id=source_id,
        )
        await repo.add_alias(ta)
        await session.commit()
        print(f"Added alias '{alias}' -> team {team_id} (source: {source_id})")
    await close_db()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sportshub manual reconciliation tool")
    parser.add_argument("--show-unmatched", action="store_true", help="Show unmatched source records")
    parser.add_argument("--show-low-confidence", action="store_true", help="Show low-confidence events")
    parser.add_argument("--add-alias", nargs=3, metavar=("TEAM_ID", "ALIAS", "SOURCE_ID"),
                        help="Add a team alias")
    args = parser.parse_args()

    if args.show_unmatched:
        asyncio.run(show_unmatched())
    elif args.show_low_confidence:
        asyncio.run(show_low_confidence())
    elif args.add_alias:
        asyncio.run(add_alias(*args.add_alias))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
