"""Discover Mollybet competition IDs via WebSocket stream.

Run this once after configuring credentials:
    SPORTSHUB_MOLLYBET_USERNAME=xxx SPORTSHUB_MOLLYBET_PASSWORD=yyy \
        python3 -m scripts.discover_mollybet_competitions

Connects to the Mollybet WebSocket, collects all event messages until sync,
then extracts unique competition_id/name/country mappings and matches them
against our known competitions in data/mollybet_competitions.json.
"""

import asyncio
import json
from pathlib import Path

import httpx
import websockets

API_URL = "https://api.mollybet.com"
WS_URL = "wss://api.mollybet.com"
DATA_FILE = Path(__file__).parent.parent / "data" / "mollybet_competitions.json"

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


async def login(username: str, password: str) -> str:
    """REST login -> session token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{API_URL}/v1/sessions/",
            json={"username": username, "password": password},
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("session_id") or body.get("data") or body.get("token")
        if not token:
            raise RuntimeError(f"Could not extract session token from response: {body}")
        print(f"  Logged in. Session: {token[:16]}...")
        return token


async def stream_competitions(token: str) -> dict[str, dict[str, dict]]:
    """Connect to WebSocket, collect event messages until sync.

    Returns: {sport: {competition_id: {name, country, event_count}}}
    """
    ws_url = f"{WS_URL}/v1/stream/?token={token}"
    competitions: dict[str, dict[str, dict]] = {}
    event_count = 0

    print("  Connecting to WebSocket stream...")
    async with websockets.connect(ws_url, open_timeout=15, close_timeout=5) as ws:
        deadline = asyncio.get_event_loop().time() + 120  # 2 min max
        synced = False

        while not synced:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                print("  Timeout waiting for sync.")
                break

            try:
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            try:
                msg = json.loads(raw_msg)
            except (json.JSONDecodeError, TypeError):
                continue

            # Messages are wrapped: {"ts": ..., "data": [["event", {...}], ...]}
            if not isinstance(msg, dict) or "data" not in msg:
                continue

            for item in msg["data"]:
                if not isinstance(item, list) or len(item) < 2:
                    continue

                if item[0] == "sync":
                    print(f"  Sync received. Processed {event_count} events.")
                    synced = True
                    break

                if item[0] != "event":
                    continue

                ev = item[1] if isinstance(item[1], dict) else None
                if not ev:
                    continue

                event_count += 1
                sport = ev.get("sport", "unknown")
                comp_id = str(ev.get("competition_id", ""))
                comp_name = ev.get("competition_name", "")
                comp_country = ev.get("competition_country", "")

                if not comp_id:
                    continue

                if sport not in competitions:
                    competitions[sport] = {}
                if comp_id not in competitions[sport]:
                    competitions[sport][comp_id] = {
                        "name": comp_name,
                        "country": comp_country,
                        "event_count": 0,
                    }
                competitions[sport][comp_id]["event_count"] += 1

    return competitions


async def discover(username: str, password: str) -> None:
    token = await login(username, password)
    competitions = await stream_competitions(token)

    # Print discovered competitions
    total_comps = sum(len(v) for v in competitions.values())
    print(f"\n  Discovered {total_comps} competitions across {len(competitions)} sports:")
    for sport in sorted(competitions):
        print(f"\n  [{sport}]")
        for comp_id, info in sorted(competitions[sport].items(), key=lambda x: -x[1]["event_count"]):
            print(f"    {comp_id}: {info['name']} ({info['country']}) — {info['event_count']} events")

    # Match against our seed file
    seed_data = json.loads(DATA_FILE.read_text())
    updated = 0

    for entry in seed_data:
        short_name = entry["sportshub_short_name"]
        mollybet_sport = entry["mollybet_sport"]
        keywords = [k.lower() for k in MATCH_KEYWORDS.get(short_name, [])]

        if not keywords:
            continue

        sport_comps = competitions.get(mollybet_sport, {})

        best_match = None
        best_events = 0
        for comp_id, info in sport_comps.items():
            comp_name_lower = info["name"].lower()
            if any(kw in comp_name_lower for kw in keywords):
                if info["event_count"] > best_events:
                    best_match = (comp_id, info["name"], info["country"])
                    best_events = info["event_count"]

        if best_match:
            comp_id, comp_name, country = best_match
            entry["mollybet_competition_id"] = comp_id
            if country:
                entry["country"] = country
            entry["notes"] = f"Discovered: {comp_name} ({best_events} events)"
            print(f"\n  {short_name} -> {comp_id} ({comp_name}, {best_events} events)")
            updated += 1
        else:
            print(f"\n  {short_name} -> NOT FOUND (sport={mollybet_sport}, keywords={keywords[:2]})")

    # Write back
    DATA_FILE.write_text(json.dumps(seed_data, indent=2, ensure_ascii=False) + "\n")
    print(f"\nDone. {updated}/{len(seed_data)} competitions mapped.")
    print(f"Updated: {DATA_FILE}")

    # Logout
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.delete(
            f"{API_URL}/v1/sessions/{token}/",
            headers={"Session": token},
        )


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
