"""Merge Basketball Reference advanced stats into existing player stats.

Takes the raw bref_advanced_2026_raw.json (scraped from Basketball Reference)
and merges PER, TS%, WS, BPM, VORP, USG%, and other advanced metrics into
data/nba_player_stats.json.

For players with multiple team entries (traded mid-season), uses the total
row (Team='TOT') or the entry with the most minutes played.

Usage: python3 -m scripts.merge_bref_advanced
"""

import json
from difflib import SequenceMatcher
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BREF_FILE = DATA_DIR / "bref_advanced_2026_raw.json"
STATS_FILE = DATA_DIR / "nba_player_stats.json"

# Basketball Reference team abbreviations -> our team_id mapping
BREF_TEAM_MAP = {
    "ATL": "nba-atl", "BOS": "nba-bos", "BRK": "nba-bkn", "CHO": "nba-cha",
    "CHI": "nba-chi", "CLE": "nba-cle", "DAL": "nba-dal", "DEN": "nba-den",
    "DET": "nba-det", "GSW": "nba-gsw", "HOU": "nba-hou", "IND": "nba-ind",
    "LAC": "nba-lac", "LAL": "nba-lal", "MEM": "nba-mem", "MIA": "nba-mia",
    "MIL": "nba-mil", "MIN": "nba-min", "NOP": "nba-nop", "NYK": "nba-nyk",
    "OKC": "nba-okc", "ORL": "nba-orl", "PHI": "nba-phi", "PHO": "nba-phx",
    "POR": "nba-por", "SAC": "nba-sac", "SAS": "nba-sas", "TOR": "nba-tor",
    "UTA": "nba-uta", "WAS": "nba-was",
    # Traded players have 'TOT' for combined stats
    "TOT": None,
    # Handle possible alternate abbreviations
    "PHX": "nba-phx", "BKN": "nba-bkn", "CHA": "nba-cha",
    "2TM": None, "3TM": None, "4TM": None,
}


def normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching."""
    import unicodedata
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return name.lower().strip().replace(".", "").replace("'", "").replace("-", " ")


def build_bref_lookup(bref_data: list[dict]) -> dict[str, dict]:
    """Build a lookup of player name -> best advanced stats entry.

    For players with multiple entries (traded), prefer 'TOT' row or
    the entry with most minutes.
    """
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
            # Multiple entries: prefer TOT (total across teams), else most MP
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


def merge_advanced(player_stat: dict, bref_row: dict) -> dict:
    """Merge Basketball Reference advanced stats into our player stats entry."""
    adv = {
        "player_efficiency_rating": safe_float(bref_row.get("PER")),
        "true_shooting_pct": safe_float(bref_row.get("TS%")),
        "three_point_attempt_rate": safe_float(bref_row.get("3PAr")),
        "free_throw_rate": safe_float(bref_row.get("FTr")),
        "offensive_rebound_pct": safe_float(bref_row.get("ORB%")),
        "defensive_rebound_pct": safe_float(bref_row.get("DRB%")),
        "total_rebound_pct": safe_float(bref_row.get("TRB%")),
        "assist_pct": safe_float(bref_row.get("AST%")),
        "steal_pct": safe_float(bref_row.get("STL%")),
        "block_pct": safe_float(bref_row.get("BLK%")),
        "turnover_pct": safe_float(bref_row.get("TOV%")),
        "usage_rate": safe_float(bref_row.get("USG%")),
        "offensive_win_shares": safe_float(bref_row.get("OWS")),
        "defensive_win_shares": safe_float(bref_row.get("DWS")),
        "win_shares": safe_float(bref_row.get("WS")),
        "win_shares_per_48": safe_float(bref_row.get("WS/48")),
        "offensive_box_plus_minus": safe_float(bref_row.get("OBPM")),
        "defensive_box_plus_minus": safe_float(bref_row.get("DBPM")),
        "box_plus_minus": safe_float(bref_row.get("BPM")),
        "value_over_replacement": safe_float(bref_row.get("VORP")),
    }

    # Remove zero values that are actually missing
    adv = {k: v for k, v in adv.items() if v != 0.0 or k in ("box_plus_minus", "offensive_box_plus_minus", "defensive_box_plus_minus")}

    player_stat.setdefault("advanced", {}).update(adv)
    return player_stat


def main():
    if not BREF_FILE.exists():
        print(f"Error: {BREF_FILE} not found. Run the browser scrape first.")
        return

    with open(BREF_FILE) as f:
        bref_data = json.load(f)

    with open(STATS_FILE) as f:
        stats_data = json.load(f)

    players = stats_data.get("players", [])
    print(f"Basketball Reference rows: {len(bref_data)}")
    print(f"Our player stats: {len(players)}")

    # Build lookup
    bref_lookup = build_bref_lookup(bref_data)
    print(f"Unique Basketball Reference players: {len(bref_lookup)}")

    # Match and merge
    matched = 0
    unmatched = []

    for player in players:
        name = player.get("player_name", "")
        norm_name = normalize_name(name)

        # Direct match
        bref_row = bref_lookup.get(norm_name)

        if not bref_row:
            # Fuzzy match: try closest name
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
            merge_advanced(player, bref_row)
            matched += 1
        else:
            unmatched.append(name)

    # Update source info
    stats_data["advanced_stats_source"] = "basketball-reference.com"
    stats_data["advanced_stats_season"] = "2025-26"

    with open(STATS_FILE, "w") as f:
        json.dump(stats_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nMatched: {matched}/{len(players)}")
    print(f"Unmatched: {len(unmatched)}")
    if unmatched[:10]:
        print(f"Sample unmatched: {unmatched[:10]}")

    # Show top 5 by PER
    with_per = [(p["player_name"], p["advanced"]["player_efficiency_rating"])
                for p in players if p.get("advanced", {}).get("player_efficiency_rating")]
    with_per.sort(key=lambda x: x[1], reverse=True)
    print("\nTop 5 by PER:")
    for name, per in with_per[:5]:
        print(f"  {name}: {per}")

    print(f"\nWritten to {STATS_FILE.name}")


if __name__ == "__main__":
    main()
