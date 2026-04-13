"""Demo page data service.

Loads curated examples from the project's JSON data files to showcase
the depth and richness of multi-source sports data aggregation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
if not _DATA_DIR.is_dir():
    _DATA_DIR = Path("/app/data")


def _load(filename: str) -> Any:
    path = _DATA_DIR / filename
    if not path.exists():
        return [] if filename.endswith(".json") else {}
    with open(path) as f:
        return json.load(f)


def _find(records: list[dict], key: str, substr: str) -> dict | None:
    for r in records:
        if substr.lower() in r.get(key, "").lower():
            return r
    return None


def _find_team(teams: list[dict], name: str) -> dict | None:
    for t in teams:
        if t.get("name", "").lower() == name.lower():
            return t
        if t.get("short_name", "").lower() == name.lower():
            return t
    return None


class DemoService:
    """Loads curated showcase data from flat JSON files."""

    def get_demo_data(self) -> dict:
        """Return all data needed for the demo page."""
        nba_stats = _load("nba_player_stats.json")
        fifa_players = _load("fifa_wc_players.json")
        fifa_teams = _load("fifa_wc_teams.json")
        lol_teams = _load("lol_teams.json")
        providers = _load("providers.json")

        provider_list = []
        if isinstance(providers, dict) and "providers" in providers:
            provider_list = providers["providers"]
            if isinstance(provider_list, dict):
                provider_list = list(provider_list.values())

        return {
            "nba": self._nba_showcase(nba_stats),
            "fifa": self._fifa_showcase(fifa_players, fifa_teams),
            "lol": self._lol_showcase(lol_teams),
            "coverage": self._coverage_stats(
                nba_stats, fifa_players, fifa_teams, lol_teams, provider_list
            ),
        }

    # -- NBA -----------------------------------------------------------------

    def _nba_showcase(self, nba_stats: dict) -> dict:
        players = nba_stats.get("players", [])
        season = nba_stats.get("season", "2025-26")

        # Featured: Luka Doncic
        luka = _find(players, "player_name", "Doncic")

        # Second star for comparison: Shai Gilgeous-Alexander
        sga = _find(players, "player_name", "Gilgeous")

        # Top scorers for leaderboard
        scorers = sorted(
            [p for p in players if p.get("games_played", 0) >= 40],
            key=lambda p: p.get("stats", {}).get("points_per_game", 0),
            reverse=True,
        )[:5]

        # Count stat fields on Luka to show depth
        field_count = 0
        if luka:
            field_count += len(luka.get("stats", {}))
            field_count += len(luka.get("advanced", {}))
            field_count += len(luka.get("play_by_play", {}))
            field_count += len(luka.get("adjusted_shooting", {}))
            field_count += 3  # games_played, games_started, minutes_per_game

        return {
            "season": season,
            "total_players": len(players),
            "featured": luka,
            "comparison": sga,
            "top_scorers": scorers,
            "field_count": field_count,
        }

    # -- FIFA ----------------------------------------------------------------

    def _fifa_showcase(
        self, players: list[dict], teams: list[dict]
    ) -> dict:
        mbappe = _find(players, "name", "Mbapp")
        pulisic = _find(players, "name", "Pulisic")

        usa = _find_team(teams, "United States")
        brazil = _find_team(teams, "Brazil")
        france = _find_team(teams, "France")

        # Count enrichment stats
        with_espn = sum(
            1
            for p in players
            if p.get("metadata", {}).get("club_goals_2025_26") is not None
        )
        avg_aliases = (
            sum(len(p.get("aliases", [])) for p in players) / len(players)
            if players
            else 0
        )

        return {
            "total_players": len(players),
            "total_teams": len(teams),
            "featured_player": mbappe,
            "secondary_player": pulisic,
            "featured_teams": [t for t in [usa, brazil, france] if t],
            "with_espn_stats": with_espn,
            "avg_aliases": round(avg_aliases, 1),
        }

    # -- LoL -----------------------------------------------------------------

    def _lol_showcase(self, teams: list[dict]) -> dict:
        ranked = sorted(
            [
                t
                for t in teams
                if t.get("metadata", {}).get("win_rate") is not None
            ],
            key=lambda t: t.get("metadata", {}).get("win_rate", 0),
            reverse=True,
        )

        return {
            "total_teams": len(teams),
            "leaderboard": ranked[:8],
            "featured": _find_team(teams, "Gen.G"),
        }

    # -- Coverage Stats ------------------------------------------------------

    def _coverage_stats(
        self,
        nba_stats: dict,
        fifa_players: list,
        fifa_teams: list,
        lol_teams: list,
        providers: list,
    ) -> dict:
        nba_count = len(nba_stats.get("players", []))
        return {
            "provider_count": len(providers),
            "sport_count": 3,
            "total_athletes": nba_count + len(fifa_players),
            "total_teams": 30 + len(fifa_teams) + len(lol_teams),
            "providers": [
                {
                    "name": p.get("display_name", p.get("source_id", "?")),
                    "sport": p.get("sport", ""),
                    "type": p.get("type", ""),
                }
                for p in providers
            ],
        }
