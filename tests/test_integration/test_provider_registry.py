"""Integration tests for the provider registry.

Validates that providers.json and provider_id_mappings.json load correctly,
that abbreviation maps are consistent, and that cross-provider lookups work
end-to-end.
"""

import pytest

from sportshub.providers.registry import ProviderRegistry
from sportshub.providers.abbreviations import (
    bref_to_standard,
    espn_to_standard,
    nbacom_to_standard,
    standard_to_bref,
    standard_to_espn,
    standard_to_nbacom,
)


@pytest.fixture(scope="module")
def registry():
    return ProviderRegistry()


class TestProviderRegistryLoading:
    """Verify the registry loads and indexes all expected data."""

    def test_loads_all_14_providers(self, registry):
        providers = registry.get_all_providers()
        assert len(providers) == 14

    def test_all_source_ids_unique(self, registry):
        ids = [p.source_id for p in registry.get_all_providers()]
        assert len(ids) == len(set(ids))

    def test_nba_providers(self, registry):
        nba = registry.get_providers_by_sport("nba")
        nba_ids = {p.source_id for p in nba}
        assert "espn_nba" in nba_ids
        assert "nbacom_cdn" in nba_ids
        assert "balldontlie_nba" in nba_ids
        assert "bref" in nba_ids

    def test_football_providers(self, registry):
        fb = registry.get_providers_by_sport("football")
        assert len(fb) >= 4  # espn_fifa, fifa_api, footballdata_wc, reep, mollybet_fb

    def test_lol_providers(self, registry):
        lol = registry.get_providers_by_sport("lol")
        assert len(lol) >= 3

    def test_espn_provider_has_required_fields(self, registry):
        espn = registry.get_provider("espn_nba")
        assert espn is not None
        assert espn.base_url == "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
        assert espn.rate_limit_seconds == 5.0
        assert espn.auth_required is False
        assert espn.reliability == "established"

    def test_nbacom_requires_special_headers(self, registry):
        cdn = registry.get_provider("nbacom_cdn")
        assert cdn is not None
        assert "Referer" in cdn.headers_required


class TestAbbreviationMaps:
    """Verify abbreviation translations are bidirectional and consistent."""

    # The 6 teams where ESPN abbreviations differ from standard
    ESPN_DIFFS = {
        "GSW": "GS", "NOP": "NO", "NYK": "NY",
        "SAS": "SA", "UTA": "UTAH", "WAS": "WSH",
    }

    def test_espn_to_standard_known_diffs(self):
        for standard, espn in self.ESPN_DIFFS.items():
            assert espn_to_standard(espn) == standard

    def test_standard_to_espn_known_diffs(self):
        for standard, espn in self.ESPN_DIFFS.items():
            assert standard_to_espn(standard) == espn

    def test_espn_roundtrip(self):
        """standard -> ESPN -> standard should be identity."""
        for standard, espn in self.ESPN_DIFFS.items():
            assert espn_to_standard(standard_to_espn(standard)) == standard

    def test_passthrough_for_matching_abbrevs(self):
        """Teams with matching abbreviations should pass through unchanged."""
        for abbr in ["BOS", "LAL", "CHI", "MIA", "DEN", "PHX", "OKC"]:
            assert espn_to_standard(abbr) == abbr
            assert standard_to_espn(abbr) == abbr

    def test_nbacom_to_standard(self):
        assert nbacom_to_standard("GS") == "GSW"
        assert nbacom_to_standard("UTAH") == "UTA"
        assert nbacom_to_standard("WSH") == "WAS"
        assert nbacom_to_standard("BOS") == "BOS"

    def test_bref_to_standard(self):
        assert bref_to_standard("BRK") == "BKN"
        assert bref_to_standard("CHO") == "CHA"
        assert bref_to_standard("PHO") == "PHX"
        assert bref_to_standard("BOS") == "BOS"

    def test_bref_roundtrip(self):
        for bref, standard in [("BRK", "BKN"), ("CHO", "CHA"), ("PHO", "PHX")]:
            assert bref_to_standard(standard_to_bref(standard)) == standard


class TestEndpointBuilding:
    """Verify endpoint URL construction from provider config."""

    def test_espn_roster_endpoint(self, registry):
        url = registry.get_endpoint("espn_nba", "roster", team_id="9")
        assert url == "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/9/roster"

    def test_espn_player_stats_absolute_url(self, registry):
        """Player stats endpoint is an absolute URL, not relative."""
        url = registry.get_endpoint("espn_nba", "player_stats", player_id="4065648")
        assert url.startswith("https://site.web.api.espn.com")
        assert "4065648" in url

    def test_bref_advanced_endpoint(self, registry):
        url = registry.get_endpoint("bref", "advanced", year="2026")
        assert url == "https://www.basketball-reference.com/leagues/NBA_2026_advanced.html"

    def test_nbacom_schedule_endpoint(self, registry):
        url = registry.get_endpoint("nbacom_cdn", "schedule")
        assert "scheduleLeagueV2.json" in url

    def test_unknown_endpoint_returns_none(self, registry):
        url = registry.get_endpoint("espn_nba", "nonexistent")
        assert url is None

    def test_unknown_provider_returns_none(self, registry):
        url = registry.get_endpoint("nonexistent_provider", "teams")
        assert url is None


class TestCrossProviderIdMappings:
    """Verify cross-provider ID lookups."""

    def test_all_30_nba_teams_mapped(self, registry):
        # Try looking up all teams
        found = 0
        for i in range(1, 31):
            mapping = registry.get_canonical_id_by_provider(str(i), "espn_nba")
            if mapping:
                found += 1
        assert found == 30

    def test_espn_id_for_boston(self, registry):
        assert registry.get_provider_entity_id("nba-bos", "espn_nba") == "2"

    def test_espn_id_for_gsw(self, registry):
        assert registry.get_provider_entity_id("nba-gsw", "espn_nba") == "9"

    def test_nbacom_team_id(self, registry):
        nba_id = registry.get_provider_entity_id("nba-bos", "nbacom_cdn")
        assert nba_id == "1610612738"

    def test_reverse_lookup_espn(self, registry):
        assert registry.get_canonical_id_by_provider("2", "espn_nba") == "nba-bos"
        assert registry.get_canonical_id_by_provider("9", "espn_nba") == "nba-gsw"

    def test_reverse_lookup_unknown(self, registry):
        assert registry.get_canonical_id_by_provider("999", "espn_nba") is None

    def test_bref_abbreviation(self, registry):
        mapping = registry.get_team_mapping("nba-bkn")
        assert mapping is not None
        assert mapping.bref == "BRK"

    def test_bref_abbreviation_phx(self, registry):
        mapping = registry.get_team_mapping("nba-phx")
        assert mapping is not None
        assert mapping.bref == "PHO"


class TestRateLimits:
    """Verify rate limit configuration."""

    def test_espn_rate_limit(self, registry):
        assert registry.get_rate_limit("espn_nba") == 5.0

    def test_bref_rate_limit(self, registry):
        assert registry.get_rate_limit("bref") == 3.0

    def test_nbacom_no_rate_limit(self, registry):
        """NBA.com CDN is a single static file, no rate limit."""
        assert registry.get_rate_limit("nbacom_cdn") is None

    def test_unknown_provider_rate_limit(self, registry):
        assert registry.get_rate_limit("nonexistent") is None
