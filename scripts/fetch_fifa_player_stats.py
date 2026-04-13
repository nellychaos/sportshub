"""Fetch FIFA World Cup player performance stats.

Enriches existing data/fifa_wc_players.json with performance data from
ESPN FIFA and Football-Data.org. Follows the ESPN pattern used by the
NBA pipeline -- roster endpoint to get player IDs, then player overview
for stats.

Graceful degradation: tries ESPN first, falls back to Football-Data.org
for squad data. Reports what worked.

Requires: fifa_wc_players.json and fifa_wc_teams.json to exist.

Usage: python3 -m scripts.fetch_fifa_player_stats
       python3 -m scripts.fetch_fifa_player_stats --team USA
       python3 -m scripts.fetch_fifa_player_stats --source footballdata
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data
from sportshub.scripts.normalization import normalize_player_name

ESPN_FIFA_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
ESPN_OVERVIEW_BASE = "https://site.web.api.espn.com/apis/common/v3/sports/soccer/fifa.world"
FOOTBALLDATA_BASE = "https://api.football-data.org/v4"

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FOOTBALLDATA_KEY = (os.environ.get("SPORTSHUB_FOOTBALLDATA_API_KEY")
                    or os.environ.get("FOOTBALLDATA_API_KEY", ""))

ESPN_RATE_LIMIT = 1.0
FOOTBALLDATA_RATE_LIMIT = 6.0


def fetch_espn(url: str) -> dict | list | None:
    """Fetch from ESPN API."""
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


def fetch_footballdata(url: str) -> dict | list | None:
    """Fetch from Football-Data.org API."""
    time.sleep(FOOTBALLDATA_RATE_LIMIT)
    try:
        headers = {
            "Accept": "application/json",
        }
        if FOOTBALLDATA_KEY:
            headers["X-Auth-Token"] = FOOTBALLDATA_KEY
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError) as e:
        return None


def safe_float(val, default: float | None = None) -> float | None:
    if val is None:
        return default
    try:
        return round(float(val), 1)
    except (ValueError, TypeError):
        return default


def safe_int(val, default: int | None = None) -> int | None:
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ── ESPN pipeline ──────────────────────────────────────────────────────────

def fetch_espn_teams() -> list[dict]:
    """Fetch all WC teams from ESPN to get their ESPN IDs."""
    data = fetch_espn(f"{ESPN_FIFA_BASE}/teams")
    if not data:
        return []
    sports = data.get("sports", [])
    for sport in sports:
        for league in sport.get("leagues", []):
            return league.get("teams", [])
    return []


def fetch_espn_roster(team_id: str) -> list[dict]:
    """Fetch a team's roster from ESPN.

    ESPN FIFA returns athletes as a flat list (unlike NBA which nests
    in position groups with 'items').
    """
    data = fetch_espn(f"{ESPN_FIFA_BASE}/teams/{team_id}/roster")
    if not data:
        return []

    athletes = data.get("athletes", [])
    if not athletes:
        return []

    # Check if it's a flat list of players or grouped by position
    if isinstance(athletes[0], dict):
        if "items" in athletes[0]:
            # Grouped format (like NBA)
            flat = []
            for group in athletes:
                flat.extend(group.get("items", []))
            return flat
        else:
            # Flat format (FIFA/soccer)
            return athletes

    return []


def fetch_espn_player_overview(player_id: str) -> dict | None:
    """Fetch player overview stats from ESPN."""
    url = f"{ESPN_OVERVIEW_BASE}/athletes/{player_id}/overview"
    return fetch_espn(url)


def parse_espn_player_stats(overview: dict) -> dict:
    """Parse ESPN player overview into our stats fields.

    ESPN soccer stats use names like 'starts', 'foulsCommitted',
    'yellowCards', 'redCards', 'totalGoals', 'goalAssists', etc.
    Stats are split by competition/season.
    """
    stats = {}

    statistics = overview.get("statistics", {})
    splits = statistics.get("splits", [])
    names = statistics.get("names", [])

    # Aggregate across all club season splits (skip international friendlies)
    total_starts = 0
    total_goals = 0
    total_assists = 0
    total_yellow = 0
    total_red = 0
    total_fouls = 0
    found_club = False

    for split in splits:
        display = split.get("displayName", "").lower()

        # Skip international splits -- we want club stats
        if "international" in display or "friendly" in display:
            continue

        # Look for current season club data
        if "2025" not in display and "2026" not in display:
            continue

        values = split.get("stats", [])
        if not values:
            continue

        stat_map = {}
        for name_key, val in zip(names, values):
            try:
                stat_map[name_key] = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                stat_map[name_key] = val

        total_starts += safe_int(stat_map.get("starts"), 0)
        total_goals += safe_int(stat_map.get("totalGoals", stat_map.get("goals")), 0)
        total_assists += safe_int(stat_map.get("goalAssists", stat_map.get("assists")), 0)
        total_yellow += safe_int(stat_map.get("yellowCards"), 0)
        total_red += safe_int(stat_map.get("redCards"), 0)
        total_fouls += safe_int(stat_map.get("foulsCommitted"), 0)
        found_club = True

    if found_club:
        stats["club_appearances_2025_26"] = total_starts if total_starts > 0 else None
        stats["club_goals_2025_26"] = total_goals
        stats["club_assists_2025_26"] = total_assists
        stats["yellow_cards"] = total_yellow
        stats["red_cards"] = total_red

    # Also check international splits for career totals
    for split in splits:
        display = split.get("displayName", "").lower()
        if "international" not in display and "friendly" not in display:
            continue

        values = split.get("stats", [])
        if not values:
            continue

        stat_map = {}
        for name_key, val in zip(names, values):
            try:
                stat_map[name_key] = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                stat_map[name_key] = val

        intl_goals = safe_int(stat_map.get("totalGoals", stat_map.get("goals")))
        if intl_goals is not None and intl_goals > 0:
            stats.setdefault("international_goals_recent", intl_goals)

    return stats


# ── Football-Data.org pipeline ─────────────────────────────────────────────

def fetch_footballdata_teams() -> list[dict]:
    """Fetch all WC teams with squads from Football-Data.org."""
    data = fetch_footballdata(f"{FOOTBALLDATA_BASE}/competitions/WC/teams")
    if not data:
        return []
    return data.get("teams", [])


def parse_footballdata_squad(fd_teams: list[dict]) -> dict[str, list[dict]]:
    """Parse Football-Data.org teams into per-team player lists.

    Returns: {normalized_team_name: [{name, position, dateOfBirth, nationality}]}
    """
    result = {}
    for team in fd_teams:
        team_name = team.get("name", "")
        squad = team.get("squad", []) or []
        players = []
        for p in squad:
            players.append({
                "name": p.get("name", ""),
                "position": p.get("position"),
                "dateOfBirth": p.get("dateOfBirth"),
                "nationality": p.get("nationality"),
                "footballdata_id": p.get("id"),
            })
        if players:
            result[normalize_player_name(team_name)] = players
    return result


# ── Matching and merging ──────────────────────────────────────────────────

def find_player_match(player_name: str, players: list[dict], threshold: float = 0.85) -> dict | None:
    """Find the best matching player in our data by name."""
    norm_name = normalize_player_name(player_name)

    # Direct match
    for p in players:
        if normalize_player_name(p["name"]) == norm_name:
            return p

    # Fuzzy match
    best_score = 0
    best_player = None
    for p in players:
        score = SequenceMatcher(None, norm_name, normalize_player_name(p["name"])).ratio()
        if score > best_score and score >= threshold:
            best_score = score
            best_player = p

    return best_player


def main():
    parser = argparse.ArgumentParser(description="Fetch FIFA player stats")
    parser.add_argument("--team", type=str, help="Process only one team by FIFA code (e.g., USA)")
    parser.add_argument("--source", choices=["espn", "footballdata", "both"], default="both",
                        help="Which data source to use")
    args = parser.parse_args()

    with log_script_run("fetch_fifa_player_stats") as run:
        wc_players = load_data("fifa_wc_players.json")
        wc_teams = load_data("fifa_wc_teams.json")

        if args.team:
            code = args.team.upper()
            wc_teams = [t for t in wc_teams
                        if t.get("metadata", {}).get("fifa_code", "") == code
                        or t.get("short_name", "").upper() == code]
            if not wc_teams:
                print(f"No team found matching '{args.team}'")
                return
            # Filter players to matching teams
            team_ids = {t["id"] for t in wc_teams}
            wc_players = [p for p in wc_players if p.get("team_id") in team_ids]

        print(f"Enriching stats for {len(wc_players)} players across {len(wc_teams)} teams...")

        enriched_espn = 0
        enriched_fd = 0
        errors = 0

        # ── ESPN pipeline ──
        if args.source in ("espn", "both"):
            print("\n--- ESPN pipeline ---")
            espn_teams = fetch_espn_teams()
            print(f"  ESPN teams: {len(espn_teams)}")

            for espn_entry in espn_teams:
                espn_team = espn_entry.get("team", espn_entry)
                espn_team_id = espn_team.get("id", "")
                espn_team_name = espn_team.get("displayName", espn_team.get("name", ""))

                if args.team:
                    abbr = espn_team.get("abbreviation", "")
                    if abbr.upper() != args.team.upper() and espn_team_name.lower() != args.team.lower():
                        continue

                print(f"\n  {espn_team_name}:")

                # Fetch roster
                roster = fetch_espn_roster(espn_team_id)
                if not roster:
                    print(f"    No roster data")
                    continue

                print(f"    {len(roster)} players in ESPN roster")

                for athlete in roster:
                    espn_player_id = str(athlete.get("id", ""))
                    espn_player_name = athlete.get("displayName", athlete.get("fullName", ""))
                    if not espn_player_id or not espn_player_name:
                        continue

                    # Match to our player data
                    our_player = find_player_match(espn_player_name, wc_players)
                    if not our_player:
                        continue

                    # Fetch overview stats
                    overview = fetch_espn_player_overview(espn_player_id)
                    if not overview:
                        errors += 1
                        continue

                    stats = parse_espn_player_stats(overview)

                    # Merge non-None stats into player metadata
                    meta = our_player.setdefault("metadata", {})
                    for key, val in stats.items():
                        if val is not None:
                            meta[key] = val

                    # Store ESPN ID as alias
                    aliases = our_player.setdefault("aliases", [])
                    if not any(a.get("source_id") == "espn_fifa" and a.get("alias") == espn_player_id
                               for a in aliases):
                        aliases.append({"alias": espn_player_id, "source_id": "espn_fifa"})

                    enriched_espn += 1

                    if enriched_espn % 30 == 0:
                        print(f"    [{enriched_espn} enriched so far...]")

        # ── Football-Data.org pipeline ──
        if args.source in ("footballdata", "both"):
            print("\n--- Football-Data.org pipeline ---")
            if not FOOTBALLDATA_KEY:
                print("  Warning: FOOTBALLDATA_API_KEY not set. Trying without auth...")

            fd_teams = fetch_footballdata_teams()
            print(f"  Football-Data teams: {len(fd_teams)}")

            for fd_team in fd_teams:
                fd_team_name = fd_team.get("name", "")
                squad = fd_team.get("squad", []) or []

                if not squad:
                    continue

                for fd_player in squad:
                    fd_name = fd_player.get("name", "")
                    if not fd_name:
                        continue

                    our_player = find_player_match(fd_name, wc_players)
                    if not our_player:
                        continue

                    meta = our_player.setdefault("metadata", {})

                    # Football-Data provides position and DOB confirmation
                    fd_position = fd_player.get("position")
                    if fd_position and not meta.get("position_detail"):
                        # Map Football-Data positions to more specific roles
                        meta["position_detail"] = fd_position

                    fd_id = fd_player.get("id")
                    if fd_id:
                        aliases = our_player.setdefault("aliases", [])
                        fd_id_str = str(fd_id)
                        if not any(a.get("source_id") == "footballdata" and a.get("alias") == fd_id_str
                                   for a in aliases):
                            aliases.append({"alias": fd_id_str, "source_id": "footballdata"})

                    enriched_fd += 1

        # Save enriched data (always save the full dataset, not the filtered subset)
        if args.team:
            # Reload full data and merge enriched players back in
            full_players = load_data("fifa_wc_players.json")
            enriched_names = {p["name"] for p in wc_players if p.get("metadata", {}).get("club_goals_2025_26") is not None
                              or p.get("metadata", {}).get("club_appearances_2025_26") is not None}
            for fp in full_players:
                for wp in wc_players:
                    if fp["name"] == wp["name"] and fp.get("team_id") == wp.get("team_id"):
                        fp["metadata"] = wp.get("metadata", fp.get("metadata", {}))
                        fp["aliases"] = wp.get("aliases", fp.get("aliases", []))
                        break
            save_data("fifa_wc_players.json", full_players)
        else:
            save_data("fifa_wc_players.json", wc_players)

        total_enriched = enriched_espn + enriched_fd
        run.records_processed = total_enriched
        run.summary = (
            f"Enriched {total_enriched} player records "
            f"(ESPN: {enriched_espn}, Football-Data: {enriched_fd}, errors: {errors})"
        )

        print(f"\n{'='*50}")
        print(f"Done: {total_enriched} player records enriched")
        print(f"  ESPN: {enriched_espn} players with stats")
        print(f"  Football-Data.org: {enriched_fd} players with metadata")
        print(f"  Errors: {errors}")

        # Show sample enriched players
        with_goals = [(p["name"], p["metadata"].get("goals"), p["metadata"].get("club_goals_2025_26"))
                      for p in wc_players if p.get("metadata", {}).get("goals") is not None]
        if with_goals:
            with_goals.sort(key=lambda x: x[1] or 0, reverse=True)
            print("\nSample enriched players (by int'l goals):")
            for name, goals, club_goals in with_goals[:10]:
                club = f", {club_goals} club goals" if club_goals else ""
                print(f"  {name}: {goals} int'l goals{club}")

        print(f"\nWritten to fifa_wc_players.json")


if __name__ == "__main__":
    main()
