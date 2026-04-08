"""Seed canonical teams and aliases from JSON data files.

Usage: python -m scripts.seed_teams
"""

import asyncio
import json
from pathlib import Path

from sportshub.db.engine import init_db, get_session_factory, close_db
from sportshub.models import Sport, Team, TeamAlias
from sportshub.scripts.normalization import normalize_alias


async def seed_teams() -> None:
    await init_db()
    factory = get_session_factory()

    data_dir = Path(__file__).resolve().parent.parent / "data"

    for filename, sport in [
        ("nba_teams.json", Sport.NBA),
        ("lol_teams.json", Sport.LOL),
        ("fifa_wc_teams.json", Sport.FOOTBALL),
    ]:
        filepath = data_dir / filename
        if not filepath.exists():
            print(f"Skipping {filename} (not found)")
            continue

        with open(filepath) as f:
            teams_data = json.load(f)

        async with factory() as session:
            from sportshub.db.repositories import TeamRepository
            repo = TeamRepository(session)

            created = 0
            skipped = 0
            aliases_added = 0
            alias_skipped = 0

            for td in teams_data:
                team = Team(
                    name=td["name"],
                    short_name=td["short_name"],
                    abbreviation=td.get("abbreviation") or td.get("short_name", ""),
                    sport=sport,
                    metadata=td.get("metadata", {}),
                )

                # Use savepoint so failures don't roll back the whole transaction
                try:
                    async with session.begin_nested():
                        team = await repo.create(team)
                    created += 1
                except Exception:
                    # Already exists — find it by abbreviation
                    existing = await repo.get_all(sport=sport)
                    team_match = None
                    target_abbrev = td.get("abbreviation") or td.get("short_name", "")
                    for t in existing[0]:
                        if t.abbreviation == target_abbrev:
                            team_match = t
                            break
                    if team_match:
                        team = team_match
                        skipped += 1
                    else:
                        print(f"  ERROR: Could not create or find team {td['name']}")
                        continue

                # Add aliases (each in its own savepoint)
                for alias_data in td.get("aliases", []):
                    alias = TeamAlias(
                        team_id=team.id,
                        alias=alias_data["alias"],
                        alias_normalized=normalize_alias(alias_data["alias"]),
                        source_id=alias_data.get("source_id") or "global",
                        is_primary=alias_data.get("is_primary", False),
                    )
                    try:
                        async with session.begin_nested():
                            await repo.add_alias(alias)
                        aliases_added += 1
                    except Exception:
                        # Alias already exists (unique constraint), skip
                        alias_skipped += 1

            await session.commit()
            print(
                f"{filename}: {created} created, {skipped} skipped, "
                f"{aliases_added} aliases added, {alias_skipped} aliases skipped (dupes)"
            )

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed_teams())
