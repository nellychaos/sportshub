"""Fetch NBA team standings and statistics from ESPN public API.

Creates data/nba_team_stats.json with conference standings, W-L records,
PPG, opponent PPG, point differential, and split records.

Usage: python3 -m scripts.fetch_nba_team_stats
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.providers.abbreviations import espn_to_standard
from sportshub.scripts.http import ScriptHttpClient
from sportshub.scripts.io import load_data, save_data

_client = ScriptHttpClient("espn_nba")


def get_stat(stats: list[dict], stat_name: str) -> str | int | float | None:
    """Extract a stat value from ESPN's stats array by name."""
    for s in stats:
        if s.get("name") == stat_name or s.get("type") == stat_name:
            val = s.get("displayValue", s.get("value"))
            return val
    return None


def parse_entry(entry: dict, conference: str, rank: int) -> dict:
    """Parse a single standings entry into our schema."""
    team_data = entry.get("team", {})
    stats = entry.get("stats", [])

    espn_abbr = team_data.get("abbreviation", "")
    our_abbr = espn_to_standard(espn_abbr)
    team_id = f"nba-{our_abbr.lower()}"

    wins = int(get_stat(stats, "wins") or 0)
    losses = int(get_stat(stats, "losses") or 0)
    win_pct_raw = get_stat(stats, "winPercent") or "0"
    win_pct = float(win_pct_raw.replace(".", "0.", 1)) if win_pct_raw.startswith(".") else float(win_pct_raw)

    return {
        "team_id": team_id,
        "team_name": team_data.get("displayName", ""),
        "conference": conference,
        "conference_rank": rank,
        "wins": wins,
        "losses": losses,
        "win_pct": round(win_pct, 3),
        "games_back": get_stat(stats, "gamesBehind") or "0",
        "home_record": get_stat(stats, "home") or "",
        "away_record": get_stat(stats, "road") or "",
        "conference_record": get_stat(stats, "vsconf") or "",
        "division_record": get_stat(stats, "vsdiv") or "",
        "last_10": get_stat(stats, "lasttengames") or "",
        "streak": get_stat(stats, "streak") or "",
        "points_per_game": float(get_stat(stats, "avgPointsFor") or 0),
        "opponent_points_per_game": float(get_stat(stats, "avgPointsAgainst") or 0),
        "point_differential": get_stat(stats, "differential") or "0",
        "clincher": get_stat(stats, "clincher") or "",
        "playoff_seed": int(get_stat(stats, "playoffSeed") or 0),
    }


def main():
    # Load teams for validation
    teams = load_data("nba_teams.json")
    team_ids = {t["id"] for t in teams}

    print("Fetching ESPN standings...")
    data = _client.get_endpoint("standings")

    conferences = data.get("children", [])
    print(f"  Found {len(conferences)} conferences")

    standings = {"eastern": [], "western": []}

    for conf in conferences:
        conf_name = conf.get("abbreviation", "").lower()
        if "east" in conf_name:
            key = "eastern"
        elif "west" in conf_name:
            key = "western"
        else:
            key = conf_name

        entries = conf.get("standings", {}).get("entries", [])
        print(f"  {conf.get('name', key)}: {len(entries)} teams")

        for rank, entry in enumerate(entries, 1):
            parsed = parse_entry(entry, key, rank)
            if parsed["team_id"] not in team_ids:
                print(f"    WARNING: {parsed['team_id']} not in teams file")
            standings[key].append(parsed)

    output = {
        "competition_id": "nba-2025-26-regular-season",
        "season": "2025-26",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "standings": standings,
    }

    save_data("nba_team_stats.json", output)

    # Summary
    total = len(standings["eastern"]) + len(standings["western"])
    print(f"\nDone: {total} teams with standings")

    # Show top 3 each conference
    for conf_key in ("eastern", "western"):
        print(f"\n  {conf_key.title()} Conference top 3:")
        for team in standings[conf_key][:3]:
            print(f"    {team['conference_rank']}. {team['team_name']} ({team['wins']}-{team['losses']}) "
                  f"PPG: {team['points_per_game']} | Diff: {team['point_differential']}")

    print(f"\nWritten to nba_team_stats.json")


if __name__ == "__main__":
    main()
