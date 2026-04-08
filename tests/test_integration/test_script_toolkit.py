"""Integration tests for the shared script toolkit.

Tests the ScriptHttpClient, normalization functions, and IO utilities.
"""

import json
from pathlib import Path

import pytest

from sportshub.scripts.http import ScriptHttpClient, fetch_json
from sportshub.scripts.io import DATA_DIR, data_path, load_data
from sportshub.scripts.normalization import (
    normalize_alias,
    normalize_player_name,
    normalize_team_name,
)


class TestNormalization:
    """Test normalization functions produce consistent canonical forms."""

    def test_team_name_lowercase(self):
        assert normalize_team_name("Boston Celtics") == "boston celtics"

    def test_team_name_strips_accents(self):
        result = normalize_team_name("Olimpija Ljubljana")
        assert "j" in result  # accent stripped

    def test_team_name_strips_suffixes(self):
        assert normalize_team_name("T1 Esports") == "t1"
        assert normalize_team_name("Manchester United FC") == "manchester united"

    def test_team_name_strips_punctuation(self):
        result = normalize_team_name("Olympique de Marseille")
        assert "'" not in result
        assert "." not in result

    def test_player_name_strips_accents(self):
        assert normalize_player_name("Nikola Jokic") == "nikola jokic"

    def test_player_name_handles_diacritics(self):
        # The accent on c should be stripped
        result = normalize_player_name("Luka Doncic")
        assert result == "luka doncic"

    def test_player_name_strips_hyphens(self):
        result = normalize_player_name("Shai Gilgeous-Alexander")
        assert result == "shai gilgeousalexander"

    def test_player_name_strips_periods(self):
        result = normalize_player_name("P.J. Washington")
        assert result == "pj washington"

    def test_normalize_alias_same_as_team_name(self):
        """normalize_alias is the same function as normalize_team_name."""
        assert normalize_alias("ATL Hawks") == normalize_team_name("ATL Hawks")


class TestIO:
    """Test data I/O utilities."""

    def test_data_dir_exists(self):
        assert DATA_DIR.exists()
        assert DATA_DIR.is_dir()

    def test_data_path_resolution(self):
        path = data_path("nba_teams.json")
        assert path.parent == DATA_DIR
        assert path.name == "nba_teams.json"

    def test_load_data_teams(self):
        teams = load_data("nba_teams.json")
        assert isinstance(teams, list)
        assert len(teams) == 30

    def test_load_data_competitions(self):
        comps = load_data("competitions.json")
        assert isinstance(comps, list)
        assert len(comps) >= 1

    def test_load_data_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_data("nonexistent_file.json")


class TestScriptHttpClient:
    """Test ScriptHttpClient initialization and configuration."""

    def test_espn_client_init(self):
        client = ScriptHttpClient("espn_nba")
        assert client.rate_limit_seconds == 5.0
        assert client.base_url is not None
        assert "espn" in client.base_url

    def test_nbacom_client_init(self):
        client = ScriptHttpClient("nbacom_cdn")
        assert client.base_url is not None
        assert "nba.com" in client.base_url

    def test_abbreviation_translation(self):
        client = ScriptHttpClient("espn_nba")
        assert client.translate_abbreviation("GS") == "GSW"
        assert client.translate_abbreviation("BOS") == "BOS"

    def test_standard_to_provider(self):
        client = ScriptHttpClient("espn_nba")
        assert client.standard_to_provider("GSW") == "GS"
        assert client.standard_to_provider("BOS") == "BOS"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            ScriptHttpClient("nonexistent_provider_xyz")
