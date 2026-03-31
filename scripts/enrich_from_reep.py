"""Enrich FIFA WC 2026 players with cross-provider aliases from Reep.

Downloads Reep CSV data (football entity register with 430K+ players, 45K+ teams)
and fuzzy-matches against our player data. Adds provider-specific aliases
(transfermarkt, fbref, sofascore, espn, opta, etc.) to each player.

Note: Reep's teams.csv contains clubs only — no national teams. Team enrichment
is skipped. Player enrichment is the primary value: cross-provider IDs + name aliases.

Can run in two modes:
  --offline   Enrich the JSON data files directly (no database needed)
  --db        Enrich via database (inserts aliases into player_aliases)

Usage:
  python -m scripts.enrich_from_reep                # offline mode (default)
  python -m scripts.enrich_from_reep --db            # database mode
  python -m scripts.enrich_from_reep --download-only  # just download CSVs
"""

import argparse
import asyncio
import csv
import json
import re
import unicodedata
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REEP_DIR = DATA_DIR / "reep"

GITHUB_RAW = "https://raw.githubusercontent.com/withqwerty/reep/main/data"
REEP_FILES = ["people.csv", "teams.csv", "names.csv", "meta.json"]

# Demonym -> country mapping for nationality comparison.
# Our data uses demonyms ("French"), Reep uses country names ("France").
NATIONALITY_MAP = {
    "french": "france", "german": "germany", "danish": "denmark",
    "dutch": "netherlands", "spanish": "spain", "portuguese": "portugal",
    "brazilian": "brazil", "argentine": "argentina", "mexican": "mexico",
    "japanese": "japan", "south korean": "south korea", "chinese": "china",
    "turkish": "turkey", "moroccan": "morocco", "senegalese": "senegal",
    "cameroonian": "cameroon", "nigerian": "nigeria", "egyptian": "egypt",
    "jamaican": "jamaica", "honduran": "honduras", "ecuadorian": "ecuador",
    "colombian": "colombia", "uruguayan": "uruguay", "chilean": "chile",
    "peruvian": "peru", "paraguayan": "paraguay", "bolivian": "bolivia",
    "panamanian": "panama", "costa rican": "costa rica",
    "saudi": "saudi arabia", "bahraini": "bahrain", "iranian": "iran",
    "belgian": "belgium", "italian": "italy", "croatian": "croatia",
    "polish": "poland", "albanian": "albania", "icelandic": "iceland",
    "uzbek": "uzbekistan", "kenyan": "kenya", "guatemalan": "guatemala",
    "congolese": "congo", "south african": "south africa",
    "new zealander": "new zealand", "australian": "australia",
    "english": "england", "scottish": "scotland", "welsh": "wales",
    "serbian": "serbia", "swiss": "switzerland", "austrian": "austria",
    "norwegian": "norway", "swedish": "sweden", "finnish": "finland",
}

# Provider columns we care about — these become source_id values in our alias table.
# Prefix "reep_" to avoid collisions with our existing source_ids.
PLAYER_PROVIDERS = [
    "transfermarkt", "fbref", "soccerway", "sofascore", "flashscore", "opta",
    "premier_league", "11v11", "espn", "national_football_teams", "worldfootball",
    "soccerbase", "kicker", "uefa", "lequipe", "serie_a", "besoccer",
    "footballdatabase_eu", "eu_football_info", "understat", "whoscored",
    "sportmonks", "api_football", "fotmob",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    """Normalize a name for fuzzy comparison."""
    name = name.strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(
        r"\b(esports?|gaming|team|club|fc|sc|national football team|national team|men'?s)\b",
        "", name,
    )
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def similarity(a: str, b: str) -> float:
    """Normalized similarity score between two strings."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def download_reep_csvs(force: bool = False) -> None:
    """Download Reep CSV files from GitHub."""
    REEP_DIR.mkdir(parents=True, exist_ok=True)

    for filename in REEP_FILES:
        out_path = REEP_DIR / filename
        if out_path.exists() and not force:
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  {filename} already exists ({size_mb:.1f} MB), skipping (use --force to re-download)")
            continue

        url = f"{GITHUB_RAW}/{filename}"
        print(f"  Downloading {filename}...", end=" ", flush=True)
        try:
            urllib.request.urlretrieve(url, out_path)
            size = out_path.stat().st_size
            label = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.0f} KB"
            print(f"OK ({label})")
        except Exception as e:
            print(f"FAILED: {e}")

    meta_path = REEP_DIR / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        print(f"  Reep data generated: {meta.get('generated_at', '?')}")
        counts = meta.get("counts", {})
        print(f"  People: {counts.get('people', '?')}, Teams: {counts.get('teams', '?')}, Aliases: {counts.get('aliases', '?')}")


# ---------------------------------------------------------------------------
# CSV Loading
# ---------------------------------------------------------------------------

def load_reep_people() -> list[dict]:
    """Load Reep people.csv into list of dicts."""
    path = REEP_DIR / "people.csv"
    if not path.exists():
        raise FileNotFoundError(f"Reep people.csv not found at {path}. Run with --download-only first.")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_reep_names() -> dict[str, list[str]]:
    """Load Reep names.csv into a dict of QID -> list of aliases."""
    path = REEP_DIR / "names.csv"
    if not path.exists():
        return {}
    aliases: dict[str, list[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            qid = row.get("key_wikidata", "")
            alias = row.get("alias", "").strip()
            if qid and alias:
                aliases.setdefault(qid, []).append(alias)
    return aliases


# ---------------------------------------------------------------------------
# Name Index (for fast player matching against 430K records)
# ---------------------------------------------------------------------------

class ReepPlayerIndex:
    """Indexed lookup for Reep people by normalized name tokens.

    Instead of O(n*m) brute-force, we index every Reep person by their
    normalized name tokens and retrieve candidates via token overlap.
    """

    def __init__(self, reep_people: list[dict], reep_names: dict[str, list[str]]):
        self._people = reep_people
        self._reep_names = reep_names
        # Map: normalized_token -> set of indices into self._people
        self._token_index: dict[str, set[int]] = {}
        # Map: index -> list of all names for this person
        self._all_names: dict[int, list[str]] = {}

        # Reverse index for O(1) lookup in get_all_names()
        self._id_to_idx: dict[int, int] = {}

        print("    Building name index...", end=" ", flush=True)
        for idx, person in enumerate(reep_people):
            self._id_to_idx[id(person)] = idx
            names = []
            name = person.get("name", "").strip()
            full_name = person.get("full_name", "").strip()
            if name:
                names.append(name)
            if full_name and full_name != name:
                names.append(full_name)
            qid = person.get("key_wikidata", "")
            if qid and qid in reep_names:
                names.extend(reep_names[qid])

            self._all_names[idx] = names

            # Index by tokens of each name
            seen_tokens: set[str] = set()
            for n in names:
                for token in normalize(n).split():
                    if len(token) >= 3 and token not in seen_tokens:
                        seen_tokens.add(token)
                        self._token_index.setdefault(token, set()).add(idx)

        print(f"indexed {len(reep_people)} people, {len(self._token_index)} tokens")

    def find_candidates(self, name: str, max_candidates: int = 50) -> list[dict]:
        """Return candidate Reep people that share name tokens with the query."""
        tokens = normalize(name).split()
        if not tokens:
            return []

        # Count how many query tokens each candidate shares
        candidate_hits: dict[int, int] = {}
        for token in tokens:
            if token in self._token_index:
                for idx in self._token_index[token]:
                    candidate_hits[idx] = candidate_hits.get(idx, 0) + 1

        # Sort by most token overlap
        ranked = sorted(candidate_hits.items(), key=lambda x: -x[1])[:max_candidates]
        return [self._people[idx] for idx, _ in ranked]

    def get_all_names(self, person: dict) -> list[str]:
        """Get all names for a Reep person (used during scoring)."""
        idx = self._id_to_idx.get(id(person), -1)
        if idx >= 0 and idx in self._all_names:
            return self._all_names[idx]
        # Fallback
        names = []
        name = person.get("name", "").strip()
        full_name = person.get("full_name", "").strip()
        if name:
            names.append(name)
        if full_name:
            names.append(full_name)
        return names


def match_player(
    our_player: dict,
    index: ReepPlayerIndex,
    our_team_name: str,
) -> dict | None:
    """Find the best Reep person match for one of our players using the index."""
    our_name = our_player["name"]
    our_dob = our_player.get("metadata", {}).get("date_of_birth", "")
    our_nationality = our_player.get("nationality", "")
    our_position = our_player.get("position", "")
    our_aliases = [a.get("alias", "") for a in our_player.get("aliases", []) if a.get("alias")]
    our_names = list({our_name} | set(our_aliases))

    # Get candidates from the token index
    candidates = index.find_candidates(our_name, max_candidates=100)

    best_match = None
    best_score = 0.0

    for rp in candidates:
        rp_type = rp.get("type", "")
        rp_dob = rp.get("date_of_birth", "")
        rp_nationality = rp.get("nationality", "")

        # Skip coaches when matching players
        if rp_type == "coach" and our_position in ("GK", "DEF", "MID", "FWD"):
            continue

        # Get all Reep names for this person
        reep_all_names = index.get_all_names(rp)

        # Name similarity: best pairwise score
        name_score = 0.0
        for on in our_names:
            for rn in reep_all_names:
                s = similarity(on, rn)
                if s > name_score:
                    name_score = s

        if name_score < 0.75:
            continue

        # Boost for matching DOB (very strong disambiguator)
        dob_boost = 0.0
        if our_dob and rp_dob and our_dob == rp_dob:
            dob_boost = 0.15

        # Boost for matching nationality (demonym-aware)
        nat_boost = 0.0
        if our_nationality and rp_nationality:
            our_nat_norm = normalize(our_nationality)
            rp_nat_norm = normalize(rp_nationality)
            our_nat_country = NATIONALITY_MAP.get(our_nat_norm, our_nat_norm)
            rp_nat_country = NATIONALITY_MAP.get(rp_nat_norm, rp_nat_norm)
            if our_nat_country == rp_nat_country or our_nat_norm == rp_nat_norm:
                nat_boost = 0.05

        total = name_score + dob_boost + nat_boost

        # Provider count tiebreaker: prefer well-known players (many provider IDs)
        # over Wikidata stubs (0 provider IDs) when scores are nearly equal
        rp_provider_count = sum(1 for k, v in rp.items() if k.startswith("key_") and v and k != "key_wikidata")
        if abs(total - best_score) < 0.01 and best_match is not None:
            best_provider_count = sum(1 for k, v in best_match.items() if k.startswith("key_") and v and k != "key_wikidata")
            if rp_provider_count > best_provider_count:
                best_score = total
                best_match = rp
        elif total > best_score:
            best_score = total
            best_match = rp

    if best_score >= 0.85:
        return best_match
    return None


# ---------------------------------------------------------------------------
# Enrichment — extract aliases from Reep match
# ---------------------------------------------------------------------------

def extract_player_aliases(reep_person: dict, reep_names: dict[str, list[str]]) -> list[dict]:
    """Extract provider ID aliases from a matched Reep person."""
    aliases = []
    qid = reep_person.get("key_wikidata", "")

    # Provider IDs
    for provider in PLAYER_PROVIDERS:
        key = f"key_{provider}"
        pid = reep_person.get(key, "").strip()
        if pid:
            aliases.append({
                "alias": f"{provider}:{pid}",
                "source_id": f"reep_{provider}",
            })

    # Wikidata QID
    if qid:
        aliases.append({"alias": qid, "source_id": "reep_wikidata"})

    # Name aliases from names.csv
    if qid and qid in reep_names:
        for name_alias in reep_names[qid]:
            aliases.append({"alias": name_alias, "source_id": "reep_alias"})

    # Reep canonical name + full name
    for field in ("name", "full_name"):
        val = reep_person.get(field, "").strip()
        if val:
            aliases.append({"alias": val, "source_id": "reep"})

    return aliases


# ---------------------------------------------------------------------------
# Offline mode: enrich JSON data files
# ---------------------------------------------------------------------------

def enrich_players_offline() -> dict:
    """Enrich fifa_wc_players.json with Reep aliases. Returns stats."""
    players_path = DATA_DIR / "fifa_wc_players.json"
    teams_path = DATA_DIR / "fifa_wc_teams.json"

    with open(players_path, encoding="utf-8") as f:
        players = json.load(f)
    with open(teams_path, encoding="utf-8") as f:
        teams = json.load(f)

    # Build team_id -> team_name lookup
    team_names = {t["id"]: t["name"] for t in teams}

    reep_people = load_reep_people()
    reep_names = load_reep_names()
    print(f"  Loaded {len(reep_people)} Reep people, {len(reep_names)} name entries")

    # Pre-filter to players only (skip coaches for speed)
    reep_players = [p for p in reep_people if p.get("type") == "player"]
    print(f"  Filtered to {len(reep_players)} Reep players (excluding coaches)")

    # Build indexed lookup
    index = ReepPlayerIndex(reep_players, reep_names)

    # Group our players by team for progress reporting
    teams_done = set()
    matched = 0
    aliases_added = 0
    unmatched = []

    for i, player in enumerate(players):
        team_id = player.get("team_id", "")
        team_name = team_names.get(team_id, "Unknown")

        if team_id not in teams_done:
            teams_done.add(team_id)
            print(f"    Processing {team_name}... ({i}/{len(players)})")

        reep_match = match_player(player, index, team_name)
        if not reep_match:
            unmatched.append(f"{player['name']} ({team_name})")
            continue

        matched += 1
        new_aliases = extract_player_aliases(reep_match, reep_names)

        # Deduplicate
        existing_keys = {
            (normalize(a["alias"]), a.get("source_id") or "global")
            for a in player.get("aliases", [])
        }

        for alias in new_aliases:
            key = (normalize(alias["alias"]), alias["source_id"])
            if key not in existing_keys:
                player.setdefault("aliases", []).append(alias)
                existing_keys.add(key)
                aliases_added += 1

        # Store Reep metadata
        player.setdefault("metadata", {})["reep_qid"] = reep_match.get("key_wikidata", "")

    # Write back
    with open(players_path, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return {"matched": matched, "aliases_added": aliases_added, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# Database mode: insert aliases directly
# ---------------------------------------------------------------------------

async def enrich_players_db() -> dict:
    """Enrich player aliases in the database from Reep data."""
    from sportshub.db.engine import init_db, get_session_factory, close_db
    from sportshub.db.repositories import PlayerRepository
    from sportshub.models import Sport, PlayerAlias

    await init_db()
    factory = get_session_factory()

    reep_people = load_reep_people()
    reep_names = load_reep_names()
    reep_players = [p for p in reep_people if p.get("type") == "player"]
    index = ReepPlayerIndex(reep_players, reep_names)

    players_path = DATA_DIR / "fifa_wc_players.json"
    teams_path = DATA_DIR / "fifa_wc_teams.json"

    with open(players_path, encoding="utf-8") as f:
        our_players = json.load(f)
    with open(teams_path, encoding="utf-8") as f:
        teams = json.load(f)

    team_names = {t["id"]: t["name"] for t in teams}

    matched = 0
    aliases_added = 0
    unmatched = []

    async with factory() as session:
        repo = PlayerRepository(session)

        for our_player in our_players:
            team_name = team_names.get(our_player.get("team_id", ""), "")

            # Find DB player by name + sport
            db_player = await repo.find_by_name(
                name=our_player["name"], sport=Sport.FOOTBALL,
            )
            if not db_player:
                continue

            reep_match = match_player(our_player, index, team_name)
            if not reep_match:
                unmatched.append(f"{our_player['name']} ({team_name})")
                continue

            matched += 1
            new_aliases = extract_player_aliases(reep_match, reep_names)

            for alias_data in new_aliases:
                alias = PlayerAlias(
                    player_id=db_player.id,
                    alias=alias_data["alias"],
                    alias_normalized=normalize(alias_data["alias"]),
                    source_id=alias_data["source_id"],
                )
                try:
                    async with session.begin_nested():
                        await repo.add_alias(alias)
                    aliases_added += 1
                except Exception:
                    pass

        await session.commit()

    await close_db()
    return {"matched": matched, "aliases_added": aliases_added, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Enrich Sportshub football data with Reep cross-provider aliases",
    )
    parser.add_argument("--db", action="store_true", help="Insert aliases into database instead of JSON files")
    parser.add_argument("--download-only", action="store_true", help="Only download Reep CSVs, don't enrich")
    parser.add_argument("--force", action="store_true", help="Force re-download of Reep CSVs")
    args = parser.parse_args()

    print("=== Reep Player Enrichment ===\n")
    print("Note: Reep covers clubs only, not national teams. Enriching players only.\n")

    # Step 1: Download
    print("[1/2] Downloading Reep CSV data...")
    download_reep_csvs(force=args.force)
    print()

    if args.download_only:
        print("Done (download only).")
        return

    if args.db:
        print("[2/2] Enriching players in database...")
        stats = asyncio.run(enrich_players_db())
    else:
        print("[2/2] Enriching players in fifa_wc_players.json...")
        stats = enrich_players_offline()

    print(f"  Players matched: {stats['matched']}")
    print(f"  Aliases added: {stats['aliases_added']}")
    if stats["unmatched"]:
        print(f"  Unmatched ({len(stats['unmatched'])}): {', '.join(stats['unmatched'][:20])}")
        if len(stats["unmatched"]) > 20:
            print(f"  ... and {len(stats['unmatched']) - 20} more")

    print("\nDone.")


if __name__ == "__main__":
    main()
