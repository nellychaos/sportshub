"""Fetch 2026 F1 driver roster from Jolpica + OpenF1.

Combines:
- Jolpica/Ergast: driver identity (name, number, code, DOB, nationality, Wikipedia)
- OpenF1: team assignment, headshot URLs, team hex colors

Creates: data/f1_drivers_2026.json

Usage: python3 -m scripts.fetch_f1_drivers
"""

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.io import save_data

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


def fetch_jolpica_drivers(year: int = 2026) -> list[dict]:
    """Fetch drivers from Jolpica/Ergast."""
    print(f"Fetching Jolpica {year} drivers...")
    data = _fetch(f"{JOLPICA_BASE}/{year}/drivers.json?limit=50")
    if not data:
        return []
    drivers = data.get("MRData", {}).get("DriverTable", {}).get("Drivers", [])
    print(f"  Found {len(drivers)} drivers")
    return drivers


def fetch_openf1_drivers() -> list[dict]:
    """Fetch drivers from OpenF1 latest session for team + headshot data."""
    print("Fetching OpenF1 latest session drivers...")
    data = _fetch(f"{OPENF1_BASE}/drivers?session_key=latest")
    if not data:
        return []
    print(f"  Found {len(data)} drivers")
    return data


def fetch_jolpica_constructor_standings(year: int = 2026) -> dict:
    """Fetch driver standings to get driver-constructor mapping."""
    print(f"Fetching Jolpica {year} driver standings for team mapping...")
    data = _fetch(f"{JOLPICA_BASE}/{year}/driverStandings.json")
    if not data:
        return {}
    standings_lists = (
        data.get("MRData", {})
        .get("StandingsTable", {})
        .get("StandingsLists", [])
    )
    # Map driverId -> constructor info
    mapping = {}
    for sl in standings_lists:
        for entry in sl.get("DriverStandings", []):
            driver_id = entry.get("Driver", {}).get("driverId")
            constructors = entry.get("Constructors", [])
            if driver_id and constructors:
                mapping[driver_id] = {
                    "constructor_id": constructors[0].get("constructorId"),
                    "constructor_name": constructors[0].get("name"),
                    "points": entry.get("points"),
                    "wins": entry.get("wins"),
                    "position": entry.get("position"),
                }
    print(f"  Mapped {len(mapping)} drivers to constructors")
    return mapping


def build_drivers(
    jolpica_drivers: list[dict],
    openf1_drivers: list[dict],
    standings_map: dict,
) -> list[dict]:
    """Merge Jolpica identity with OpenF1 enrichment."""
    # Index OpenF1 by driver number for matching
    of1_by_num = {}
    for d in openf1_drivers:
        num = d.get("driver_number")
        if num is not None:
            of1_by_num[int(num)] = d

    drivers = []
    for jd in jolpica_drivers:
        driver_id = jd.get("driverId", "")
        perm_num = jd.get("permanentNumber")
        num = int(perm_num) if perm_num else None

        # OpenF1 match by driver number
        of1 = of1_by_num.get(num, {}) if num else {}

        # Standings match for constructor
        standings = standings_map.get(driver_id, {})

        # Determine team from standings (best) or OpenF1 (fallback)
        team_name = standings.get("constructor_name") or of1.get("team_name")
        team_id = standings.get("constructor_id")
        if team_id:
            team_id = f"f1-{team_id}"

        entry = {
            "name": f"{jd.get('givenName', '')} {jd.get('familyName', '')}".strip(),
            "sport": "f1",
            "driver_id": driver_id,
            "driver_number": num,
            "code": jd.get("code"),
            "date_of_birth": jd.get("dateOfBirth"),
            "nationality": jd.get("nationality"),
            "team_id": team_id,
            "team_name": team_name,
            "team_colour": of1.get("team_colour"),
            "headshot_url": of1.get("headshot_url"),
            "wikipedia_url": jd.get("url"),
            "broadcast_name": of1.get("broadcast_name"),
            "championship_position": standings.get("position"),
            "championship_points": standings.get("points"),
            "championship_wins": standings.get("wins"),
            "aliases": [
                {"alias": f"{jd.get('givenName', '')} {jd.get('familyName', '')}", "source_id": "jolpica_f1"},
            ],
        }

        # Add code-based alias
        if jd.get("code"):
            entry["aliases"].append({"alias": jd["code"], "source_id": "jolpica_f1"})

        # Add OpenF1 broadcast name alias
        if of1.get("broadcast_name"):
            entry["aliases"].append({"alias": of1["broadcast_name"], "source_id": "openf1"})

        drivers.append(entry)

    return drivers


def main() -> None:
    print("=" * 60)
    print("F1 2026 Driver Roster Fetch")
    print("=" * 60)

    jolpica_drivers = fetch_jolpica_drivers(2026)
    openf1_drivers = fetch_openf1_drivers()
    standings_map = fetch_jolpica_constructor_standings(2026)

    if not jolpica_drivers:
        print("ERROR: No drivers from Jolpica. Aborting.")
        sys.exit(1)

    print(f"\nMerging {len(jolpica_drivers)} Jolpica + {len(openf1_drivers)} OpenF1 drivers...")
    drivers = build_drivers(jolpica_drivers, openf1_drivers, standings_map)

    # Filter to drivers with a team assignment (active race drivers)
    active = [d for d in drivers if d.get("team_name")]
    reserve = [d for d in drivers if not d.get("team_name")]

    path = save_data("f1_drivers_2026.json", drivers)
    print(f"\nSaved {len(drivers)} drivers -> {path}")
    print(f"  {len(active)} with team assignment, {len(reserve)} reserve/unassigned")

    # Show driver-team grid
    print("\n2026 Driver Grid:")
    for d in sorted(active, key=lambda x: x.get("team_name", "")):
        num = d.get("driver_number", "?")
        print(f"  #{num:>2} {d['name']:<25} {d.get('team_name', '?')}")


if __name__ == "__main__":
    main()
