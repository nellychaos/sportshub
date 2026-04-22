"""Seed team_aliases rows for the Cloudbet and Mollybet betting providers.

For every team in the canonical `teams` table, this script registers the team's
`name`, `short_name`, and `abbreviation` as aliases under each betting provider's
`source_id`. Hand-curated extra variants are also added for well-known NBA teams
(e.g. "LA Lakers" → Los Angeles Lakers) since Mollybet uses abbreviated city
forms that don't match any stored name field.

Idempotent: aliases already in the table are skipped.

Usage:
    python -m scripts.seed_betting_provider_aliases                 # dry run
    python -m scripts.seed_betting_provider_aliases --write         # actually insert
    python -m scripts.seed_betting_provider_aliases --write --providers cloudbet_basketball,mollybet_basket
"""

from __future__ import annotations

import argparse
import asyncio
import re
import unicodedata

import sqlalchemy as sa

from sportshub.db.engine import close_db, get_session_factory, init_db
from sportshub.db.tables import team_aliases_table, teams_table

# Sport → list of (source_id) that should be keyed off teams in that sport
SPORT_TO_PROVIDERS: dict[str, list[str]] = {
    "nba": ["cloudbet_basketball", "mollybet_basket"],
    "football": ["cloudbet_soccer", "mollybet_fb"],
    "lol": ["cloudbet_league_of_legends", "mollybet_esports"],
}

# Hand-curated NBA team name variants observed in Mollybet/Cloudbet feeds
# Maps canonical team `name` -> list of additional alias strings to register.
NBA_EXTRA_ALIASES: dict[str, list[str]] = {
    "Los Angeles Lakers":   ["LA Lakers", "LAL Lakers"],
    "Los Angeles Clippers": ["LA Clippers", "LAC Clippers"],
    "Golden State Warriors": ["GS Warriors", "Golden State", "GSW Warriors"],
    "New York Knicks":       ["NY Knicks", "New York"],
    "Brooklyn Nets":         ["BKN Nets", "Brooklyn"],
    "Philadelphia 76ers":    ["Philly 76ers", "Philadelphia", "Philly Sixers"],
    "Portland Trail Blazers":["Portland Blazers", "Blazers"],
    "Oklahoma City Thunder": ["OKC Thunder", "Oklahoma City", "OKC"],
    "San Antonio Spurs":     ["SA Spurs", "San Antonio"],
    "New Orleans Pelicans":  ["NO Pelicans", "New Orleans"],
    "Washington Wizards":    ["DC Wizards", "Washington"],
}


def normalize(s: str) -> str:
    """Match the resolution pipeline's normalisation exactly."""
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


async def plan_aliases(session, providers_filter: set[str] | None) -> list[dict]:
    """Build the list of (team_id, alias, source_id) rows to insert."""
    # Load all teams
    teams_stmt = sa.select(
        teams_table.c.id,
        teams_table.c.name,
        teams_table.c.short_name,
        teams_table.c.abbreviation,
        teams_table.c.sport,
    )
    teams = (await session.execute(teams_stmt)).all()

    # Load existing (normalized, source_id) pairs to avoid duplicates
    existing_stmt = sa.select(
        team_aliases_table.c.alias_normalized,
        team_aliases_table.c.source_id,
    )
    existing = {(r.alias_normalized, r.source_id) for r in (await session.execute(existing_stmt)).all()}

    planned: list[dict] = []
    for t in teams:
        providers = SPORT_TO_PROVIDERS.get(t.sport, [])
        if providers_filter is not None:
            providers = [p for p in providers if p in providers_filter]
        if not providers:
            continue

        # Candidate alias strings
        candidates: list[str] = []
        for s in (t.name, t.short_name, t.abbreviation):
            if s and s not in candidates:
                candidates.append(s)
        # NBA-only hand-curated extras
        if t.sport == "nba":
            for extra in NBA_EXTRA_ALIASES.get(t.name, []):
                if extra not in candidates:
                    candidates.append(extra)

        for alias_text in candidates:
            norm = normalize(alias_text)
            if not norm:
                continue
            for source_id in providers:
                if (norm, source_id) in existing:
                    continue
                planned.append({
                    "team_id": t.id,
                    "alias": alias_text,
                    "alias_normalized": norm,
                    "source_id": source_id,
                    "is_primary": False,
                })
                # Reserve so two alias strings that normalise the same aren't duplicated in one run
                existing.add((norm, source_id))

    return planned


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Actually insert (default: dry run)")
    parser.add_argument(
        "--providers",
        type=str,
        default=None,
        help="Comma-separated subset of source_ids to target (e.g. 'mollybet_basket,cloudbet_basketball')",
    )
    args = parser.parse_args()

    providers_filter: set[str] | None = None
    if args.providers:
        providers_filter = {p.strip() for p in args.providers.split(",") if p.strip()}

    await init_db()
    factory = get_session_factory()
    try:
        async with factory() as session:
            planned = await plan_aliases(session, providers_filter)
            by_source: dict[str, int] = {}
            for row in planned:
                by_source[row["source_id"]] = by_source.get(row["source_id"], 0) + 1

            print(f"\nPlanned {len(planned)} new alias rows.")
            print(f"{'source_id':<35} {'new aliases':>12}")
            print("─" * 50)
            for sid in sorted(by_source):
                print(f"{sid:<35} {by_source[sid]:>12}")
            if not planned:
                print("  (nothing to do)")

            if args.write and planned:
                await session.execute(sa.insert(team_aliases_table), planned)
                await session.commit()
                print(f"\n✓ Inserted {len(planned)} alias rows.")
            elif planned:
                print("\n(dry run — rerun with --write to apply)")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
