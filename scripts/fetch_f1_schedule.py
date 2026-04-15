"""Fetch 2026 F1 season calendar and circuit data from multiple sources.

Combines:
- Jolpica/Ergast API: race schedule, session times, circuit lat/long
- OpenF1 API: circuit images, types, flags, sponsor names
- Multiviewer API: track geometry (x/y coordinates, corners, sectors)
- GitHub SVGs: pre-rendered track layout URLs

Creates:
- data/f1_schedule_2026.json  (22 races with full session times)
- data/f1_circuits.json       (circuit catalog with track layouts)

Usage: python3 -m scripts.fetch_f1_schedule
"""

import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.io import save_data

# ── Constants ──────────────────────────────────────────────────────

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE = "https://api.openf1.org/v1"
MULTIVIEWER_BASE = "https://api.multiviewer.app/api/v1/circuits"
SVG_BASE = "https://raw.githubusercontent.com/julesr0y/f1-circuits-svg/main/circuits/detailed/white"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Sportshub/1.0)",
    "Accept": "application/json",
}

# Map Jolpica circuit IDs to julesr0y SVG filenames
CIRCUIT_SVG_MAP = {
    "albert_park": "melbourne",
    "shanghai": "shanghai",
    "suzuka": "suzuka",
    "miami": "miami",
    "BAK": "baku",
    "baku": "baku",
    "monaco": "monaco",
    "villeneuve": "montreal",
    "silverstone": "silverstone",
    "spa": "spa-francorchamps",
    "red_bull_ring": "spielberg",
    "hungaroring": "hungaroring",
    "zandvoort": "zandvoort",
    "monza": "monza",
    "marina_bay": "marina-bay",
    "rodriguez": "mexico-city",
    "americas": "austin",
    "interlagos": "interlagos",
    "vegas": "las-vegas",
    "losail": "lusail",
    "yas_marina": "yas-marina",
    "jeddah": "jeddah",
    "catalunya": "catalunya",
    "imola": None,  # Not in SVG repo
    "portimao": None,
    "ricard": None,
}

RATE_LIMIT = 1.0  # seconds between requests
_last_req = 0.0


def _fetch(url: str) -> dict | list | None:
    """Fetch JSON with rate limiting and error handling."""
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


# ── Jolpica: Season Schedule ───────────────────────────────────────

def fetch_jolpica_schedule(year: int = 2026) -> list[dict]:
    """Fetch full season schedule from Jolpica/Ergast."""
    print(f"Fetching Jolpica {year} schedule...")
    data = _fetch(f"{JOLPICA_BASE}/{year}.json?limit=50")
    if not data:
        return []
    races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    print(f"  Found {len(races)} races")
    return races


def fetch_jolpica_circuits() -> list[dict]:
    """Fetch all-time circuit catalog from Jolpica/Ergast."""
    print("Fetching Jolpica circuit catalog...")
    data = _fetch(f"{JOLPICA_BASE}/circuits.json?limit=100")
    if not data:
        return []
    circuits = data.get("MRData", {}).get("CircuitTable", {}).get("Circuits", [])
    print(f"  Found {len(circuits)} circuits total")
    return circuits


# ── OpenF1: Meetings (enrichment) ─────────────────────────────────

def fetch_openf1_meetings(year: int = 2026) -> list[dict]:
    """Fetch meetings from OpenF1 for enrichment data."""
    print(f"Fetching OpenF1 {year} meetings...")
    data = _fetch(f"{OPENF1_BASE}/meetings?year={year}")
    if not data:
        return []
    # Filter out pre-season testing
    races = [m for m in data if "Grand Prix" in m.get("meeting_name", "")]
    print(f"  Found {len(races)} race meetings (filtered from {len(data)})")
    return races


# ── Multiviewer: Track Geometry ────────────────────────────────────

def fetch_multiviewer_circuit(circuit_key: int, year: int = 2026) -> dict | None:
    """Fetch track geometry from Multiviewer API."""
    data = _fetch(f"{MULTIVIEWER_BASE}/{circuit_key}/{year}")
    if not data:
        # Fall back to earlier years if 2026 data not available yet
        for fallback_year in [2025, 2024, 2023]:
            data = _fetch(f"{MULTIVIEWER_BASE}/{circuit_key}/{fallback_year}")
            if data:
                break
    return data


# ── Build Schedule ─────────────────────────────────────────────────

def build_schedule(jolpica_races: list[dict], openf1_meetings: list[dict]) -> list[dict]:
    """Merge Jolpica schedule with OpenF1 enrichment."""
    # Index OpenF1 meetings by circuit short name for matching
    of1_by_name = {}
    for m in openf1_meetings:
        key = m.get("meeting_name", "").replace(" Grand Prix", "").strip().lower()
        of1_by_name[key] = m

    schedule = []
    for race in jolpica_races:
        circuit = race.get("Circuit", {})
        circuit_id = circuit.get("circuitId", "")

        # Build session schedule
        sessions = {}
        for key in ("FirstPractice", "SecondPractice", "ThirdPractice",
                     "Qualifying", "Sprint", "SprintQualifying"):
            session_data = race.get(key)
            if session_data:
                sessions[key] = {
                    "date": session_data.get("date"),
                    "time_utc": session_data.get("time"),
                }

        is_sprint = "Sprint" in race

        # Match to OpenF1 for enrichment
        race_name = race.get("raceName", "")
        match_key = race_name.replace(" Grand Prix", "").strip().lower()
        of1 = of1_by_name.get(match_key, {})

        entry = {
            "round": int(race.get("round", 0)),
            "race_name": race_name,
            "official_name": of1.get("meeting_official_name"),
            "date": race.get("date"),
            "time_utc": race.get("time"),
            "is_sprint_weekend": is_sprint,
            "sessions": sessions,
            "circuit_id": circuit_id,
            "circuit_name": circuit.get("circuitName"),
            "location": circuit.get("Location", {}).get("locality"),
            "country": circuit.get("Location", {}).get("country"),
            "wikipedia_url": race.get("url"),
            "country_flag_url": of1.get("country_flag"),
            "circuit_image_url": of1.get("circuit_image"),
            "openf1_meeting_key": of1.get("meeting_key"),
            "openf1_circuit_key": of1.get("circuit_key"),
        }
        schedule.append(entry)

    return schedule


# ── Build Circuit Catalog ──────────────────────────────────────────

def build_circuits(
    schedule: list[dict],
    jolpica_circuits: list[dict],
    openf1_meetings: list[dict],
) -> list[dict]:
    """Build enriched circuit catalog with track geometry."""
    # Index Jolpica circuits by ID
    jol_by_id = {c["circuitId"]: c for c in jolpica_circuits}

    # Index OpenF1 meetings by circuit_key for enrichment
    of1_by_key = {}
    for m in openf1_meetings:
        ck = m.get("circuit_key")
        if ck and ck not in of1_by_key:
            of1_by_key[ck] = m

    # Collect unique circuits from the 2026 schedule
    seen = set()
    circuits = []

    for race in schedule:
        cid = race.get("circuit_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)

        jol = jol_by_id.get(cid, {})
        loc = jol.get("Location", {})
        of1_key = race.get("openf1_circuit_key")
        of1 = of1_by_key.get(of1_key, {})

        # SVG track map URL
        svg_name = CIRCUIT_SVG_MAP.get(cid)
        svg_url = f"{SVG_BASE}/{svg_name}.svg" if svg_name else None

        entry = {
            "circuit_id": cid,
            "circuit_name": jol.get("circuitName") or race.get("circuit_name"),
            "locality": loc.get("locality") or race.get("location"),
            "country": loc.get("country") or race.get("country"),
            "latitude": float(loc["lat"]) if loc.get("lat") else None,
            "longitude": float(loc["long"]) if loc.get("long") else None,
            "wikipedia_url": jol.get("url"),
            "circuit_type": of1.get("circuit_type"),
            "circuit_image_url": race.get("circuit_image_url"),
            "country_flag_url": race.get("country_flag_url"),
            "svg_track_map_url": svg_url,
            "openf1_circuit_key": of1_key,
            "track_layout": None,  # Filled by multiviewer fetch
        }
        circuits.append(entry)

    # Fetch track geometry from Multiviewer for each circuit
    for circuit in circuits:
        ck = circuit.get("openf1_circuit_key")
        if not ck:
            print(f"  No circuit_key for {circuit['circuit_id']}, skipping geometry")
            continue

        print(f"  Fetching track geometry: {circuit['circuit_name']} (key={ck})")
        mv = fetch_multiviewer_circuit(ck)
        if mv:
            corners = mv.get("corners", [])
            circuit["track_layout"] = {
                "x": mv.get("x", []),
                "y": mv.get("y", []),
                "rotation": mv.get("rotation", 0),
                "num_points": len(mv.get("x", [])),
                "corners": [
                    {
                        "number": c.get("number"),
                        "angle": round(c.get("angle", 0), 1),
                        "position": c.get("trackPosition"),
                    }
                    for c in corners
                ],
                "num_corners": len(corners),
                "marshal_sectors": len(mv.get("marshalSectors", [])),
                "candidate_lap": mv.get("candidateLap"),
            }
        else:
            print(f"    No geometry data available")

    return circuits


# ── Main ───────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("F1 2026 Season Schedule + Circuit Fetch")
    print("=" * 60)

    # Fetch from all sources
    jolpica_races = fetch_jolpica_schedule(2026)
    jolpica_circuits = fetch_jolpica_circuits()
    openf1_meetings = fetch_openf1_meetings(2026)

    if not jolpica_races:
        print("ERROR: No races from Jolpica. Aborting.")
        sys.exit(1)

    # Build merged data
    print("\nBuilding schedule...")
    schedule = build_schedule(jolpica_races, openf1_meetings)

    print(f"\nBuilding circuit catalog ({len(schedule)} circuits)...")
    circuits = build_circuits(schedule, jolpica_circuits, openf1_meetings)

    # Save
    schedule_path = save_data("f1_schedule_2026.json", schedule)
    print(f"\nSaved {len(schedule)} races -> {schedule_path}")

    circuits_path = save_data("f1_circuits.json", circuits)
    with_geo = sum(1 for c in circuits if c.get("track_layout"))
    print(f"Saved {len(circuits)} circuits ({with_geo} with track geometry) -> {circuits_path}")

    # Summary
    sprints = sum(1 for r in schedule if r["is_sprint_weekend"])
    print(f"\nSummary: {len(schedule)} races, {sprints} sprint weekends, {len(circuits)} circuits")


if __name__ == "__main__":
    main()
