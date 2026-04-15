"""Demo page data service.

Loads curated examples from the project's JSON data files to showcase
the depth and richness of multi-source sports data aggregation.
"""

from __future__ import annotations

import json
from datetime import date
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

        f1_drivers = _load("f1_drivers_2026.json")
        f1_constructors = _load("f1_constructors_2026.json")
        f1_schedule = _load("f1_schedule_2026.json")
        f1_circuits = _load("f1_circuits.json")

        provider_list = []
        if isinstance(providers, dict) and "providers" in providers:
            provider_list = providers["providers"]
            if isinstance(provider_list, dict):
                provider_list = list(provider_list.values())

        return {
            "nba": self._nba_showcase(nba_stats),
            "fifa": self._fifa_showcase(fifa_players, fifa_teams),
            "lol": self._lol_showcase(lol_teams),
            "f1": self._f1_showcase(f1_drivers, f1_constructors, f1_schedule, f1_circuits),
            "coverage": self._coverage_stats(
                nba_stats, fifa_players, fifa_teams, lol_teams, provider_list,
                f1_drivers, f1_constructors,
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

    # -- F1 -----------------------------------------------------------------

    def _f1_showcase(
        self,
        drivers: list[dict],
        constructors: list[dict],
        schedule: list[dict],
        circuits: list[dict],
    ) -> dict:
        today = date.today().isoformat()

        # Calendar data
        past_rounds = [r["round"] for r in schedule if r.get("date", "") < today]
        next_race = None
        for r in schedule:
            if r.get("date", "") >= today:
                next_race = r
                break

        # Build circuit lookup for enriching schedule with circuit info
        circuit_map = {c["circuit_id"]: c for c in circuits}

        # Enrich each race with circuit metadata (type, svg, corners)
        for race in schedule:
            cid = race.get("circuit_id")
            circ = circuit_map.get(cid)
            if circ:
                tl = circ.get("track_layout") or {}
                race["_circuit_type"] = circ.get("circuit_type")
                race["_svg_url"] = circ.get("svg_track_map_url")
                race["_circuit_image_url"] = circ.get("circuit_image_url")
                race["_num_corners"] = tl.get("num_corners")
                race["_lat"] = circ.get("latitude")
                race["_lon"] = circ.get("longitude")

        # Drivers sorted by team then championship position (exclude reserves)
        active_drivers = [d for d in drivers if d.get("team_id")]
        sorted_drivers = sorted(
            active_drivers,
            key=lambda d: (
                d.get("team_name") or "ZZZ",
                int(d.get("championship_position") or 99),
            ),
        )

        by_team: dict[str, list[dict]] = {}
        for d in sorted_drivers:
            by_team.setdefault(d["team_id"], []).append(d)

        # Depth stats
        circuits_with_geo = sum(1 for c in circuits if (c.get("track_layout") or {}).get("x"))
        total_corners = sum(len((c.get("track_layout") or {}).get("corners", [])) for c in circuits)

        return {
            "calendar": {
                "races": schedule,
                "total": len(schedule),
                "sprint_count": sum(1 for r in schedule if r.get("is_sprint_weekend")),
                "next_race": next_race,
                "past_rounds": past_rounds,
            },
            "drivers": {
                "grid": sorted_drivers,
                "total": len(sorted_drivers),
                "by_team": by_team,
            },
            "constructors": {
                "teams": constructors,
                "total": len(constructors),
            },
            "depth": {
                "total_drivers": len(sorted_drivers),
                "total_constructors": len(constructors),
                "total_races": len(schedule),
                "total_circuits": len(circuits),
                "circuits_with_geometry": circuits_with_geo,
                "total_corners": total_corners,
                "sprint_weekends": sum(1 for r in schedule if r.get("is_sprint_weekend")),
            },
        }

    # -- Coverage Stats ------------------------------------------------------

    def _coverage_stats(
        self,
        nba_stats: dict,
        fifa_players: list,
        fifa_teams: list,
        lol_teams: list,
        providers: list,
        f1_drivers: list | None = None,
        f1_constructors: list | None = None,
    ) -> dict:
        nba_count = len(nba_stats.get("players", []))
        f1_driver_count = len(f1_drivers) if f1_drivers else 0
        f1_constructor_count = len(f1_constructors) if f1_constructors else 0
        return {
            "provider_count": len(providers),
            "sport_count": 4,
            "total_athletes": nba_count + len(fifa_players) + f1_driver_count,
            "total_teams": 30 + len(fifa_teams) + len(lol_teams) + f1_constructor_count,
            "providers": [
                {
                    "name": p.get("display_name", p.get("source_id", "?")),
                    "sport": p.get("sport", ""),
                    "type": p.get("type", ""),
                }
                for p in providers
            ],
        }
