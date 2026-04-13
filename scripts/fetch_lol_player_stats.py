"""Fetch LoL player performance stats from PandaScore API.

Creates data/lol_player_stats.json with per-player stats (KDA, win rate,
champion pool) for all players in lol_players.json. Uses PandaScore free
tier which provides player profiles with basic aggregated stats.

Requires: lol_players.json to be populated first (run fetch_lol_players.py).

Usage: python3 -m scripts.fetch_lol_player_stats
       python3 -m scripts.fetch_lol_player_stats --team T1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data

PANDASCORE_BASE = "https://api.pandascore.co"
RATE_LIMIT_SECONDS = 1.0

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PANDASCORE_TOKEN = (os.environ.get("SPORTSHUB_PANDASCORE_TOKEN")
                    or os.environ.get("PANDASCORE_TOKEN", ""))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {PANDASCORE_TOKEN}",
        "Accept": "application/json",
    }


def fetch_json(url: str) -> dict | list | None:
    """Fetch JSON from PandaScore with rate limiting."""
    time.sleep(RATE_LIMIT_SECONDS)
    try:
        req = Request(url, headers=_headers())
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"  Error fetching {url}: {e}")
        return None


def safe_float(val, default: float | None = None) -> float | None:
    """Safely convert to float, returning default on failure."""
    if val is None:
        return default
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return default


def safe_int(val, default: int | None = None) -> int | None:
    """Safely convert to int, returning default on failure."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def fetch_player_stats(player_id: int) -> dict | None:
    """Fetch detailed player profile from PandaScore."""
    url = f"{PANDASCORE_BASE}/lol/players/{player_id}"
    return fetch_json(url)


def fetch_player_matches(player_id: int, per_page: int = 50) -> list[dict]:
    """Fetch recent matches for a player to compute stats."""
    url = (f"{PANDASCORE_BASE}/lol/matches?filter[player_id]={player_id}"
           f"&filter[status]=finished&per_page={per_page}&sort=-scheduled_at")
    data = fetch_json(url)
    return data if isinstance(data, list) else []


def compute_stats_from_matches(matches: list[dict], player_team_name: str) -> dict:
    """Compute win/loss stats from match results."""
    wins = 0
    losses = 0

    for match in matches:
        winner = match.get("winner", {})
        if not winner:
            continue

        winner_name = winner.get("name", "")
        opponents = match.get("opponents", [])

        # Determine if player's team won
        team_in_match = False
        for opp in opponents:
            opp_name = opp.get("opponent", {}).get("name", "")
            if opp_name.lower() == player_team_name.lower():
                team_in_match = True
                break

        if team_in_match:
            if winner_name.lower() == player_team_name.lower():
                wins += 1
            else:
                losses += 1

    total = wins + losses
    win_rate = round(wins / total * 100, 1) if total > 0 else None

    return {
        "games_played": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
    }


def parse_player_stats(profile: dict, matches: list[dict], team_name: str) -> dict:
    """Parse PandaScore player profile + matches into our stats schema."""
    # Basic stats from profile
    stats_data = profile.get("stats", {}) or {}

    # Compute match-based stats
    match_stats = compute_stats_from_matches(matches, team_name)

    # Extract per-game averages from profile stats if available
    # PandaScore provides kills/deaths/assists totals in some tiers
    total_kills = safe_int(stats_data.get("kills"))
    total_deaths = safe_int(stats_data.get("deaths"))
    total_assists = safe_int(stats_data.get("assists"))
    games = match_stats["games_played"] or 1

    kills_pg = safe_float(total_kills / games) if total_kills else None
    deaths_pg = safe_float(total_deaths / games) if total_deaths else None
    assists_pg = safe_float(total_assists / games) if total_assists else None

    # KDA calculation
    kda = None
    if kills_pg is not None and deaths_pg is not None and assists_pg is not None:
        if deaths_pg > 0:
            kda = round((kills_pg + assists_pg) / deaths_pg, 2)
        elif kills_pg + assists_pg > 0:
            kda = round(kills_pg + assists_pg, 2)  # Perfect KDA

    # Champion pool from profile's most_played or videogame data
    champion_pool = []
    most_played = profile.get("most_played_champions") or profile.get("favorite_champions") or []
    for champ in most_played[:10]:
        if isinstance(champ, dict):
            champion_pool.append({
                "champion": champ.get("name", champ.get("champion", "")),
                "games": safe_int(champ.get("games_count"), 0),
                "win_rate": safe_float(champ.get("win_rate")),
                "kda": safe_float(champ.get("kda")),
            })

    return {
        "player_name": profile.get("name", ""),
        "team_id": "",  # filled by caller
        "pandascore_player_id": profile.get("id"),
        "games_played": match_stats["games_played"],
        "wins": match_stats["wins"],
        "losses": match_stats["losses"],
        "stats": {
            "win_rate": match_stats["win_rate"],
            "kda": kda,
            "kills_per_game": kills_pg,
            "deaths_per_game": deaths_pg,
            "assists_per_game": assists_pg,
            "cs_per_min": None,  # Requires detailed game data (paid tier)
            "gold_per_min": None,  # Requires detailed game data (paid tier)
        },
        "champion_pool": champion_pool,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch LoL player stats from PandaScore")
    parser.add_argument("--team", type=str, help="Fetch only one team by name/abbreviation")
    parser.add_argument("--skip-matches", action="store_true",
                        help="Skip match fetching (faster, less accurate stats)")
    args = parser.parse_args()

    if not PANDASCORE_TOKEN:
        print("Error: PANDASCORE_TOKEN environment variable not set.")
        return

    with log_script_run("fetch_lol_player_stats") as run:
        players = load_data("lol_players.json")
        if not players:
            print("Error: lol_players.json is empty. Run fetch_lol_players.py first.")
            return

        if args.team:
            key = args.team.lower().strip()
            players = [p for p in players
                       if key in (p.get("metadata", {}).get("team", "").lower(),
                                  p.get("team_id", "").replace("lol-", ""))]
            if not players:
                print(f"No players found for team '{args.team}'")
                return

        print(f"Fetching stats for {len(players)} players...")

        results = []
        fetched = 0
        skipped = 0
        errors = 0

        for i, player in enumerate(players):
            ps_id = player.get("metadata", {}).get("pandascore_player_id")
            if not ps_id:
                skipped += 1
                continue

            if i > 0 and i % 20 == 0:
                print(f"  [{i}/{len(players)}] {fetched} fetched, {errors} errors...")

            # Fetch player profile
            profile = fetch_player_stats(ps_id)
            if not profile:
                errors += 1
                time.sleep(2)
                continue

            # Fetch recent matches for win/loss computation
            matches = []
            if not args.skip_matches:
                team_name = player.get("metadata", {}).get("team", "")
                matches = fetch_player_matches(ps_id, per_page=50)

            team_name = player.get("metadata", {}).get("team", "")
            stat_entry = parse_player_stats(profile, matches, team_name)
            stat_entry["team_id"] = player.get("team_id", "")

            results.append(stat_entry)
            fetched += 1

        # Sort by KDA descending (players with stats first)
        results.sort(
            key=lambda p: p.get("stats", {}).get("kda") or 0,
            reverse=True,
        )

        output = {
            "competition_id": "lol-2026-spring",
            "season": "2026 Spring",
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_players": len(results),
            "players": results,
        }

        save_data("lol_player_stats.json", output)

        run.records_processed = fetched
        run.summary = f"Fetched stats for {fetched}/{len(players)} players ({errors} errors)"

        print(f"\nDone: {fetched} players with stats")
        print(f"  Skipped (no PandaScore ID): {skipped}")
        print(f"  Errors: {errors}")

        # Show top 5 by KDA
        with_kda = [(p["player_name"], p["stats"]["kda"], p["team_id"])
                     for p in results if p["stats"].get("kda")]
        if with_kda:
            print("\nTop 5 by KDA:")
            for name, kda, tid in with_kda[:5]:
                print(f"  {name} ({tid}): {kda} KDA")

        print(f"\nWritten to lol_player_stats.json")


if __name__ == "__main__":
    main()
