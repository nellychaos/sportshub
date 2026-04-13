"""Merge champion pool data from Liquipedia into LoL player stats.

Scrapes Liquipedia player pages to fill champion pool gaps left by
PandaScore free tier. Uses the MediaWiki API to fetch parsed HTML,
then extracts champion statistics from career tables.

Best-effort enrichment -- HTML parsing is inherently fragile. Missing
or unparseable data is logged and skipped.

Requires: lol_player_stats.json to be populated first.

Usage: python3 -m scripts.merge_liquipedia_lol_stats
       python3 -m scripts.merge_liquipedia_lol_stats --limit 50
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data
from sportshub.scripts.normalization import normalize_player_name

LIQUIPEDIA_API = "https://liquipedia.net/leagueoflegends/api.php"
RATE_LIMIT_SECONDS = 2.5  # Conservative -- Liquipedia enforces 1 req/2s
USER_AGENT = "Sportshub/0.1 (data enrichment; contact: admin@sportshub.dev)"


def fetch_page(page_title: str) -> str | None:
    """Fetch a Liquipedia page's parsed HTML via the MediaWiki API."""
    time.sleep(RATE_LIMIT_SECONDS)
    url = (f"{LIQUIPEDIA_API}?action=parse&page={quote(page_title)}"
           f"&prop=text&format=json&redirects=1")
    try:
        req = Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("parse", {}).get("text", {}).get("*", "")
    except (HTTPError, URLError, TimeoutError) as e:
        print(f"    Error fetching {page_title}: {e}")
        return None


def extract_champion_stats(html: str) -> list[dict]:
    """Extract champion pool stats from Liquipedia player page HTML.

    Looks for the "Champion Statistics" or "Tournament Results" tables.
    Returns a list of {champion, games, win_rate, kda} dicts.
    """
    champions = []

    # Look for champion stats in infobox or dedicated tables
    # Pattern: champion name in a cell, followed by games/win rate
    # Liquipedia format varies, so we try multiple patterns

    # Pattern 1: Champion stats table rows
    # <td>Champion Name</td><td>Games</td><td>W-L</td><td>Win%</td>
    table_pattern = re.compile(
        r'title="([^"]+)"[^>]*>(?:</a>)?\s*</td>\s*'
        r'<td[^>]*>(\d+)</td>\s*'  # games
        r'<td[^>]*>(\d+)\s*-\s*(\d+)</td>',  # W-L
        re.DOTALL,
    )

    for match in table_pattern.finditer(html):
        champ_name = match.group(1).strip()
        games = int(match.group(2))
        wins = int(match.group(3))
        losses = int(match.group(4))

        # Skip non-champion entries
        if games == 0 or len(champ_name) > 30:
            continue

        total = wins + losses
        win_rate = round(wins / total * 100, 1) if total > 0 else None

        champions.append({
            "champion": champ_name,
            "games": games,
            "win_rate": win_rate,
            "kda": None,  # Liquipedia doesn't consistently provide KDA per champion
        })

    # Pattern 2: Signature champions in infobox
    # <div class="infobox-cell-2">Signature Champions</div>
    sig_pattern = re.compile(
        r'(?:Signature|Main)\s*Champions?.*?'
        r'(?:<a[^>]*title="([^"]+)"[^>]*>[^<]*</a>[,\s]*)+',
        re.DOTALL | re.IGNORECASE,
    )

    sig_match = sig_pattern.search(html)
    if sig_match and not champions:
        # Extract champion names from links
        champ_links = re.findall(r'title="([^"]+)"', sig_match.group(0))
        for name in champ_links[:5]:
            if len(name) < 25 and not any(c["champion"] == name for c in champions):
                champions.append({
                    "champion": name,
                    "games": None,
                    "win_rate": None,
                    "kda": None,
                })

    return champions[:10]  # Cap at 10 champions


def ign_to_page_title(ign: str) -> str:
    """Convert an in-game name to a Liquipedia page title.

    Liquipedia uses the IGN directly as the page title for most players,
    with spaces replaced by underscores. Some players have disambiguation
    (e.g., "Faker (Lee Sang-hyeok)") but we try the simple form first.
    """
    return ign.replace(" ", "_")


def main():
    parser = argparse.ArgumentParser(
        description="Merge Liquipedia champion pool data into LoL player stats"
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of players to process (0 = all)")
    parser.add_argument("--only-missing", action="store_true", default=True,
                        help="Only fetch for players missing champion pool data")
    args = parser.parse_args()

    with log_script_run("merge_liquipedia_lol_stats") as run:
        stats_data = load_data("lol_player_stats.json")
        players = stats_data.get("players", [])

        if not players:
            print("Error: lol_player_stats.json has no players.")
            return

        # Filter to players needing champion pool data
        if args.only_missing:
            targets = [p for p in players if not p.get("champion_pool")]
        else:
            targets = list(players)

        if args.limit > 0:
            targets = targets[:args.limit]

        print(f"Processing {len(targets)} players for champion pool data...")

        enriched = 0
        skipped = 0
        errors = 0

        # Build name -> player index for merging results back
        player_index = {normalize_player_name(p["player_name"]): p for p in players}

        for i, player in enumerate(targets):
            ign = player.get("player_name", "")
            if not ign:
                skipped += 1
                continue

            if i > 0 and i % 10 == 0:
                print(f"  [{i}/{len(targets)}] {enriched} enriched, {errors} errors...")

            page_title = ign_to_page_title(ign)
            html = fetch_page(page_title)

            if not html:
                errors += 1
                continue

            # Check if page exists (vs redirect to search)
            if "There is currently no text in this page" in html:
                skipped += 1
                continue

            champions = extract_champion_stats(html)

            if champions:
                # Find the player in our data and update
                norm_ign = normalize_player_name(ign)
                target_player = player_index.get(norm_ign)
                if target_player:
                    target_player["champion_pool"] = champions
                    enriched += 1
                    print(f"  [OK] {ign}: {len(champions)} champions")
            else:
                skipped += 1

        # Update metadata
        stats_data["liquipedia_enrichment"] = {
            "enriched_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "players_enriched": enriched,
        }

        save_data("lol_player_stats.json", stats_data)

        run.records_processed = enriched
        run.summary = (
            f"Enriched champion pool for {enriched}/{len(targets)} players "
            f"({skipped} skipped, {errors} errors)"
        )

        print(f"\nDone: {enriched} players enriched with champion data")
        print(f"  Skipped: {skipped}")
        print(f"  Errors: {errors}")
        print(f"\nWritten to lol_player_stats.json")


if __name__ == "__main__":
    main()
