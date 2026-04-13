"""Fetch LoL esports player rosters from PandaScore API.

Populates data/lol_players.json with player profiles for all teams in
lol_teams.json. Uses PandaScore free tier which provides player name,
role, nationality, team association, and image URL.

Usage: python3 -m scripts.fetch_lol_players
       python3 -m scripts.fetch_lol_players --team T1
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
from sportshub.scripts.normalization import normalize_team_name

PANDASCORE_BASE = "https://api.pandascore.co"
RATE_LIMIT_SECONDS = 1.0

# Load .env if dotenv available, then check env vars (SPORTSHUB_ prefix convention)
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


def build_team_lookup(teams: list[dict]) -> dict[str, dict]:
    """Build lookup from normalized team name -> team record."""
    lookup = {}
    for team in teams:
        # Index by all aliases
        for alias_entry in team.get("aliases", []):
            alias = alias_entry.get("alias", "")
            if alias:
                lookup[normalize_team_name(alias)] = team
                lookup[alias.lower().strip()] = team
        # Also index by name and abbreviation
        lookup[normalize_team_name(team["name"])] = team
        if team.get("abbreviation"):
            lookup[team["abbreviation"].lower()] = team
    return lookup


def match_team(pandascore_team: dict, team_lookup: dict) -> dict | None:
    """Match a PandaScore team to our lol_teams.json entry."""
    ps_name = pandascore_team.get("name", "")
    ps_acronym = pandascore_team.get("acronym", "")
    ps_slug = pandascore_team.get("slug", "")

    # Try exact matches first
    for candidate in [ps_name, ps_acronym, ps_slug]:
        if not candidate:
            continue
        key = candidate.lower().strip()
        if key in team_lookup:
            return team_lookup[key]
        norm = normalize_team_name(candidate)
        if norm in team_lookup:
            return team_lookup[norm]

    return None


def team_name_to_id(team_name: str) -> str:
    """Generate a team_id from team name, matching existing convention."""
    slug = team_name.lower().strip()
    slug = slug.replace(" ", "-").replace(".", "")
    return f"lol-{slug}"


def fetch_team_players(team_id: int) -> list[dict]:
    """Fetch players for a specific PandaScore team ID."""
    url = f"{PANDASCORE_BASE}/lol/players?filter[team_id]={team_id}&per_page=25"
    data = fetch_json(url)
    return data if isinstance(data, list) else []


def parse_player(ps_player: dict, our_team: dict) -> dict:
    """Parse a PandaScore player into our schema."""
    first = ps_player.get("first_name") or ""
    last = ps_player.get("last_name") or ""
    real_name = f"{first} {last}".strip() or None

    # Calculate age from birthday if available
    age = None
    birthday = ps_player.get("birthday")
    if birthday:
        try:
            born = datetime.fromisoformat(birthday)
            age = (datetime.now(timezone.utc) - born.replace(tzinfo=timezone.utc)).days // 365
        except (ValueError, TypeError):
            pass

    ign = ps_player.get("name", "")
    ps_id = ps_player.get("id")
    nationality = ps_player.get("nationality") or None
    role = ps_player.get("role") or ps_player.get("current_videogame", {}).get("role") or None
    image_url = ps_player.get("image_url") or None

    team_name = our_team["name"]
    team_id = team_name_to_id(team_name)
    region = our_team.get("metadata", {}).get("region", "")

    # Build aliases
    aliases = [{"alias": ign, "source_id": "global"}]
    if real_name:
        aliases.append({"alias": real_name, "source_id": "global"})
    if ps_id:
        aliases.append({"alias": str(ps_id), "source_id": "pandascore_lol"})

    return {
        "name": ign,
        "sport": "lol",
        "position": role,
        "team_id": team_id,
        "nationality": nationality,
        "metadata": {
            "real_name": real_name,
            "team": team_name,
            "league": region,
            "age": age,
            "pandascore_player_id": ps_id,
            "image_url": image_url,
        },
        "aliases": aliases,
    }


def main():
    parser = argparse.ArgumentParser(description="Fetch LoL player rosters from PandaScore")
    parser.add_argument("--team", type=str, help="Fetch only one team by name/abbreviation")
    args = parser.parse_args()

    if not PANDASCORE_TOKEN:
        print("Error: PANDASCORE_TOKEN environment variable not set.")
        print("Get a free token at https://pandascore.co/")
        return

    with log_script_run("fetch_lol_players") as run:
        teams = load_data("lol_teams.json")
        team_lookup = build_team_lookup(teams)

        if args.team:
            key = args.team.lower().strip()
            matching = [t for t in teams
                        if key in (t["name"].lower(), t.get("abbreviation", "").lower(),
                                   t.get("short_name", "").lower())]
            if not matching:
                print(f"No team found matching '{args.team}'")
                return
            teams = matching

        print(f"Fetching players for {len(teams)} teams...")

        all_players = []
        teams_matched = 0
        teams_missed = 0

        # Fetch all PandaScore teams to get their IDs
        print("  Loading PandaScore team index...")
        ps_teams = []
        page = 1
        while True:
            url = f"{PANDASCORE_BASE}/lol/teams?per_page=100&page={page}"
            data = fetch_json(url)
            if not data or not isinstance(data, list):
                break
            ps_teams.extend(data)
            if len(data) < 100:
                break
            page += 1

        print(f"  PandaScore has {len(ps_teams)} LoL teams indexed")

        # Match our teams to PandaScore team IDs
        ps_team_lookup = {}
        for ps_team in ps_teams:
            our_team = match_team(ps_team, team_lookup)
            if our_team:
                ps_team_lookup[our_team["name"]] = ps_team

        print(f"  Matched {len(ps_team_lookup)}/{len(teams)} teams to PandaScore")

        # Fetch players per matched team
        for team in teams:
            ps_team = ps_team_lookup.get(team["name"])
            if not ps_team:
                print(f"  [MISS] {team['name']} -- no PandaScore match")
                teams_missed += 1
                continue

            ps_team_id = ps_team["id"]
            players = fetch_team_players(ps_team_id)

            if not players:
                print(f"  [EMPTY] {team['name']} -- PandaScore returned 0 players")
                teams_missed += 1
                continue

            team_players = []
            for ps_player in players:
                parsed = parse_player(ps_player, team)
                team_players.append(parsed)

            all_players.extend(team_players)
            teams_matched += 1
            print(f"  [OK] {team['name']}: {len(team_players)} players")

        # Sort by team then name
        all_players.sort(key=lambda p: (p.get("team_id", ""), p.get("name", "")))

        save_data("lol_players.json", all_players)

        run.records_processed = len(all_players)
        run.summary = (
            f"Fetched {len(all_players)} players from {teams_matched} teams "
            f"({teams_missed} teams unmatched)"
        )

        print(f"\nDone: {len(all_players)} players from {teams_matched} teams")
        print(f"  Unmatched teams: {teams_missed}")

        # Show sample
        if all_players:
            print("\nSample players:")
            for p in all_players[:5]:
                role = p.get("position") or "?"
                print(f"  {p['name']} ({role}) -- {p['metadata']['team']}")

        print(f"\nWritten to lol_players.json")


if __name__ == "__main__":
    main()
