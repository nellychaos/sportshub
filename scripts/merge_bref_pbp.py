"""Merge Basketball Reference play-by-play stats into existing player stats.

Takes bref_pbp_2026_raw.json (scraped from Basketball Reference) and merges
position estimates, on/off court plus-minus, and turnover/foul breakdowns
into data/nba_player_stats.json.

For players with multiple team entries (traded mid-season), uses the total
row (Team='TOT') or the entry with the most minutes played.

Usage: python3 -m scripts.merge_bref_pbp
"""

import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sportshub.scripts.io import load_data, save_data, data_path
from sportshub.scripts.normalization import normalize_player_name as normalize_name


BREF_FILE = data_path("bref_pbp_2026_raw.json")


def build_bref_lookup(bref_data: list[dict]) -> dict[str, dict]:
    """Build a lookup of normalized player name -> best PBP stats entry."""
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


def safe_int(val: str, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def merge_pbp(player_stat: dict, bref_row: dict) -> dict:
    """Merge Basketball Reference play-by-play stats into our player stats entry."""
    pbp = {
        "position_estimate_pg_pct": safe_float(bref_row.get("PG%")),
        "position_estimate_sg_pct": safe_float(bref_row.get("SG%")),
        "position_estimate_sf_pct": safe_float(bref_row.get("SF%")),
        "position_estimate_pf_pct": safe_float(bref_row.get("PF%")),
        "position_estimate_c_pct": safe_float(bref_row.get("C%")),
        "on_court_plus_minus_per_100": safe_float(bref_row.get("OnCourt")),
        "on_off_plus_minus_per_100": safe_float(bref_row.get("On-Off")),
        "bad_pass_turnovers": safe_int(bref_row.get("BadPass")),
        "lost_ball_turnovers": safe_int(bref_row.get("LostBall")),
        "shooting_fouls_committed": safe_int(bref_row.get("ShootFoul")),
        "offensive_fouls_committed": safe_int(bref_row.get("OffFoul")),
        "shooting_fouls_drawn": safe_int(bref_row.get("ShootFoulDrawn")),
        "offensive_fouls_drawn": safe_int(bref_row.get("OffFoulDrawn")),
        "points_generated_by_assists": safe_int(bref_row.get("PGA")),
        "and_one_plays": safe_int(bref_row.get("And1")),
        "shots_blocked": safe_int(bref_row.get("Blkd")),
    }

    # Remove zero values that are actually missing (keep plus-minus zeros)
    keep_zero = {"on_court_plus_minus_per_100", "on_off_plus_minus_per_100"}
    pbp = {k: v for k, v in pbp.items() if v != 0 or k in keep_zero}

    player_stat.setdefault("play_by_play", {}).update(pbp)
    return player_stat


def main():
    if not BREF_FILE.exists():
        print(f"Error: {BREF_FILE} not found. Run the browser scrape first.")
        return

    with open(BREF_FILE) as f:
        bref_data = json.load(f)

    stats_data = load_data("nba_player_stats.json")

    players = stats_data.get("players", [])
    print(f"Basketball Reference PBP rows: {len(bref_data)}")
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
            merge_pbp(player, bref_row)
            matched += 1
        else:
            unmatched.append(name)

    stats_data["pbp_stats_source"] = "basketball-reference.com"
    stats_data["pbp_stats_season"] = "2025-26"

    save_data("nba_player_stats.json", stats_data)

    print(f"\nMatched: {matched}/{len(players)}")
    print(f"Unmatched: {len(unmatched)}")
    if unmatched[:10]:
        print(f"Sample unmatched: {unmatched[:10]}")

    # Show top 5 by on/off
    with_onoff = [(p["player_name"], p["play_by_play"]["on_off_plus_minus_per_100"])
                  for p in players if p.get("play_by_play", {}).get("on_off_plus_minus_per_100")]
    with_onoff.sort(key=lambda x: x[1], reverse=True)
    print("\nTop 5 by On-Off +/-:")
    for name, val in with_onoff[:5]:
        print(f"  {name}: {val:+.1f}")

    print(f"\nWritten to nba_player_stats.json")


if __name__ == "__main__":
    main()
