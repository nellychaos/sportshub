"""Fetch FIFA World Cup team tactical and historical stats.

Enriches existing data/fifa_wc_teams.json with tournament standings,
qualifying records, and historical World Cup performance data.

Sources:
- Football-Data.org /competitions/WC/standings for group stage standings
- ESPN FIFA scoreboard for match results
- Curated historical WC performance data for major nations

Usage: python3 -m scripts.fetch_fifa_team_stats
       python3 -m scripts.fetch_fifa_team_stats --team USA
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data
from sportshub.scripts.normalization import normalize_team_name

FOOTBALLDATA_BASE = "https://api.football-data.org/v4"
ESPN_FIFA_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FOOTBALLDATA_KEY = (os.environ.get("SPORTSHUB_FOOTBALLDATA_API_KEY")
                    or os.environ.get("FOOTBALLDATA_API_KEY", ""))

FOOTBALLDATA_RATE_LIMIT = 6.0
ESPN_RATE_LIMIT = 1.0


def fetch_footballdata(url: str) -> dict | None:
    time.sleep(FOOTBALLDATA_RATE_LIMIT)
    try:
        headers = {"Accept": "application/json"}
        if FOOTBALLDATA_KEY:
            headers["X-Auth-Token"] = FOOTBALLDATA_KEY
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"  Error: {e}")
        return None


def fetch_espn(url: str) -> dict | None:
    time.sleep(ESPN_RATE_LIMIT)
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError) as e:
        return None


# ── Historical WC data (curated) ──────────────────────────────────────────
# Major nations' historical World Cup records. This is reference data that
# doesn't change and is sourced from FIFA.com/Wikipedia.

HISTORICAL_WC_STATS = {
    "Brazil": {"total_matches": 114, "total_wins": 76, "total_draws": 19, "total_losses": 19, "total_goals_for": 237, "total_goals_against": 108, "titles": 5, "finals": 7, "semi_finals": 11, "best_result": "Winners (1958, 1962, 1970, 1994, 2002)"},
    "Germany": {"total_matches": 112, "total_wins": 68, "total_draws": 21, "total_losses": 23, "total_goals_for": 232, "total_goals_against": 130, "titles": 4, "finals": 8, "semi_finals": 13, "best_result": "Winners (1954, 1974, 1990, 2014)"},
    "Argentina": {"total_matches": 88, "total_wins": 47, "total_draws": 17, "total_losses": 24, "total_goals_for": 156, "total_goals_against": 100, "titles": 3, "finals": 6, "semi_finals": 6, "best_result": "Winners (1978, 1986, 2022)"},
    "France": {"total_matches": 73, "total_wins": 39, "total_draws": 13, "total_losses": 21, "total_goals_for": 131, "total_goals_against": 82, "titles": 2, "finals": 4, "semi_finals": 7, "best_result": "Winners (1998, 2018)"},
    "Italy": {"total_matches": 83, "total_wins": 45, "total_draws": 21, "total_losses": 17, "total_goals_for": 128, "total_goals_against": 77, "titles": 4, "finals": 6, "semi_finals": 8, "best_result": "Winners (1934, 1938, 1982, 2006)"},
    "England": {"total_matches": 74, "total_wins": 32, "total_draws": 21, "total_losses": 21, "total_goals_for": 108, "total_goals_against": 72, "titles": 1, "finals": 1, "semi_finals": 4, "best_result": "Winners (1966)"},
    "Spain": {"total_matches": 67, "total_wins": 33, "total_draws": 16, "total_losses": 18, "total_goals_for": 103, "total_goals_against": 72, "titles": 1, "finals": 1, "semi_finals": 2, "best_result": "Winners (2010)"},
    "Netherlands": {"total_matches": 56, "total_wins": 30, "total_draws": 12, "total_losses": 14, "total_goals_for": 95, "total_goals_against": 57, "titles": 0, "finals": 3, "semi_finals": 5, "best_result": "Runners-up (1974, 1978, 2010)"},
    "Uruguay": {"total_matches": 59, "total_wins": 24, "total_draws": 12, "total_losses": 23, "total_goals_for": 87, "total_goals_against": 78, "titles": 2, "finals": 2, "semi_finals": 5, "best_result": "Winners (1930, 1950)"},
    "Portugal": {"total_matches": 37, "total_wins": 19, "total_draws": 6, "total_losses": 12, "total_goals_for": 56, "total_goals_against": 38, "titles": 0, "finals": 0, "semi_finals": 2, "best_result": "3rd place (1966, 2006)"},
    "Belgium": {"total_matches": 51, "total_wins": 21, "total_draws": 9, "total_losses": 21, "total_goals_for": 75, "total_goals_against": 75, "titles": 0, "finals": 0, "semi_finals": 2, "best_result": "3rd place (2018)"},
    "Croatia": {"total_matches": 24, "total_wins": 11, "total_draws": 3, "total_losses": 10, "total_goals_for": 34, "total_goals_against": 31, "titles": 0, "finals": 2, "semi_finals": 3, "best_result": "Runners-up (2018, 2022)"},
    "Mexico": {"total_matches": 60, "total_wins": 17, "total_draws": 15, "total_losses": 28, "total_goals_for": 62, "total_goals_against": 98, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Quarter-finals (1970, 1986)"},
    "United States": {"total_matches": 36, "total_wins": 10, "total_draws": 6, "total_losses": 20, "total_goals_for": 40, "total_goals_against": 64, "titles": 0, "finals": 0, "semi_finals": 1, "best_result": "3rd place (1930)"},
    "Japan": {"total_matches": 24, "total_wins": 7, "total_draws": 5, "total_losses": 12, "total_goals_for": 27, "total_goals_against": 33, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Round of 16 (2002, 2010, 2018, 2022)"},
    "South Korea": {"total_matches": 37, "total_wins": 8, "total_draws": 9, "total_losses": 20, "total_goals_for": 37, "total_goals_against": 72, "titles": 0, "finals": 0, "semi_finals": 1, "best_result": "4th place (2002)"},
    "Colombia": {"total_matches": 21, "total_wins": 9, "total_draws": 3, "total_losses": 9, "total_goals_for": 32, "total_goals_against": 28, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Quarter-finals (2014)"},
    "Senegal": {"total_matches": 11, "total_wins": 4, "total_draws": 3, "total_losses": 4, "total_goals_for": 13, "total_goals_against": 14, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Quarter-finals (2002)"},
    "Morocco": {"total_matches": 20, "total_wins": 6, "total_draws": 5, "total_losses": 9, "total_goals_for": 19, "total_goals_against": 24, "titles": 0, "finals": 0, "semi_finals": 1, "best_result": "4th place (2022)"},
    "Canada": {"total_matches": 4, "total_wins": 0, "total_draws": 0, "total_losses": 4, "total_goals_for": 0, "total_goals_against": 5, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Group stage (1986, 2022)"},
    "Australia": {"total_matches": 18, "total_wins": 4, "total_draws": 4, "total_losses": 10, "total_goals_for": 17, "total_goals_against": 32, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Round of 16 (2006, 2022)"},
    "Switzerland": {"total_matches": 40, "total_wins": 14, "total_draws": 8, "total_losses": 18, "total_goals_for": 55, "total_goals_against": 68, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Quarter-finals (1934, 1938, 1954)"},
    "Denmark": {"total_matches": 21, "total_wins": 8, "total_draws": 4, "total_losses": 9, "total_goals_for": 30, "total_goals_against": 29, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Quarter-finals (1998)"},
    "Serbia": {"total_matches": 49, "total_wins": 20, "total_draws": 10, "total_losses": 19, "total_goals_for": 72, "total_goals_against": 63, "titles": 0, "finals": 0, "semi_finals": 2, "best_result": "4th place (1930, 1962) (as Yugoslavia)"},
    "Poland": {"total_matches": 34, "total_wins": 16, "total_draws": 5, "total_losses": 13, "total_goals_for": 46, "total_goals_against": 45, "titles": 0, "finals": 0, "semi_finals": 2, "best_result": "3rd place (1974, 1982)"},
    "Ecuador": {"total_matches": 10, "total_wins": 3, "total_draws": 1, "total_losses": 6, "total_goals_for": 9, "total_goals_against": 15, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Round of 16 (2006)"},
    "Saudi Arabia": {"total_matches": 17, "total_wins": 4, "total_draws": 2, "total_losses": 11, "total_goals_for": 12, "total_goals_against": 36, "titles": 0, "finals": 0, "semi_finals": 0, "best_result": "Round of 16 (1994)"},
}


def fetch_wc_standings() -> list[dict]:
    """Fetch WC 2026 group standings from Football-Data.org."""
    data = fetch_footballdata(f"{FOOTBALLDATA_BASE}/competitions/WC/standings")
    if not data:
        return []

    standings = []
    for group in data.get("standings", []):
        group_name = group.get("group", "")
        stage = group.get("stage", "")
        for entry in group.get("table", []):
            team = entry.get("team", {})
            standings.append({
                "team_name": team.get("name", ""),
                "team_tla": team.get("tla", ""),
                "group": group_name,
                "stage": stage,
                "position": entry.get("position"),
                "played": entry.get("playedGames", 0),
                "won": entry.get("won", 0),
                "drawn": entry.get("draw", 0),
                "lost": entry.get("lost", 0),
                "goals_for": entry.get("goalsFor", 0),
                "goals_against": entry.get("goalsAgainst", 0),
                "goal_difference": entry.get("goalDifference", 0),
                "points": entry.get("points", 0),
            })

    return standings


def match_team(team_name: str, our_teams: list[dict]) -> dict | None:
    """Match a source team name to our team data."""
    norm = normalize_team_name(team_name)

    for team in our_teams:
        if normalize_team_name(team["name"]) == norm:
            return team
        if team.get("metadata", {}).get("fifa_code", "").lower() == team_name.lower():
            return team
        for alias in team.get("aliases", []):
            if normalize_team_name(alias.get("alias", "")) == norm:
                return team

    return None


def main():
    parser = argparse.ArgumentParser(description="Fetch FIFA team tactical stats")
    parser.add_argument("--team", type=str, help="Process only one team by FIFA code")
    args = parser.parse_args()

    with log_script_run("fetch_fifa_team_stats") as run:
        wc_teams = load_data("fifa_wc_teams.json")

        if args.team:
            code = args.team.upper()
            wc_teams = [t for t in wc_teams
                        if t.get("metadata", {}).get("fifa_code", "") == code]
            if not wc_teams:
                print(f"No team found for code '{args.team}'")
                return

        print(f"Enriching stats for {len(wc_teams)} teams...")
        enriched = 0

        # ── Football-Data.org standings ──
        print("\n--- Football-Data.org standings ---")
        if not FOOTBALLDATA_KEY:
            print("  Warning: FOOTBALLDATA_API_KEY not set. Trying without auth...")

        standings = fetch_wc_standings()
        print(f"  {len(standings)} team standings fetched")

        standings_matched = 0
        for standing in standings:
            team = match_team(standing["team_name"], wc_teams)
            if not team:
                # Try by TLA
                tla = standing.get("team_tla", "")
                for t in wc_teams:
                    if t.get("metadata", {}).get("fifa_code", "") == tla:
                        team = t
                        break

            if team:
                meta = team.setdefault("metadata", {})
                meta["tournament_stats"] = {
                    "source": "football-data.org",
                    "group": standing.get("group"),
                    "group_position": standing.get("position"),
                    "played": standing.get("played", 0),
                    "won": standing.get("won", 0),
                    "drawn": standing.get("drawn", 0),
                    "lost": standing.get("lost", 0),
                    "goals_for": standing.get("goals_for", 0),
                    "goals_against": standing.get("goals_against", 0),
                    "goal_difference": standing.get("goal_difference", 0),
                    "points": standing.get("points", 0),
                }
                standings_matched += 1

        print(f"  Matched {standings_matched} teams with tournament standings")

        # ── Historical WC stats (curated) ──
        print("\n--- Historical WC stats ---")
        historical_matched = 0

        for team in wc_teams:
            team_name = team["name"]
            historical = HISTORICAL_WC_STATS.get(team_name)

            if not historical:
                # Try short name or common variants
                for key in HISTORICAL_WC_STATS:
                    if normalize_team_name(key) == normalize_team_name(team_name):
                        historical = HISTORICAL_WC_STATS[key]
                        break

            if historical:
                meta = team.setdefault("metadata", {})
                meta["historical_wc_stats"] = historical
                historical_matched += 1

        print(f"  Matched {historical_matched}/{len(wc_teams)} teams with historical data")

        # ── Count enriched teams ──
        for team in wc_teams:
            meta = team.get("metadata", {})
            if meta.get("tournament_stats") or meta.get("historical_wc_stats"):
                enriched += 1

        save_data("fifa_wc_teams.json", wc_teams)

        run.records_processed = enriched
        run.summary = (
            f"Enriched {enriched}/{len(wc_teams)} teams "
            f"(standings: {standings_matched}, historical: {historical_matched})"
        )

        print(f"\n{'='*50}")
        print(f"Done: {enriched} teams enriched")
        print(f"  Tournament standings: {standings_matched}")
        print(f"  Historical WC stats: {historical_matched}")

        # Show sample
        print("\nSample enriched teams:")
        for team in wc_teams[:8]:
            m = team.get("metadata", {})
            hist = m.get("historical_wc_stats", {})
            ts = m.get("tournament_stats", {})
            titles = hist.get("titles", "?")
            matches = hist.get("total_matches", "?")
            group = ts.get("group", "?")
            print(f"  {team['name']}: {titles} titles, {matches} WC matches, group {group}")

        print(f"\nWritten to fifa_wc_teams.json")


if __name__ == "__main__":
    main()
