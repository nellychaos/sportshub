"""Seed player records from JSON data (placeholder — players are synced from sources in production).

Usage: python -m scripts.seed_players
"""

import asyncio
import json
from pathlib import Path

from sportshub.db.engine import init_db, get_session_factory, close_db
from sportshub.models import Player, PlayerAlias, Sport
from sportshub.scripts.normalization import normalize_alias


async def seed_players() -> None:
    await init_db()
    factory = get_session_factory()
    data_dir = Path(__file__).resolve().parent.parent / "data"

    for filename, sport in [
        ("nba_players.json", Sport.NBA),
        ("lol_players.json", Sport.LOL),
        ("fifa_wc_players.json", Sport.FOOTBALL),
    ]:
        filepath = data_dir / filename
        if not filepath.exists():
            print(f"Skipping {filename} (not found)")
            continue

        with open(filepath) as f:
            players_data = json.load(f)

        async with factory() as session:
            from sportshub.db.repositories import PlayerRepository
            repo = PlayerRepository(session)

            created = 0
            for pd in players_data:
                player = Player(
                    name=pd["name"],
                    sport=sport,
                    position=pd.get("position"),
                    role=pd.get("role"),
                    jersey_number=pd.get("jersey_number"),
                    nationality=pd.get("nationality"),
                    metadata=pd.get("metadata", {}),
                )
                try:
                    player = await repo.create(player)
                    created += 1

                    for alias_data in pd.get("aliases", []):
                        alias = PlayerAlias(
                            player_id=player.id,
                            alias=alias_data["alias"],
                            alias_normalized=normalize_alias(alias_data["alias"]),
                            source_id=alias_data["source_id"],
                        )
                        try:
                            await repo.add_alias(alias)
                        except Exception:
                            await session.rollback()
                except Exception:
                    await session.rollback()

            await session.commit()
            print(f"{filename}: {created} players seeded")

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed_players())
