"""Enrich LoL team metadata with W/L records and roster stats.

Computes team records from completed events in the LoL Esports schedule
API and adds roster size from lol_players.json. Merges into existing
data/lol_teams.json metadata.

Falls back to schedule-based record computation when the getStandings
endpoint is unavailable (returns 502 when tournament IDs are not known).

Usage: python3 -m scripts.enrich_lol_teams
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data
from sportshub.scripts.normalization import normalize_team_name

LOLESPORTS_BASE = "https://esports-api.lolesports.com/persisted/gw"

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

LOLESPORTS_KEY = (os.environ.get("SPORTSHUB_LOLESPORTS_API_KEY")
                  or os.environ.get("LOLESPORTS_API_KEY")
                  or "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z")

MAJOR_LEAGUE_IDS = {
    "98767991310872058": "LCK",
    "98767991314006698": "LPL",
    "98767991302996019": "LEC",
    "98767991299243165": "LCS",
}


def fetch_lolesports(endpoint: str, params: dict | None = None) -> dict | None:
    """Fetch from LoL Esports API."""
    time.sleep(5.0)
    url = f"{LOLESPORTS_BASE}/{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    try:
        req = Request(url, headers={
            "x-api-key": LOLESPORTS_KEY,
            "Accept": "application/json",
        })
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"  Error fetching {endpoint}: {e}")
        return None


def fetch_league_schedule(league_id: str) -> list[dict]:
    """Fetch all events (including completed) for a league.

    Follows pagination to get the full schedule.
    """
    all_events = []
    page_token = None
    pages = 0

    while pages < 10:  # Safety limit
        params = {"hl": "en-US", "leagueId": league_id}
        if page_token:
            params["pageToken"] = page_token

        data = fetch_lolesports("getSchedule", params)
        if not data:
            break

        schedule = data.get("data", {}).get("schedule", {})
        events = schedule.get("events", [])
        all_events.extend(events)

        older_token = schedule.get("pages", {}).get("older")
        if older_token and older_token != page_token:
            page_token = older_token
            pages += 1
        else:
            break

    return all_events


def compute_records(events: list[dict], league_name: str) -> dict[str, dict]:
    """Compute W/L records from completed events.

    Returns: {team_code: {name, wins, losses, league}}
    """
    records: dict[str, dict] = {}

    for event in events:
        state = event.get("state", "")
        if state != "completed":
            continue

        match = event.get("match", {})
        if not match:
            continue

        teams = match.get("teams", [])
        if len(teams) < 2:
            continue

        # Determine winner from match result
        winner = match.get("winner", {})
        if not winner:
            # Check teams for outcome
            for team in teams:
                result = team.get("result", {})
                outcome = result.get("outcome", "")
                code = team.get("code", "")
                name = team.get("name", "")

                if code not in records:
                    records[code] = {"name": name, "wins": 0, "losses": 0, "league": league_name}

                if outcome == "win":
                    records[code]["wins"] += 1
                elif outcome == "loss":
                    records[code]["losses"] += 1
        else:
            # Winner is specified
            for team in teams:
                code = team.get("code", "")
                name = team.get("name", "")

                if code not in records:
                    records[code] = {"name": name, "wins": 0, "losses": 0, "league": league_name}

    return records


def build_team_name_lookup(teams: list[dict]) -> dict[str, int]:
    """Build lookup from normalized name/alias/abbreviation -> team index."""
    lookup = {}
    for idx, team in enumerate(teams):
        lookup[normalize_team_name(team["name"])] = idx
        for alias_entry in team.get("aliases", []):
            alias = alias_entry.get("alias", "")
            if alias:
                lookup[normalize_team_name(alias)] = idx
                lookup[alias.lower().strip()] = idx
        if team.get("abbreviation"):
            lookup[team["abbreviation"].lower()] = idx
    return lookup


def main():
    with log_script_run("enrich_lol_teams") as run:
        teams = load_data("lol_teams.json")
        team_lookup = build_team_name_lookup(teams)

        # Load player data for roster size
        try:
            players = load_data("lol_players.json")
        except (FileNotFoundError, json.JSONDecodeError):
            players = []

        # Count players per team
        roster_counts: dict[str, int] = defaultdict(int)
        for p in players:
            tid = p.get("team_id", "")
            if tid:
                roster_counts[tid] += 1

        print(f"Enriching {len(teams)} LoL teams...")
        enriched = 0

        # Fetch schedules and compute records per league
        all_records: dict[str, dict] = {}
        for league_id, league_name in MAJOR_LEAGUE_IDS.items():
            print(f"\n  Fetching {league_name} schedule...")
            events = fetch_league_schedule(league_id)

            completed = [e for e in events if e.get("state") == "completed"]
            print(f"    {len(events)} total events, {len(completed)} completed")

            records = compute_records(events, league_name)
            print(f"    {len(records)} teams with records")

            for code, record in records.items():
                if record["wins"] + record["losses"] > 0:
                    all_records[code] = record

        # Sort records per league for ranking
        league_groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        for code, record in all_records.items():
            league_groups[record["league"]].append((code, record))

        for league, entries in league_groups.items():
            entries.sort(key=lambda x: (x[1]["wins"], -x[1]["losses"]), reverse=True)
            for rank, (code, record) in enumerate(entries, 1):
                record["region_standing"] = rank

        # Match records to our teams
        for code, record in all_records.items():
            team_idx = None

            # Try code, then name
            for candidate in [code, record.get("name", "")]:
                if not candidate:
                    continue
                key = candidate.lower().strip()
                if key in team_lookup:
                    team_idx = team_lookup[key]
                    break
                norm = normalize_team_name(candidate)
                if norm in team_lookup:
                    team_idx = team_lookup[norm]
                    break

            if team_idx is not None:
                team = teams[team_idx]
                meta = team.setdefault("metadata", {})
                wins = record["wins"]
                losses = record["losses"]
                total = wins + losses

                meta["wins"] = wins
                meta["losses"] = losses
                meta["win_rate"] = round(wins / total * 100, 1) if total > 0 else None
                meta["region_standing"] = record.get("region_standing")
                meta["league"] = record["league"]
                enriched += 1

        # Add roster size from player data
        for team in teams:
            team_id_slug = team["name"].lower().replace(" ", "-").replace(".", "")
            team_id = f"lol-{team_id_slug}"
            roster_size = roster_counts.get(team_id, 0)
            if roster_size > 0:
                team.setdefault("metadata", {})["roster_size"] = roster_size

        save_data("lol_teams.json", teams)

        run.records_processed = enriched
        run.summary = f"Enriched {enriched}/{len(teams)} teams with W/L records"

        print(f"\nDone: {enriched} teams enriched with W/L records")

        # Show enriched teams
        enriched_teams = [t for t in teams if t.get("metadata", {}).get("wins") is not None]
        enriched_teams.sort(key=lambda t: t.get("metadata", {}).get("win_rate", 0) or 0, reverse=True)
        if enriched_teams:
            print("\nTeam records:")
            for t in enriched_teams[:16]:
                m = t["metadata"]
                print(f"  {t['name']} ({m.get('league', '?')}): "
                      f"{m.get('wins', 0)}W-{m.get('losses', 0)}L "
                      f"({m.get('win_rate', 0)}%) #{m.get('region_standing', '?')}")

        print(f"\nWritten to lol_teams.json")


if __name__ == "__main__":
    main()
