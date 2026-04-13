"""Merge FBref club-season stats into FIFA World Cup player data.

Uses FBref player IDs already stored in fifa_wc_players.json (via Reep
enrichment) to scrape current season stats from FBref. Adds a nested
`club_stats_2025_26` dict with goals, assists, xG, xA, minutes, and
other performance metrics.

FBref is Cloudflare-protected, requiring browser automation (Chrome MCP)
or a scraping service. This script uses standard HTTP with retry logic --
for production use, integrate ScrapingBee/Zenrows.

Follows the merge_bref_advanced.py pattern: load existing data, fetch
from source, fuzzy-match, merge, save.

Requires: fifa_wc_players.json with FBref aliases from Reep enrichment.

Usage: python3 -m scripts.merge_fbref_stats
       python3 -m scripts.merge_fbref_stats --limit 100
       python3 -m scripts.merge_fbref_stats --team football-brazil
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data

FBREF_BASE = "https://fbref.com"
RATE_LIMIT_SECONDS = 3.5  # Cloudflare needs conservative pacing
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_fbref_page(player_id: str) -> str | None:
    """Fetch a FBref player page HTML.

    Note: FBref has Cloudflare protection. Standard HTTP may be blocked.
    For reliable access, use Chrome MCP or ScrapingBee.
    """
    time.sleep(RATE_LIMIT_SECONDS)
    url = f"{FBREF_BASE}/en/players/{player_id}/"
    try:
        req = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as e:
        return None


def safe_float(val: str | None, default: float | None = None) -> float | None:
    if val is None or val == "":
        return default
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return default


def safe_int(val: str | None, default: int | None = None) -> int | None:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def parse_standard_stats_table(html: str) -> dict | None:
    """Extract current season stats from FBref 'Standard Stats' table.

    FBref uses data-stat attributes on <td> elements. We look for the
    most recent season row in the standard stats table.
    """
    # Find the Standard Stats table
    stats_table = re.search(
        r'<table[^>]*id="stats_standard_dom_lg"[^>]*>.*?</table>',
        html, re.DOTALL,
    )
    if not stats_table:
        # Try combined table
        stats_table = re.search(
            r'<table[^>]*id="stats_standard_combined"[^>]*>.*?</table>',
            html, re.DOTALL,
        )
    if not stats_table:
        return None

    table_html = stats_table.group(0)

    # Find the most recent season row (2025-2026 or 2024-2025)
    # FBref rows: <tr><th data-stat="season">2025-2026</th>...
    season_patterns = ["2025-2026", "2024-2025", "2025"]
    target_row = None

    for season in season_patterns:
        pattern = rf'<tr[^>]*>.*?>{season}</(?:th|td)>.*?</tr>'
        match = re.search(pattern, table_html, re.DOTALL)
        if match:
            target_row = match.group(0)
            break

    if not target_row:
        # Fall back to last data row
        rows = re.findall(r'<tr[^>]*>(?:(?!</thead>).)*?</tr>', table_html, re.DOTALL)
        data_rows = [r for r in rows if 'data-stat="season"' in r and '<th' not in r[:50]]
        if data_rows:
            target_row = data_rows[-1]

    if not target_row:
        return None

    # Extract stats using data-stat attributes
    def extract_stat(stat_name: str) -> str | None:
        match = re.search(
            rf'data-stat="{stat_name}"[^>]*>([^<]*)</(?:td|th)>',
            target_row,
        )
        return match.group(1).strip() if match else None

    return {
        "source": "fbref.com",
        "appearances": safe_int(extract_stat("games")),
        "starts": safe_int(extract_stat("games_starts")),
        "minutes": safe_int(extract_stat("minutes")),
        "goals": safe_int(extract_stat("goals")),
        "assists": safe_int(extract_stat("assists")),
        "goals_assists": safe_int(extract_stat("goals_assists")),
        "non_penalty_goals": safe_int(extract_stat("goals_pens")),
        "penalty_goals": safe_int(extract_stat("pens_made")),
        "penalties_attempted": safe_int(extract_stat("pens_att")),
        "yellow_cards": safe_int(extract_stat("cards_yellow")),
        "red_cards": safe_int(extract_stat("cards_red")),
        "xg": safe_float(extract_stat("xg")),
        "npxg": safe_float(extract_stat("npxg")),
        "xa": safe_float(extract_stat("xg_assist")),
        "progressive_carries": safe_int(extract_stat("progressive_carries")),
        "progressive_passes": safe_int(extract_stat("progressive_passes")),
        "progressive_passes_received": safe_int(extract_stat("progressive_passes_received")),
    }


def get_fbref_id(player: dict) -> str | None:
    """Extract FBref player ID from aliases.

    Reep enrichment stores FBref IDs as:
      {"alias": "fbref:1e9bde92", "source_id": "reep_fbref"}
    We need just the hex ID part after "fbref:".
    """
    for alias in player.get("aliases", []):
        source = alias.get("source_id", "")
        alias_val = alias.get("alias", "")

        # Reep format: source_id="reep_fbref", alias="fbref:HEXID"
        if source == "reep_fbref" and alias_val.startswith("fbref:"):
            return alias_val.split(":", 1)[1]

        # Direct format: source_id="fbref", alias=ID
        if source == "fbref":
            return alias_val

    return None


def main():
    parser = argparse.ArgumentParser(description="Merge FBref stats into FIFA player data")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of players to scrape (0 = all)")
    parser.add_argument("--team", type=str,
                        help="Process only one team by team_id (e.g., football-brazil)")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip players who already have club_stats_2025_26")
    args = parser.parse_args()

    with log_script_run("merge_fbref_stats") as run:
        wc_players = load_data("fifa_wc_players.json")

        # Filter
        targets = wc_players
        if args.team:
            targets = [p for p in targets if p.get("team_id") == args.team]
        if args.skip_existing:
            targets = [p for p in targets
                       if not p.get("metadata", {}).get("club_stats_2025_26")]

        # Only process players with FBref IDs
        targets_with_ids = [(p, get_fbref_id(p)) for p in targets]
        targets_with_ids = [(p, fid) for p, fid in targets_with_ids if fid]

        if args.limit > 0:
            targets_with_ids = targets_with_ids[:args.limit]

        total = len(targets_with_ids)
        total_with_fbref = len([p for p in wc_players if get_fbref_id(p)])
        print(f"FBref IDs available: {total_with_fbref}/{len(wc_players)} players")
        print(f"Processing {total} players for club stats...")

        if total == 0:
            print("No players to process (all have stats or no FBref IDs)")
            run.summary = "No players to process"
            return

        enriched = 0
        blocked = 0
        errors = 0
        no_data = 0

        for i, (player, fbref_id) in enumerate(targets_with_ids):
            if i > 0 and i % 20 == 0:
                print(f"  [{i}/{total}] {enriched} enriched, {blocked} blocked, {errors} errors...")

            html = fetch_fbref_page(fbref_id)

            if not html:
                errors += 1
                continue

            # Detect Cloudflare block
            if "Just a moment" in html or "cf-browser-verification" in html:
                blocked += 1
                if blocked >= 5:
                    print(f"\n  Cloudflare is blocking requests. "
                          f"Stopping after {i} attempts.")
                    print("  For reliable FBref access, use Chrome MCP or ScrapingBee.")
                    break
                time.sleep(10)  # Back off on block
                continue

            stats = parse_standard_stats_table(html)

            if stats and any(v is not None for k, v in stats.items() if k != "source"):
                # Merge into player
                player.setdefault("metadata", {})["club_stats_2025_26"] = stats
                enriched += 1
            else:
                no_data += 1

        save_data("fifa_wc_players.json", wc_players)

        run.records_processed = enriched
        run.summary = (
            f"Merged FBref stats for {enriched}/{total} players "
            f"({blocked} blocked, {errors} errors, {no_data} no data)"
        )

        print(f"\n{'='*50}")
        print(f"Done: {enriched} players enriched with FBref club stats")
        print(f"  Cloudflare blocks: {blocked}")
        print(f"  HTTP errors: {errors}")
        print(f"  No stats found: {no_data}")

        # Show sample
        with_stats = [(p["name"], p["metadata"]["club_stats_2025_26"])
                      for p in wc_players
                      if p.get("metadata", {}).get("club_stats_2025_26")]
        if with_stats:
            print("\nSample enriched players:")
            for name, s in with_stats[:5]:
                goals = s.get("goals", "?")
                assists = s.get("assists", "?")
                apps = s.get("appearances", "?")
                xg = s.get("xg", "?")
                print(f"  {name}: {goals}G {assists}A in {apps} apps (xG: {xg})")

        if blocked > 0:
            print(f"\nNote: {blocked} requests were blocked by Cloudflare.")
            print("For complete FBref coverage, use Chrome MCP browser automation")
            print("or a managed scraping service (ScrapingBee, Zenrows).")

        print(f"\nWritten to fifa_wc_players.json")


if __name__ == "__main__":
    main()
