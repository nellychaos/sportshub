"""Fetch 2026 F1 constructor/team data from Jolpica + OpenF1.

Combines:
- Jolpica/Ergast: team identity (name, nationality, Wikipedia)
- OpenF1: team hex colors (from driver data)
- Curated metadata: team principal, HQ, founded year, championships

Creates: data/f1_constructors_2026.json

Usage: python3 -m scripts.fetch_f1_constructors
"""

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.io import load_data, save_data

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE = "https://api.openf1.org/v1"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Sportshub/1.0)",
    "Accept": "application/json",
}

RATE_LIMIT = 1.0
_last_req = 0.0


def _fetch(url: str) -> dict | list | None:
    global _last_req
    elapsed = time.monotonic() - _last_req
    if elapsed < RATE_LIMIT and _last_req > 0:
        time.sleep(RATE_LIMIT - elapsed)
    _last_req = time.monotonic()

    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, json.JSONDecodeError) as e:
        print(f"  WARN: {url} -> {e}")
        return None


# Curated team metadata not available from APIs
TEAM_METADATA = {
    "red_bull": {
        "team_principal": "Christian Horner",
        "headquarters": "Milton Keynes, UK",
        "founded": 2005,
        "championships": 6,
        "abbreviation": "RBR",
    },
    "mclaren": {
        "team_principal": "Andrea Stella",
        "headquarters": "Woking, UK",
        "founded": 1963,
        "championships": 9,
        "abbreviation": "MCL",
    },
    "ferrari": {
        "team_principal": "Fred Vasseur",
        "headquarters": "Maranello, Italy",
        "founded": 1929,
        "championships": 16,
        "abbreviation": "FER",
    },
    "mercedes": {
        "team_principal": "Toto Wolff",
        "headquarters": "Brackley, UK",
        "founded": 2010,
        "championships": 8,
        "abbreviation": "MER",
    },
    "aston_martin": {
        "team_principal": "Andy Cowell",
        "headquarters": "Silverstone, UK",
        "founded": 2021,
        "championships": 0,
        "abbreviation": "AMR",
    },
    "alpine": {
        "team_principal": "Oliver Oakes",
        "headquarters": "Enstone, UK",
        "founded": 2021,
        "championships": 0,
        "abbreviation": "ALP",
    },
    "williams": {
        "team_principal": "James Vowles",
        "headquarters": "Grove, UK",
        "founded": 1977,
        "championships": 9,
        "abbreviation": "WIL",
    },
    "rb": {
        "team_principal": "Laurent Mekies",
        "headquarters": "Faenza, Italy",
        "founded": 2006,
        "championships": 0,
        "abbreviation": "RB",
    },
    "haas": {
        "team_principal": "Ayao Komatsu",
        "headquarters": "Kannapolis, USA",
        "founded": 2016,
        "championships": 0,
        "abbreviation": "HAA",
    },
    "audi": {
        "team_principal": "Mattia Binotto",
        "headquarters": "Hinwil, Switzerland",
        "founded": 1993,
        "championships": 0,
        "abbreviation": "AUD",
        "notes": "Formerly Sauber, rebranded as Audi for 2026",
    },
    "cadillac": {
        "team_principal": "Graeme Lowdon",
        "headquarters": "Detroit, USA",
        "founded": 2026,
        "championships": 0,
        "abbreviation": "CAD",
        "notes": "New entry for 2026 (GM/Cadillac)",
    },
}


def fetch_jolpica_constructors(year: int = 2026) -> list[dict]:
    print(f"Fetching Jolpica {year} constructors...")
    data = _fetch(f"{JOLPICA_BASE}/{year}/constructors.json")
    if not data:
        return []
    constructors = data.get("MRData", {}).get("ConstructorTable", {}).get("Constructors", [])
    print(f"  Found {len(constructors)} constructors")
    return constructors


def fetch_openf1_team_colors() -> dict[str, str]:
    """Extract team colors from OpenF1 driver data."""
    print("Fetching OpenF1 team colors from driver data...")
    data = _fetch(f"{OPENF1_BASE}/drivers?session_key=latest")
    if not data:
        return {}
    colors = {}
    for d in data:
        team = d.get("team_name")
        color = d.get("team_colour")
        if team and color and team not in colors:
            colors[team] = color
    print(f"  Found colors for {len(colors)} teams")
    return colors


def get_driver_pairs() -> dict[str, list[str]]:
    """Load driver data to build constructor->drivers mapping."""
    try:
        drivers = load_data("f1_drivers_2026.json")
    except FileNotFoundError:
        print("  WARN: f1_drivers_2026.json not found, run fetch_f1_drivers.py first")
        return {}

    pairs = {}
    for d in drivers:
        team_id = d.get("team_id", "")
        if not team_id:
            continue
        cid = team_id.replace("f1-", "")
        pairs.setdefault(cid, []).append(d["name"])
    return pairs


def build_constructors(
    jolpica: list[dict],
    team_colors: dict[str, str],
    driver_pairs: dict[str, list[str]],
) -> list[dict]:
    """Merge all constructor data."""
    # Normalize OpenF1 team names for matching
    color_map = {}
    for team_name, color in team_colors.items():
        key = team_name.lower().replace(" ", "_").replace("f1_team", "").strip("_")
        color_map[key] = color
        # Also store by simplified name
        color_map[team_name.lower()] = color

    constructors = []
    for jc in jolpica:
        cid = jc.get("constructorId", "")
        meta = TEAM_METADATA.get(cid, {})

        # Match team color from OpenF1
        team_color = None
        name_lower = jc.get("name", "").lower()
        for of1_name, color in team_colors.items():
            if name_lower in of1_name.lower() or of1_name.lower() in name_lower:
                team_color = color
                break

        drivers = driver_pairs.get(cid, [])

        entry = {
            "name": jc.get("name"),
            "short_name": jc.get("name", "").split(" ")[0] if " " in jc.get("name", "") else jc.get("name"),
            "abbreviation": meta.get("abbreviation", cid[:3].upper()),
            "sport": "f1",
            "team_id": f"f1-{cid}",
            "constructor_id": cid,
            "nationality": jc.get("nationality"),
            "wikipedia_url": jc.get("url"),
            "team_colour": f"#{team_color}" if team_color else None,
            "drivers": drivers,
            "metadata": {
                "team_principal": meta.get("team_principal"),
                "headquarters": meta.get("headquarters"),
                "founded": meta.get("founded"),
                "constructor_championships": meta.get("championships"),
                "notes": meta.get("notes"),
            },
            "aliases": [
                {"alias": jc.get("name"), "source_id": "jolpica_f1"},
                {"alias": cid, "source_id": "jolpica_f1"},
            ],
        }

        # Add abbreviation alias
        if meta.get("abbreviation"):
            entry["aliases"].append({"alias": meta["abbreviation"], "source_id": "global"})

        constructors.append(entry)

    return constructors


def main() -> None:
    print("=" * 60)
    print("F1 2026 Constructor/Team Fetch")
    print("=" * 60)

    jolpica = fetch_jolpica_constructors(2026)
    team_colors = fetch_openf1_team_colors()
    driver_pairs = get_driver_pairs()

    if not jolpica:
        print("ERROR: No constructors from Jolpica. Aborting.")
        sys.exit(1)

    print(f"\nMerging constructor data...")
    constructors = build_constructors(jolpica, team_colors, driver_pairs)

    path = save_data("f1_constructors_2026.json", constructors)
    print(f"\nSaved {len(constructors)} constructors -> {path}")

    # Show grid
    print("\n2026 Constructor Grid:")
    for c in constructors:
        drivers = ", ".join(c.get("drivers", [])) or "TBD"
        color = c.get("team_colour") or "?"
        print(f"  {c['abbreviation']:>3} {c['name']:<25} ({color}) -> {drivers}")


if __name__ == "__main__":
    main()
