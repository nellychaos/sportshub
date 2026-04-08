"""Fetch NBA player season averages from ESPN public API.

Creates data/nba_player_stats.json with per-game averages for all players
in the current roster. Uses the ESPN athlete overview endpoint which provides
GP, MIN, FG%, 3P%, FT%, REB, AST, BLK, STL, PF, TO, PTS.

For more detailed splits (FG made/attempted, OREB/DREB), falls back to the
athlete stats endpoint.

Usage: python3 -m scripts.fetch_nba_player_stats
       python3 -m scripts.fetch_nba_player_stats --team BOS
       python3 -m scripts.fetch_nba_player_stats --detailed  # fetch full splits (slower)
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.http import ScriptHttpClient
from sportshub.scripts.io import load_data, save_data

_client = ScriptHttpClient("espn_nba")


def fetch_json(url: str) -> dict | None:
    try:
        return _client.get(url, timeout=15)
    except (HTTPError, URLError, TimeoutError, Exception):
        return None


def parse_overview_stats(data: dict) -> dict | None:
    """Parse the overview endpoint for basic season averages."""
    stats = data.get("statistics", {})
    labels = stats.get("labels", [])
    names = stats.get("names", [])
    splits = stats.get("splits", [])

    # Find the regular season split
    for split in splits:
        if "regular" in split.get("displayName", "").lower():
            values = split.get("stats", [])
            if not values:
                continue

            stat_map = {}
            for label, name, val in zip(labels, names, values):
                try:
                    stat_map[name] = float(val) if "." in str(val) else int(val)
                except (ValueError, TypeError):
                    stat_map[name] = val

            return {
                "games_played": stat_map.get("gamesPlayed", 0),
                "minutes_per_game": stat_map.get("avgMinutes", 0),
                "stats": {
                    "points_per_game": stat_map.get("avgPoints", 0),
                    "rebounds_per_game": stat_map.get("avgRebounds", 0),
                    "assists_per_game": stat_map.get("avgAssists", 0),
                    "steals_per_game": stat_map.get("avgSteals", 0),
                    "blocks_per_game": stat_map.get("avgBlocks", 0),
                    "turnovers_per_game": stat_map.get("avgTurnovers", 0),
                    "personal_fouls_per_game": stat_map.get("avgFouls", 0),
                    "field_goal_pct": stat_map.get("fieldGoalPct", 0),
                    "three_point_pct": stat_map.get("threePointPct", 0),
                    "free_throw_pct": stat_map.get("freeThrowPct", 0),
                },
            }

    return None


def parse_detailed_stats(data: dict) -> dict | None:
    """Parse the stats endpoint for detailed splits with made/attempted."""
    cats = data.get("categories", [])

    # Find the averages category
    averages = None
    for c in cats:
        if c.get("name") == "averages":
            averages = c
            break

    if not averages:
        return None

    labels = averages.get("labels", [])
    # Data is in 'statistics' not 'splits' -- ordered by season, most recent last
    statistics = averages.get("statistics", [])

    if not statistics:
        return None

    # Use the most recent season entry (last in list)
    entry = statistics[-1]
    values = entry.get("stats", [])
    if not values:
        return None

    stat_map = dict(zip(labels, values))

    # Parse FG made-attempted format: "9.2-19.5"
    def parse_made_attempted(val):
        if isinstance(val, str) and "-" in val:
            parts = val.split("-")
            if len(parts) == 2:
                try:
                    return float(parts[0]), float(parts[1])
                except ValueError:
                    pass
        return None, None

    fg_made, fg_att = parse_made_attempted(stat_map.get("FG"))
    tp_made, tp_att = parse_made_attempted(stat_map.get("3PT"))
    ft_made, ft_att = parse_made_attempted(stat_map.get("FT"))

    def safe_float(v, default=0):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def safe_int(v, default=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    result = {
        "games_played": safe_int(stat_map.get("GP")),
        "games_started": safe_int(stat_map.get("GS")),
        "minutes_per_game": safe_float(stat_map.get("MIN")),
        "stats": {
            "points_per_game": safe_float(stat_map.get("PTS")),
            "rebounds_per_game": safe_float(stat_map.get("REB")),
            "assists_per_game": safe_float(stat_map.get("AST")),
            "steals_per_game": safe_float(stat_map.get("STL")),
            "blocks_per_game": safe_float(stat_map.get("BLK")),
            "turnovers_per_game": safe_float(stat_map.get("TO")),
            "personal_fouls_per_game": safe_float(stat_map.get("PF")),
            "field_goal_pct": safe_float(stat_map.get("FG%")),
            "three_point_pct": safe_float(stat_map.get("3P%")),
            "free_throw_pct": safe_float(stat_map.get("FT%")),
            "offensive_rebounds_per_game": safe_float(stat_map.get("OR")),
            "defensive_rebounds_per_game": safe_float(stat_map.get("DR")),
        },
    }

    if fg_made is not None:
        result["stats"]["field_goals_made_per_game"] = fg_made
        result["stats"]["field_goals_attempted_per_game"] = fg_att
    if tp_made is not None:
        result["stats"]["three_pointers_made_per_game"] = tp_made
        result["stats"]["three_pointers_attempted_per_game"] = tp_att
    if ft_made is not None:
        result["stats"]["free_throws_made_per_game"] = ft_made
        result["stats"]["free_throws_attempted_per_game"] = ft_att

    return result


def main():
    parser = argparse.ArgumentParser(description="Fetch NBA player statistics from ESPN")
    parser.add_argument("--team", type=str, help="Fetch only one team by abbreviation (e.g., BOS)")
    parser.add_argument("--detailed", action="store_true",
                        help="Fetch detailed splits (FG made/attempted, OREB/DREB) - slower")
    args = parser.parse_args()

    players = load_data("nba_players.json")

    if args.team:
        team_id = f"nba-{args.team.lower()}"
        players = [p for p in players if p["team_id"] == team_id]
        if not players:
            print(f"No players found for team {args.team}")
            return

    print(f"Fetching stats for {len(players)} players (detailed={args.detailed})...")

    results = []
    fetched = 0
    skipped = 0
    errors = 0

    for i, player in enumerate(players):
        espn_id = player.get("metadata", {}).get("espn_player_id", "")
        if not espn_id:
            skipped += 1
            continue

        # Progress every 30 players
        if i % 30 == 0 and i > 0:
            print(f"  [{i}/{len(players)}] {fetched} fetched, {errors} errors...")

        if args.detailed:
            url = _client._registry.get_endpoint("espn_nba", "player_stats", player_id=espn_id)
            data = fetch_json(url)
            stat_data = parse_detailed_stats(data) if data else None
        else:
            url = _client._registry.get_endpoint("espn_nba", "player_overview", player_id=espn_id)
            data = fetch_json(url)
            stat_data = parse_overview_stats(data) if data else None

        if stat_data and stat_data.get("games_played", 0) > 0:
            stat_data["player_name"] = player["name"]
            stat_data["team_id"] = player["team_id"]
            stat_data["espn_player_id"] = espn_id
            results.append(stat_data)
            fetched += 1
        elif data is None:
            errors += 1
            # Brief pause on error
            time.sleep(2)
        else:
            skipped += 1  # Player has 0 games played

        # Rate limiting handled by ScriptHttpClient

    # Sort by PPG descending
    results.sort(key=lambda p: p.get("stats", {}).get("points_per_game", 0), reverse=True)

    output = {
        "competition_id": "nba-2025-26-regular-season",
        "season": "2025-26",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_players": len(results),
        "players": results,
    }

    save_data("nba_player_stats.json", output)

    print(f"\nDone: {fetched} players with stats")
    print(f"  Skipped (no ESPN ID or 0 GP): {skipped}")
    print(f"  Errors: {errors}")

    # Show top 5 scorers
    print("\nTop 5 scorers:")
    for p in results[:5]:
        s = p["stats"]
        print(f"  {p['player_name']} ({p['team_id']}): "
              f"{s['points_per_game']} PPG, {s['rebounds_per_game']} RPG, "
              f"{s['assists_per_game']} APG in {p['games_played']} games")

    print(f"\nWritten to nba_player_stats.json")


if __name__ == "__main__":
    main()
