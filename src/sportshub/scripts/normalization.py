"""Canonical normalization functions for scripts.

Re-exports the application's normalize_team_name and provides
a unified normalize_player_name for player matching across providers.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_team_name(raw_name: str) -> str:
    """Reduce a team name to canonical form for matching.

    Identical to sportshub.resolution.normalizer.normalize_team_name.
    Re-implemented here to avoid pulling in async/DB dependencies in scripts.

    Steps:
    1. Lowercase and strip whitespace
    2. Remove accents (Unicode NFKD decomposition)
    3. Remove common suffixes (esports, gaming, team, club, fc, sc)
    4. Remove punctuation
    5. Normalize whitespace
    """
    name = raw_name.strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(
        r"\b(esports?|gaming|team|club|fc|sc|national football team|national team|men'?s)\b",
        "",
        name,
    )
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_player_name(raw_name: str) -> str:
    """Normalize a player name for cross-provider matching.

    Steps:
    1. Unicode NFKD decomposition, strip combining marks (accents)
    2. Lowercase
    3. Remove punctuation (hyphens, apostrophes, periods, etc.)
    4. Normalize whitespace

    Examples:
        "Nikola Jokić"  -> "nikola jokic"
        "Shai Gilgeous-Alexander" -> "shai gilgeousalexander"
        "P.J. Washington" -> "pj washington"
        "Adama-Alpha Bal" -> "adamaalpha bal"
    """
    name = unicodedata.normalize("NFKD", raw_name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.strip().lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_alias(raw_alias: str) -> str:
    """Normalize an alias string for database insertion.

    Used by seed scripts. Equivalent to normalize_team_name but
    kept as a distinct entry point for clarity.
    """
    return normalize_team_name(raw_alias)
