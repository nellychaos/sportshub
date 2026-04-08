"""Fetch full NBA 2025-26 season schedule from NBA.com CDN.

Creates data/nba_schedule.json with all regular season games,
including scores for completed games.

Usage: python3 -m scripts.fetch_nba_schedule
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.providers.abbreviations import nbacom_to_standard
from sportshub.scripts.http import ScriptHttpClient
from sportshub.scripts.io import load_data, save_data

_client = ScriptHttpClient("nbacom_cdn")

# gameStatus values: 1 = scheduled, 2 = in progress, 3 = final
STATUS_MAP = {
    1: "scheduled",
    2: "in_progress",
    3: "completed",
}


def fetch_cdn() -> dict:
    return _client.get_endpoint("schedule", timeout=120)


def tricode_to_team_id(tricode: str) -> str:
    abbr = nbacom_to_standard(tricode)
    return f"nba-{abbr.lower()}"


def parse_game(game: dict, venue_timezones: dict) -> dict | None:
    """Parse a single game from the CDN schedule."""
    home = game.get("homeTeam", {})
    away = game.get("awayTeam", {})
    home_tri = home.get("teamTricode", "")
    away_tri = away.get("teamTricode", "")

    if not home_tri or not away_tri:
        return None

    game_status = game.get("gameStatus", 1)
    status = STATUS_MAP.get(game_status, "scheduled")
    arena = game.get("arenaName", "")

    result = {
        "game_id": game.get("gameId", ""),
        "home_team_id": tricode_to_team_id(home_tri),
        "away_team_id": tricode_to_team_id(away_tri),
        "home_team_tricode": home_tri,
        "away_team_tricode": away_tri,
        "home_team_name": f"{home.get('teamCity', '')} {home.get('teamName', '')}".strip(),
        "away_team_name": f"{away.get('teamCity', '')} {away.get('teamName', '')}".strip(),
        "date": game.get("gameDateUTC", "")[:10],
        "scheduled_at_utc": game.get("gameDateTimeUTC", ""),
        "venue": arena,
        "status": status,
    }

    # Venue timezone
    tz = venue_timezones.get(arena, "")
    if tz:
        result["venue_timezone"] = tz

    # Scores for completed/in-progress games
    if game_status >= 2:
        result["home_score"] = home.get("score", 0)
        result["away_score"] = away.get("score", 0)
    else:
        result["home_score"] = None
        result["away_score"] = None

    return result


def is_regular_season_game(game_id: str) -> bool:
    """NBA game IDs starting with '002' are regular season."""
    return game_id.startswith("002")


def is_preseason_game(game_id: str) -> bool:
    """NBA game IDs starting with '001' are preseason."""
    return game_id.startswith("001")


def main():
    # Load venue timezones
    venue_timezones = {}
    try:
        vt = load_data("venue_timezones.json")
        venue_timezones = vt.get("venues", vt)
    except FileNotFoundError:
        pass

    print("Fetching NBA.com CDN schedule (this may take a moment)...")
    data = fetch_cdn()

    league_schedule = data.get("leagueSchedule", {})
    game_dates = league_schedule.get("gameDates", [])
    season_year = league_schedule.get("seasonYear", "")

    print(f"  Season: {season_year}")
    print(f"  Game dates: {len(game_dates)}")

    # Parse all games, separate regular season from preseason/playoffs
    regular_season = []
    preseason = []
    other = []

    for gd in game_dates:
        for game in gd.get("games", []):
            parsed = parse_game(game, venue_timezones)
            if not parsed:
                continue

            gid = parsed["game_id"]
            if is_regular_season_game(gid):
                regular_season.append(parsed)
            elif is_preseason_game(gid):
                preseason.append(parsed)
            else:
                other.append(parsed)

    # Sort by date
    regular_season.sort(key=lambda g: g["scheduled_at_utc"])

    # Count stats
    completed = sum(1 for g in regular_season if g["status"] == "completed")
    scheduled = sum(1 for g in regular_season if g["status"] == "scheduled")
    in_progress = sum(1 for g in regular_season if g["status"] == "in_progress")

    output = {
        "competition_id": "nba-2025-26-regular-season",
        "season": "2025-26",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "games_total": len(regular_season),
        "games_completed": completed,
        "games_scheduled": scheduled,
        "games_in_progress": in_progress,
        "games": regular_season,
    }

    save_data("nba_schedule.json", output)

    print(f"\nRegular season: {len(regular_season)} games")
    print(f"  Completed: {completed}")
    print(f"  Scheduled: {scheduled}")
    print(f"  In progress: {in_progress}")
    print(f"Preseason: {len(preseason)} games (excluded)")
    print(f"Other (playoffs/play-in/etc): {len(other)} games (excluded)")
    print(f"\nWritten to nba_schedule.json")


if __name__ == "__main__":
    main()
