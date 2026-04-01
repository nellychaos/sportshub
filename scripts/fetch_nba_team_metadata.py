"""Enrich NBA team metadata from ESPN public API.

Adds arena, head coach, colors, logo URL, and ESPN team ID to each team
in data/nba_teams.json. Also adds an 'id' field for cross-referencing.

Usage: python3 -m scripts.fetch_nba_team_metadata
"""

import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TEAMS_FILE = DATA_DIR / "nba_teams.json"

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
ESPN_TEAM_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fetch_json(url: str) -> dict:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_espn_team_id_map() -> dict[str, dict]:
    """Fetch all teams from ESPN list endpoint, keyed by abbreviation."""
    data = fetch_json(ESPN_TEAMS_URL)
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
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{espn_team_id}/roster"
        data = fetch_json(url)
        coaches = data.get("coach", [])
        if coaches:
            coach = coaches[0]
            our_team["metadata"]["head_coach"] = f"{coach['firstName']} {coach['lastName']}"
    except Exception as e:
        print(f"  Warning: could not fetch coach: {e}")
    return our_team


def main():
    with open(TEAMS_FILE) as f:
        teams = json.load(f)

    print(f"Loaded {len(teams)} teams from {TEAMS_FILE.name}")

    # Fetch ESPN team list (single request)
    print("Fetching ESPN team list...")
    espn_map = get_espn_team_id_map()
    print(f"  Got {len(espn_map)} ESPN teams")

    # Map our abbreviations to ESPN abbreviations where they differ
    ABBREV_MAP = {
        "GSW": "GS",
        "NOP": "NO",
        "NYK": "NY",
        "SAS": "SA",
        "UTA": "UTAH",
        "WAS": "WSH",
    }

    enriched = 0
    for team in teams:
        abbr = team["abbreviation"]
        espn_abbr = ABBREV_MAP.get(abbr, abbr)

        # Add id field
        team["id"] = f"nba-{abbr.lower()}"

        espn_data = espn_map.get(espn_abbr)
        if not espn_data:
            print(f"  WARNING: No ESPN match for {abbr}")
            continue

        espn_id = espn_data["id"]
        print(f"  [{enriched+1}/30] {team['name']} (ESPN ID: {espn_id})...")

        # Fetch detail for venue info
        time.sleep(1)
        try:
            detail = fetch_json(ESPN_TEAM_URL.format(team_id=espn_id))
        except Exception as e:
            print(f"    Warning: detail fetch failed: {e}")
            detail = {"team": {}}

        enrich_team(team, espn_data, detail)

        # Fetch roster for coach
        time.sleep(1)
        add_coach_from_roster(team, espn_id)

        enriched += 1

    # Write back
    with open(TEAMS_FILE, "w") as f:
        json.dump(teams, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nDone: enriched {enriched}/30 teams in {TEAMS_FILE.name}")


if __name__ == "__main__":
    main()
