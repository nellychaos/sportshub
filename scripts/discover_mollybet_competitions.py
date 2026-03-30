"""Discover Mollybet competition IDs and populate data/mollybet_competitions.json.

Run this once after configuring credentials:
    SPORTSHUB_MOLLYBET_USERNAME=xxx SPORTSHUB_MOLLYBET_PASSWORD=yyy \
        python -m scripts.discover_mollybet_competitions

Outputs the updated JSON with real Mollybet competition IDs filled in.
"""

import asyncio
import json
from pathlib import Path

import httpx


API_URL = "https://api.mollybet.com"
DATA_FILE = Path(__file__).parent.parent / "data" / "mollybet_competitions.json"

# Mollybet sport codes we care about
TARGET_SPORTS = {"fb", "basket", "esports"}

# Keywords used to match competitions to our known ones
MATCH_KEYWORDS: dict[str, list[str]] = {
    "WC2026":  ["world cup", "world cup 2026", "fifa world cup"],
    "NBA-RS":  ["nba", "national basketball association"],
    "NBA-PO":  ["nba playoff", "nba finals"],
    "LCK":     ["lck", "league championship korea", "league of legends champions korea"],
    "LPL":     ["lpl", "league pro league", "lol pro league"],
    "LEC":     ["lec", "league emea championship", "emea championship"],
    "LCS":     ["lcs", "league championship series"],
    "MSI":     ["msi", "mid-season invitational", "mid season invitational"],
    "WORLDS":  ["worlds", "world championship", "lol world"],
}


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        f"{API_URL}/v1/sessions/",
        json={"username": username, "password": password},
    )
    resp.raise_for_status()
    token = resp.json()["session_id"]
    print(f"  Logged in. Session: {token[:16]}...")
    return token


async def discover(username: str, password: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        token = await login(client, username, password)
        headers = {"Session": token}

        # Fetch available competition countries
        countries_resp = await client.get(
            f"{API_URL}/v1/orders/filters/countries/", headers=headers
        )
        countries_resp.raise_for_status()
        countries = countries_resp.json()
        print(f"  Got {len(countries)} countries")

        # Collect all competitions across all countries
        all_competitions: list[dict] = []
        for country in countries:
            try:
                comps_resp = await client.get(
                    f"{API_URL}/v1/orders/filters/competitions/",
                    params={"country": country},
                    headers=headers,
                )
                comps_resp.raise_for_status()
                comps = comps_resp.json()
                for c in comps:
                    c["_country"] = country
                all_competitions.extend(comps)
            except Exception as e:
                print(f"  Warning: failed to fetch competitions for {country}: {e}")

        print(f"  Total competitions found: {len(all_competitions)}")

        # Load our seed file
        seed_data = json.loads(DATA_FILE.read_text())
        updated = 0

        for entry in seed_data:
            short_name = entry["sportshub_short_name"]
            mollybet_sport = entry["mollybet_sport"]
            keywords = [k.lower() for k in MATCH_KEYWORDS.get(short_name, [])]

            if not keywords:
                continue

            # Filter competitions by sport code
            sport_comps = [
                c for c in all_competitions
                if c.get("sport", "").lower() == mollybet_sport
                or mollybet_sport in str(c).lower()
            ]

            best_match = None
            for comp in sport_comps:
                comp_name = str(comp.get("name", "") or comp).lower()
                comp_id = str(comp.get("id", "") or comp.get("competition_id", ""))
                if any(kw in comp_name for kw in keywords) and comp_id:
                    best_match = (comp_id, comp.get("name", ""), comp.get("_country", ""))
                    break

            if best_match:
                comp_id, comp_name, country = best_match
                entry["mollybet_competition_id"] = comp_id
                entry["_discovered_name"] = comp_name
                entry["_discovered_country"] = country
                print(f"  {short_name} → {comp_id} ({comp_name})")
                updated += 1
            else:
                print(f"  {short_name} → NOT FOUND (sport={mollybet_sport}, keywords={keywords[:2]})")

        # Write back
        DATA_FILE.write_text(json.dumps(seed_data, indent=2, ensure_ascii=False))
        print(f"\nDone. {updated}/{len(seed_data)} competitions mapped.")
        print(f"Updated: {DATA_FILE}")

        # Logout
        await client.delete(f"{API_URL}/v1/sessions/{token}/", headers=headers)


def main() -> None:
    import os
    username = os.environ.get("SPORTSHUB_MOLLYBET_USERNAME") or os.environ.get("MOLLYBET_USERNAME")
    password = os.environ.get("SPORTSHUB_MOLLYBET_PASSWORD") or os.environ.get("MOLLYBET_PASSWORD")

    if not username or not password:
        print("Error: set SPORTSHUB_MOLLYBET_USERNAME and SPORTSHUB_MOLLYBET_PASSWORD env vars")
        raise SystemExit(1)

    print(f"Discovering Mollybet competition IDs for user '{username}'...")
    asyncio.run(discover(username, password))


if __name__ == "__main__":
    main()
