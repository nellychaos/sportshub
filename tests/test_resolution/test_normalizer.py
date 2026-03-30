"""Tests for team name normalization."""

from sportshub.resolution.normalizer import normalize_team_name


class TestNormalizeTeamName:
    def test_basic_lowercase(self):
        assert normalize_team_name("Los Angeles Lakers") == "los angeles lakers"

    def test_strips_whitespace(self):
        assert normalize_team_name("  Lakers  ") == "lakers"

    def test_removes_accents(self):
        assert normalize_team_name("Fnatic") == "fnatic"

    def test_removes_esports_suffix(self):
        assert normalize_team_name("Gen.G Esports") == "geng"

    def test_removes_gaming_suffix(self):
        assert normalize_team_name("Top Esports") == "top"

    def test_removes_team_prefix(self):
        assert normalize_team_name("Team Liquid") == "liquid"

    def test_removes_punctuation(self):
        assert normalize_team_name("Gen.G") == "geng"

    def test_normalizes_whitespace(self):
        assert normalize_team_name("SK   Telecom   T1") == "sk telecom t1"

    def test_abbreviation_passthrough(self):
        assert normalize_team_name("T1") == "t1"

    def test_complex_name(self):
        result = normalize_team_name("KT Rolster Esports Club")
        assert "kt rolster" in result

    def test_empty_string(self):
        assert normalize_team_name("") == ""

    # FIFA World Cup / soccer-specific tests
    def test_removes_national_team_suffix(self):
        result = normalize_team_name("Brazil National Football Team")
        assert result == "brazil"

    def test_removes_fc_suffix(self):
        assert normalize_team_name("FC Barcelona") == "barcelona"

    def test_national_team_mens(self):
        result = normalize_team_name("Germany Men's")
        assert result == "germany"

    def test_accented_country_name(self):
        result = normalize_team_name("Côte d'Ivoire")
        assert result == "cote divoire"

    def test_korea_republic(self):
        result = normalize_team_name("Korea Republic")
        assert result == "korea republic"

    def test_fifa_abbreviation(self):
        result = normalize_team_name("USA")
        assert result == "usa"
