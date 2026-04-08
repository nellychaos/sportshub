"""Pydantic models for provider configuration and ID mappings."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """Configuration for a single data provider, loaded from data/providers.json."""

    source_id: str
    display_name: str
    sport: str
    type: str
    reliability: str
    priority: int | None = None
    auth_required: bool = False
    auth_env_var: str | None = None
    base_url: str | None = None
    rate_limit_seconds: float | None = None
    source_timezone: str | None = None
    endpoints: dict[str, str] = Field(default_factory=dict)
    entity_id_format: dict[str, str] = Field(default_factory=dict)
    abbreviation_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    headers_required: dict[str, str] = Field(default_factory=dict)
    cross_provider_ids: list[str] = Field(default_factory=list)
    notes: str = ""
    documentation: list[dict[str, str]] = Field(default_factory=list)


class NbaComCdnId(BaseModel):
    """NBA.com CDN uses both a 10-digit team_id and a tricode."""

    team_id: str
    tricode: str


class TeamIdMapping(BaseModel):
    """Cross-provider ID mapping for a single team."""

    abbreviation: str
    espn_nba: str | None = None
    nbacom_cdn: NbaComCdnId | None = None
    balldontlie_nba: str | None = None
    bref: str | None = None


class EntityMappingSection(BaseModel):
    """One section of provider_id_mappings.json (e.g., nba_teams)."""

    entity_type: str
    sport: str
    providers: list[str] = Field(default_factory=list)
    notes: dict[str, str] = Field(default_factory=dict)
    mappings: dict[str, TeamIdMapping] = Field(default_factory=dict)


class ProviderRegistryData(BaseModel):
    """Top-level schema for data/providers.json."""

    version: str
    description: str
    providers: list[ProviderConfig]


class ProviderIdMappingsData(BaseModel):
    """Top-level schema for data/provider_id_mappings.json."""

    version: str
    description: str
    nba_teams: EntityMappingSection
