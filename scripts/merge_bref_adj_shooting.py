"""Merge Basketball Reference adjusted shooting stats into existing player stats.

Takes bref_adj_shooting_2026_raw.json (scraped from Basketball Reference) and
merges league-adjusted shooting metrics (FG+, TS+, etc.) and shooting value
added into data/nba_player_stats.json.

For players with multiple team entries (traded mid-season), uses the total
row (Team='TOT') or the entry with the most minutes played.

Usage: python3 -m scripts.merge_bref_adj_shooting
"""

import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.activity_log import log_script_run
from sportshub.scripts.io import load_data, save_data, data_path
from sportshub.scripts.normalization import normalize_player_name as normalize_name


BREF_FILE = data_path("bref_adj_shooting_2026_raw.json")


def build_bref_lookup(bref_data: list[dict]) -> dict[str, dict]:
    """Build a lookup of normalized player name -> best adjusted shooting entry."""
    from collections import defaultdict
    by_player: dict[str, list[dict]] = defaultdict(list)

    for row in bref_data:
        name = row.get("Player", "")
        if not name:
            continue
        by_player[name].append(row)

    lookup = {}
    for name, entries in by_player.items():
        if len(entries) == 1:
            lookup[normalize_name(name)] = entries[0]
        else:
            tot = [e for e in entries if e.get("Team") in ("TOT", "2TM", "3TM", "4TM")]
            if tot:
                lookup[normalize_name(name)] = tot[0]
            else:
                best = max(entries, key=lambda e: int(e.get("MP", 0) or 0))
                lookup[normalize_name(name)] = best

    return lookup


def safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def merge_adj_shooting(player_stat: dict, bref_row: dict) -> dict:
    """Merge Basketball Reference adjusted shooting stats into our player stats entry."""
    adj = {
        "adj_fg_pct": safe_float(bref_row.get("FG%")),
        "adj_2p_pct": safe_float(bref_row.get("2P%")),
        "adj_3p_pct": safe_float(bref_row.get("3P%")),
        "adj_efg_pct": safe_float(bref_row.get("eFG%")),
        "adj_ft_pct": safe_float(bref_row.get("FT%")),
        "adj_ts_pct": safe_float(bref_row.get("TS%")),
        "adj_ftr": safe_float(bref_row.get("FTr")),
        "adj_3par": safe_float(bref_row.get("3PAr")),
        "fg_plus": safe_float(bref_row.get("FG+")),
        "two_pt_plus": safe_float(bref_row.get("2P+")),
        "three_pt_plus": safe_float(bref_row.get("3P+")),
        "efg_plus": safe_float(bref_row.get("eFG+")),
        "ft_plus": safe_float(bref_row.get("FT+")),
        "ts_plus": safe_float(bref_row.get("TS+")),
        "ftr_plus": safe_float(bref_row.get("FTr+")),
        "three_par_plus": safe_float(bref_row.get("3PAr+")),
        "fg_points_added": safe_float(bref_row.get("FG Add")),
        "ts_points_added": safe_float(bref_row.get("TS Add")),
    }

    # Remove zero values that are actually missing
    adj = {k: v for k, v in adj.items() if v != 0.0}

    player_stat.setdefault("adjusted_shooting", {}).update(adj)
    return player_stat


def main():
    if not BREF_FILE.exists():
        print(f"Error: {BREF_FILE} not found. Run the browser scrape first.")
        return

    with log_script_run("merge_bref_adj_shooting") as run:
        with open(BREF_FILE) as f:
            bref_data = json.load(f)

        stats_data = load_data("nba_player_stats.json")

        players = stats_data.get("players", [])
        print(f"Basketball Reference adjusted shooting rows: {len(bref_data)}")
        print(f"Our player stats: {len(players)}")

        bref_lookup = build_bref_lookup(bref_data)
        print(f"Unique Basketball Reference players: {len(bref_lookup)}")

        matched = 0
        unmatched = []

        for player in players:
            name = player.get("player_name", "")
            norm_name = normalize_name(name)

            bref_row = bref_lookup.get(norm_name)

            if not bref_row:
                best_score = 0
                best_key = None
                for bref_name in bref_lookup:
                    score = SequenceMatcher(None, norm_name, bref_name).ratio()
                    if score > best_score and score > 0.85:
                        best_score = score
                        best_key = bref_name

                if best_key:
                    bref_row = bref_lookup[best_key]

            if bref_row:
                merge_adj_shooting(player, bref_row)
                matched += 1
            else:
                unmatched.append(name)

        stats_data["adj_shooting_source"] = "basketball-reference.com"
        stats_data["adj_shooting_season"] = "2025-26"

        save_data("nba_player_stats.json", stats_data)

        run.records_processed = matched
        run.summary = f"Merged adjusted shooting for {matched}/{len(players)} players"

        print(f"\nMatched: {matched}/{len(players)}")
        print(f"Unmatched: {len(unmatched)}")
        if unmatched[:10]:
            print(f"Sample unmatched: {unmatched[:10]}")

        # Show top 5 by TS+
        with_ts = [(p["player_name"], p["adjusted_shooting"]["ts_plus"])
                   for p in players if p.get("adjusted_shooting", {}).get("ts_plus")]
        with_ts.sort(key=lambda x: x[1], reverse=True)
        print("\nTop 5 by TS+ (league-adjusted true shooting):")
        for name, val in with_ts[:5]:
            print(f"  {name}: {val:.0f}")

        print(f"\nWritten to nba_player_stats.json")


if __name__ == "__main__":
    main()
