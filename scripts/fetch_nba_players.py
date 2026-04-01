"""Fetch NBA player rosters from ESPN public API for all 30 teams.

Populates data/nba_players.json with full roster data including
name, position, jersey number, height, weight, age, DOB, college,
experience, and birthplace.

Usage: python3 -m scripts.fetch_nba_players
       python3 -m scripts.fetch_nba_players --team BOS  # single team
"""

import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import Request, urlopen

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TEAMS_FILE = DATA_DIR / "nba_teams.json"
PLAYERS_FILE = DATA_DIR / "nba_players.json"

ESPN_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ESPN position abbreviation normalization
POSITION_MAP = {
    "PG": "PG",
    "SG": "SG",
    "SF": "SF",
    "PF": "PF",
    "C": "C",
    "G": "G",
    "F": "F",
    "G-F": "G-F",
    "F-G": "F-G",
    "F-C": "F-C",
    "C-F": "C-F",
    "Guard": "G",
    "Forward": "F",
    "Center": "C",
}


def fetch_json(url: str) -> dict:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def inches_to_cm(inches: float) -> int:
    return round(inches * 2.54)


def lbs_to_kg(lbs: float) -> int:
    return round(lbs * 0.453592)


def format_height(inches: float) -> str:
    feet = int(inches // 12)
    remaining = int(inches % 12)
    return f"{feet}-{remaining}"


def nationality_from_birthplace(birthplace: dict) -> str:
    country = birthplace.get("country", "")
    if not country:
        return "American"

    COUNTRY_TO_NATIONALITY = {
        "USA": "American",
        "Canada": "Canadian",
        "France": "French",
        "Germany": "German",
        "Australia": "Australian",
        "Spain": "Spanish",
        "Serbia": "Serbian",
        "Slovenia": "Slovenian",
        "Croatia": "Croatian",
        "Greece": "Greek",
        "Cameroon": "Cameroonian",
        "Nigeria": "Nigerian",
        "Japan": "Japanese",
        "China": "Chinese",
        "South Sudan": "South Sudanese",
        "Sudan": "Sudanese",
        "Dominican Republic": "Dominican",
        "Bahamas": "Bahamian",
        "Jamaica": "Jamaican",
        "Brazil": "Brazilian",
        "Argentina": "Argentine",
        "Mexico": "Mexican",
        "Turkey": "Turkish",
        "Israel": "Israeli",
        "Lithuania": "Lithuanian",
        "Latvia": "Latvian",
        "Montenegro": "Montenegrin",
        "Bosnia and Herzegovina": "Bosnian",
        "Czech Republic": "Czech",
        "Austria": "Austrian",
        "Italy": "Italian",
        "United Kingdom": "British",
        "England": "English",
        "Georgia": "Georgian",
        "Senegal": "Senegalese",
        "Congo": "Congolese",
        "Democratic Republic of the Congo": "Congolese",
        "Mali": "Malian",
        "Guinea": "Guinean",
        "Ivory Coast": "Ivorian",
        "New Zealand": "New Zealander",
        "Philippines": "Filipino",
        "South Korea": "South Korean",
        "Puerto Rico": "Puerto Rican",
        "US Virgin Islands": "American",
        "Belgium": "Belgian",
        "Finland": "Finnish",
        "Sweden": "Swedish",
        "Switzerland": "Swiss",
        "Portugal": "Portuguese",
        "Poland": "Polish",
        "Ukraine": "Ukrainian",
        "Russia": "Russian",
        "Haiti": "Haitian",
        "Trinidad and Tobago": "Trinidadian",
        "Egypt": "Egyptian",
        "Morocco": "Moroccan",
        "Tunisia": "Tunisian",
        "Angola": "Angolan",
        "Gabon": "Gabonese",
        "Lebanon": "Lebanese",
        "Iran": "Iranian",
        "Taiwan": "Taiwanese",
        "India": "Indian",
        "Colombia": "Colombian",
        "Venezuela": "Venezuelan",
    }
    return COUNTRY_TO_NATIONALITY.get(country, country)


def parse_athlete(athlete: dict, team_id: str, team_name: str) -> dict:
    """Parse an ESPN athlete object into our player schema."""
    name = athlete.get("displayName", athlete.get("fullName", ""))
    short_name = athlete.get("shortName", "")

    # Position
    pos_data = athlete.get("position", {})
    pos_abbr = pos_data.get("abbreviation", "")
    position = POSITION_MAP.get(pos_abbr, pos_abbr)

    # Physical attributes
    height_inches = athlete.get("height", 0)
    weight_lbs = athlete.get("weight", 0)

    # Birthplace and nationality
    birthplace = athlete.get("birthPlace", {})
    nationality = nationality_from_birthplace(birthplace)

    # College
    college_data = athlete.get("college", {})
    college = college_data.get("name") or college_data.get("shortName") or ""

    # Experience
    exp = athlete.get("experience", {})
    years_exp = exp.get("years", 0) if isinstance(exp, dict) else 0

    # DOB
    dob_raw = athlete.get("dateOfBirth", "")
    dob = dob_raw[:10] if dob_raw else ""

    player = {
        "name": name,
        "sport": "nba",
        "position": position,
        "jersey_number": str(athlete.get("jersey", "")),
        "nationality": nationality,
        "team_id": team_id,
        "metadata": {
            "team": team_name,
            "espn_player_id": athlete.get("id", ""),
        },
        "aliases": [
            {"alias": name, "source_id": "global"},
        ],
    }

    # Add short name alias if different
    if short_name and short_name != name:
        player["aliases"].append({"alias": short_name, "source_id": "espn_nba"})

    # ESPN player ID alias
    espn_id = athlete.get("id", "")
    if espn_id:
        player["aliases"].append({"alias": espn_id, "source_id": "espn_nba_id"})

    # Physical metadata
    meta = player["metadata"]
    if height_inches:
        meta["height"] = format_height(height_inches)
        meta["height_cm"] = inches_to_cm(height_inches)
    if weight_lbs:
        meta["weight_lbs"] = int(weight_lbs)
        meta["weight_kg"] = lbs_to_kg(weight_lbs)
    if dob:
        meta["date_of_birth"] = dob
    if athlete.get("age"):
        meta["age"] = athlete["age"]
    if college:
        meta["college"] = college
    if years_exp:
        meta["years_experience"] = years_exp
    if athlete.get("debutYear"):
        meta["debut_year"] = athlete["debutYear"]

    # Birthplace
    if birthplace.get("city"):
        bp = birthplace.get("city", "")
        if birthplace.get("state"):
            bp += f", {birthplace['state']}"
        if birthplace.get("country") and birthplace["country"] != "USA":
            bp += f", {birthplace['country']}"
        meta["birthplace"] = bp

    return player


def main():
    parser = argparse.ArgumentParser(description="Fetch NBA player rosters from ESPN")
    parser.add_argument("--team", type=str, help="Fetch only one team by abbreviation (e.g., BOS)")
    args = parser.parse_args()

    with open(TEAMS_FILE) as f:
        teams = json.load(f)

    if args.team:
        teams = [t for t in teams if t["abbreviation"] == args.team.upper()]
        if not teams:
            print(f"Team {args.team} not found")
            return

    print(f"Fetching rosters for {len(teams)} teams...")

    all_players = []
    for i, team in enumerate(teams):
        team_id = team["id"]
        team_name = team["name"]
        espn_id = team.get("metadata", {}).get("espn_team_id")

        if not espn_id:
            print(f"  WARNING: {team_name} has no espn_team_id, skipping")
            continue

        print(f"  [{i+1}/{len(teams)}] {team_name}...", end="", flush=True)

        try:
            data = fetch_json(ESPN_ROSTER_URL.format(team_id=espn_id))
            athletes = data.get("athletes", [])

            team_players = []
            for athlete in athletes:
                player = parse_athlete(athlete, team_id, team_name)
                team_players.append(player)

            all_players.extend(team_players)
            print(f" {len(team_players)} players")

        except Exception as e:
            print(f" ERROR: {e}")

        time.sleep(1.5)

    # Sort by team, then by name
    all_players.sort(key=lambda p: (p["team_id"], p["name"]))

    with open(PLAYERS_FILE, "w") as f:
        json.dump(all_players, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Summary
    teams_with_players = len(set(p["team_id"] for p in all_players))
    print(f"\nDone: {len(all_players)} players across {teams_with_players} teams")
    print(f"Written to {PLAYERS_FILE.name}")


if __name__ == "__main__":
    main()
