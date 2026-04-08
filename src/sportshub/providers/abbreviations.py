"""Convenience functions for common abbreviation translations.

These are thin wrappers around ProviderRegistry.translate_abbreviation()
providing a simpler API for the most common use case: converting between
our canonical NBA abbreviations and provider-specific ones.

Usage::

    from sportshub.providers.abbreviations import espn_to_standard, standard_to_espn

    espn_to_standard("GS")   # -> "GSW"
    standard_to_espn("GSW")  # -> "GS"
"""

from __future__ import annotations

from functools import lru_cache

from sportshub.providers.registry import ProviderRegistry


@lru_cache(maxsize=1)
def _get_registry() -> ProviderRegistry:
    """Lazily load the singleton registry."""
    return ProviderRegistry()


def espn_to_standard(abbr: str) -> str:
    """Convert an ESPN abbreviation to our canonical format.

    e.g., "GS" -> "GSW", "NO" -> "NOP". Passthrough if no mapping needed.
    """
    return _get_registry().translate_abbreviation(abbr, from_source="espn_nba")


def standard_to_espn(abbr: str) -> str:
    """Convert our canonical abbreviation to ESPN format.

    e.g., "GSW" -> "GS", "NOP" -> "NO". Passthrough if no mapping needed.
    """
    return _get_registry().translate_abbreviation(abbr, from_source="espn_nba", to_source="espn_nba")


def nbacom_to_standard(tricode: str) -> str:
    """Convert an NBA.com CDN tricode to our canonical format.

    e.g., "GS" -> "GSW", "UTAH" -> "UTA". Passthrough if no mapping needed.
    """
    return _get_registry().translate_abbreviation(tricode, from_source="nbacom_cdn")


def standard_to_nbacom(abbr: str) -> str:
    """Convert our canonical abbreviation to NBA.com CDN tricode.

    e.g., "GSW" -> "GS", "UTA" -> "UTAH". Passthrough if no mapping needed.
    """
    return _get_registry().translate_abbreviation(abbr, from_source="nbacom_cdn", to_source="nbacom_cdn")


def bref_to_standard(abbr: str) -> str:
    """Convert a Basketball Reference abbreviation to our canonical format.

    e.g., "BRK" -> "BKN", "CHO" -> "CHA", "PHO" -> "PHX". Passthrough if no mapping needed.
    """
    return _get_registry().translate_abbreviation(abbr, from_source="bref")


def standard_to_bref(abbr: str) -> str:
    """Convert our canonical abbreviation to Basketball Reference format.

    e.g., "BKN" -> "BRK", "CHA" -> "CHO", "PHX" -> "PHO". Passthrough if no mapping needed.
    """
    return _get_registry().translate_abbreviation(abbr, from_source="bref", to_source="bref")
