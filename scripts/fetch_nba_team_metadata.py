"""Enrich NBA team metadata from ESPN public API.

Adds arena, head coach, colors, logo URL, and ESPN team ID to each team
in data/nba_teams.json. Also adds an 'id' field for cross-referencing.

Usage: python3 -m scripts.fetch_nba_team_metadata
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.providers.abbreviations import standard_to_espn
from sportshub.providers.registry import ProviderRegistry
from sportshub.scripts.http import ScriptHttpClient
from sportshub.scripts.io import load_data, save_data

_registry = ProviderRegistry()
_client = ScriptHttpClient("espn_nba", registry=_registry)


def get_espn_team_id_map() -> dict[str, dict]:
    """Fetch all teams from ESPN list endpoint, keyed by abbreviation."""
    data = _client.get_endpoint("teams")
    teams = {}
    for entry in data["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        teams[t["abbreviation"]] = t
    return teams


def extract_logo_url(logos: list[dict]) -> str | None:
    """Get the default full-size logo URL."""
    for logo in logos:
        rels = logo.get("rel", [])
        if "full" in rels and "default" in rels:
            return logo["href"]
    return logos[0]["href"] if logos else None


def enrich_team(our_team: dict, espn_list_data: dict, espn_detail: dict) -> dict:
    """Merge ESPN data into our team's metadata."""
    meta = our_team.get("metadata", {})

    # Colors
    meta["team_color_primary"] = f"#{espn_list_data.get('color', '')}"
    meta["team_color_secondary"] = f"#{espn_list_data.get('alternateColor', '')}"

    # Logo
    logos = espn_list_data.get("logos", [])
    logo_url = extract_logo_url(logos)
    if logo_url:
        meta["logo_url"] = logo_url

    # ESPN ID
    meta["espn_team_id"] = espn_list_data["id"]

    # Venue and coach from detail endpoint
    franchise = espn_detail.get("team", {}).get("franchise", {})
    venue = franchise.get("venue", {})
    if venue:
        meta["arena"] = venue.get("fullName", "")
        addr = venue.get("address", {})
        meta["arena_city"] = addr.get("city", "")
        meta["arena_state"] = addr.get("state", "")

    our_team["metadata"] = meta
    return our_team


def add_coach_from_roster(our_team: dict, espn_team_id: str) -> dict:
    """Fetch roster endpoint to get head coach name."""
    try:
        data = _client.get_endpoint("roster", team_id=espn_team_id)
        coaches = data.get("coach", [])
        if coaches:
            coach = coaches[0]
            our_team["metadata"]["head_coach"] = f"{coach['firstName']} {coach['lastName']}"
    except Exception as e:
        print(f"  Warning: could not fetch coach: {e}")
    return our_team


def main():
    teams = load_data("nba_teams.json")

    print(f"Loaded {len(teams)} teams from nba_teams.json")

    # Fetch ESPN team list (single request)
    print("Fetching ESPN team list...")
    espn_map = get_espn_team_id_map()
    print(f"  Got {len(espn_map)} ESPN teams")

    enriched = 0
    for team in teams:
        abbr = team["abbreviation"]
        espn_abbr = standard_to_espn(abbr)

        # Add id field
        team["id"] = f"nba-{abbr.lower()}"

        espn_data = espn_map.get(espn_abbr)
        if not espn_data:
            print(f"  WARNING: No ESPN match for {abbr}")
            continue

        espn_id = espn_data["id"]
        print(f"  [{enriched+1}/30] {team['name']} (ESPN ID: {espn_id})...")

        try:
            detail = _client.get_endpoint("team_detail", team_id=espn_id)
        except Exception as e:
            print(f"    Warning: detail fetch failed: {e}")
            detail = {"team": {}}

        enrich_team(team, espn_data, detail)
        add_coach_from_roster(team, espn_id)

        enriched += 1

    save_data("nba_teams.json", teams)
    print(f"\nDone: enriched {enriched}/30 teams in nba_teams.json")


if __name__ == "__main__":
    main()
