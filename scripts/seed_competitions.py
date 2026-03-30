"""Seed competition records from JSON data.

Usage: python -m scripts.seed_competitions
"""

import asyncio
import json
from pathlib import Path

from sportshub.config import get_settings
from sportshub.db.engine import init_db, get_session_factory, close_db
from sportshub.models import Competition, Sport


async def seed_competitions() -> None:
    await init_db()
    factory = get_session_factory()

    data_dir = Path(__file__).resolve().parent.parent / "data"
    filepath = data_dir / "competitions.json"

    if not filepath.exists():
        print("competitions.json not found")
        return

    with open(filepath) as f:
        competitions_data = json.load(f)

    async with factory() as session:
        from sportshub.db.repositories import CompetitionRepository
        repo = CompetitionRepository(session)

        created = 0
        skipped = 0

        for cd in competitions_data:
            comp = Competition(
                name=cd["name"],
                short_name=cd["short_name"],
                sport=Sport(cd["sport"]),
                season=cd.get("season"),
                region=cd.get("region"),
                tier=cd.get("tier"),
                metadata=cd.get("metadata", {}),
            )

            try:
                await repo.create(comp)
                created += 1
            except Exception:
                await session.rollback()
                skipped += 1

        await session.commit()
        print(f"Competitions: {created} created, {skipped} skipped")

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed_competitions())
