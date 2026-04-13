"""Provider registry: loads and indexes provider metadata and cross-provider ID mappings."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from sportshub.providers.models import (
    ProviderConfig,
    ProviderIdMappingsData,
    ProviderRegistryData,
    TeamIdMapping,
)

logger = structlog.get_logger()

# Default data directory relative to project root.
# Falls back to /app/data/ when installed as a package (Docker).
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
if not _DEFAULT_DATA_DIR.is_dir():
    _DEFAULT_DATA_DIR = Path("/app/data")


class ProviderRegistry:
    """Central registry providing typed access to provider metadata and ID mappings.

    Usage::

        registry = ProviderRegistry()  # loads from default data/ directory
        espn = registry.get_provider("espn_nba")
        abbr = registry.translate_abbreviation("GS", from_source="espn_nba")  # -> "GSW"
        espn_id = registry.get_provider_entity_id("nba-bos", "espn_nba")  # -> "2"
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or _DEFAULT_DATA_DIR
        self._providers: dict[str, ProviderConfig] = {}
        self._id_mappings: ProviderIdMappingsData | None = None

        # Precomputed bidirectional abbreviation maps per provider
        # {source_id: {"our_to_provider": {...}, "provider_to_ours": {...}}}
        self._abbrev_maps: dict[str, dict[str, dict[str, str]]] = {}

        self._load()

    def _load(self) -> None:
        """Load and validate both JSON files."""
        providers_path = self._data_dir / "providers.json"
        mappings_path = self._data_dir / "provider_id_mappings.json"

        # Load providers
        if providers_path.exists():
            with open(providers_path) as f:
                raw = json.load(f)
            data = ProviderRegistryData.model_validate(raw)
            for p in data.providers:
                self._providers[p.source_id] = p
                self._build_abbrev_map(p)
            logger.info("provider_registry_loaded", provider_count=len(self._providers))
        else:
            logger.warning("providers_json_missing", path=str(providers_path))

        # Load ID mappings
        if mappings_path.exists():
            with open(mappings_path) as f:
                raw = json.load(f)
            self._id_mappings = ProviderIdMappingsData.model_validate(raw)
            team_count = len(self._id_mappings.nba_teams.mappings)
            logger.info("provider_id_mappings_loaded", nba_team_count=team_count)
        else:
            logger.warning("provider_id_mappings_missing", path=str(mappings_path))

    def _build_abbrev_map(self, provider: ProviderConfig) -> None:
        """Build bidirectional abbreviation maps from a provider's config."""
        abbrev = provider.abbreviation_map
        our_to_prov = abbrev.get("our_to_provider", {})
        prov_to_ours = abbrev.get("provider_to_ours", {})

        # Auto-generate the reverse direction if only one is supplied
        if our_to_prov and not prov_to_ours:
            prov_to_ours = {v: k for k, v in our_to_prov.items()}
        elif prov_to_ours and not our_to_prov:
            our_to_prov = {v: k for k, v in prov_to_ours.items()}

        self._abbrev_maps[provider.source_id] = {
            "our_to_provider": our_to_prov,
            "provider_to_ours": prov_to_ours,
        }

    # ── Provider metadata ─────────────────────────────────────────────

    def get_provider(self, source_id: str) -> ProviderConfig | None:
        """Look up a provider by source_id. Returns None if not found."""
        return self._providers.get(source_id)

    def get_all_providers(self) -> list[ProviderConfig]:
        """Return all registered providers."""
        return list(self._providers.values())

    def get_providers_by_sport(self, sport: str) -> list[ProviderConfig]:
        """Return all providers for a given sport."""
        return [p for p in self._providers.values() if p.sport == sport]

    # ── Abbreviation translation ──────────────────────────────────────

    def get_abbreviation_map(
        self, source_id: str, direction: str = "provider_to_ours"
    ) -> dict[str, str]:
        """Return the abbreviation map for a provider.

        Args:
            source_id: Provider identifier (e.g., "espn_nba")
            direction: "our_to_provider" or "provider_to_ours"

        Returns:
            Dict mapping abbreviations. Only contains entries that differ;
            abbreviations not in the map are identical across systems.
        """
        maps = self._abbrev_maps.get(source_id, {})
        return maps.get(direction, {})

    def translate_abbreviation(
        self,
        abbr: str,
        from_source: str,
        to_source: str | None = None,
    ) -> str:
        """Translate an abbreviation from one provider's format to another.

        Args:
            abbr: The abbreviation to translate (e.g., "GS")
            from_source: Source provider (e.g., "espn_nba")
            to_source: Target provider. If None, translates to our canonical format.

        Returns:
            Translated abbreviation, or the input unchanged if no mapping exists.
        """
        # Step 1: Convert from source to our canonical format
        prov_to_ours = self.get_abbreviation_map(from_source, "provider_to_ours")
        canonical = prov_to_ours.get(abbr, abbr)

        if to_source is None:
            return canonical

        # Step 2: Convert from canonical to target provider format
        our_to_prov = self.get_abbreviation_map(to_source, "our_to_provider")
        return our_to_prov.get(canonical, canonical)

    # ── Rate limits ───────────────────────────────────────────────────

    def get_rate_limit(self, source_id: str) -> float | None:
        """Return the configured rate limit in seconds for a provider."""
        provider = self._providers.get(source_id)
        if provider:
            return provider.rate_limit_seconds
        return None

    # ── Endpoint URL building ─────────────────────────────────────────

    def get_endpoint(self, source_id: str, endpoint_name: str, **kwargs: str) -> str | None:
        """Build a full URL for a provider endpoint.

        If the endpoint value is already an absolute URL, use it directly.
        Otherwise, prepend the provider's base_url.

        Args:
            source_id: Provider identifier
            endpoint_name: Key in the provider's endpoints dict
            **kwargs: Template variables (e.g., team_id="9", player_id="4065648")

        Returns:
            Fully resolved URL, or None if provider/endpoint not found.
        """
        provider = self._providers.get(source_id)
        if not provider:
            return None

        template = provider.endpoints.get(endpoint_name)
        if not template:
            return None

        # Resolve template variables
        url = template.format(**kwargs) if kwargs else template

        # If the endpoint is a relative path, prepend base_url
        if not url.startswith("http") and provider.base_url:
            url = provider.base_url.rstrip("/") + "/" + url.lstrip("/")

        return url

    # ── Cross-provider ID lookups ─────────────────────────────────────

    def get_team_mapping(self, canonical_id: str) -> TeamIdMapping | None:
        """Look up cross-provider IDs for an NBA team.

        Args:
            canonical_id: Our internal team ID (e.g., "nba-bos")

        Returns:
            TeamIdMapping with IDs for each provider, or None.
        """
        if not self._id_mappings:
            return None
        return self._id_mappings.nba_teams.mappings.get(canonical_id)

    def get_provider_entity_id(
        self, canonical_id: str, target_source: str
    ) -> str | None:
        """Look up a provider-specific entity ID for an NBA team.

        Args:
            canonical_id: Our internal team ID (e.g., "nba-bos")
            target_source: Provider to get the ID for (e.g., "espn_nba")

        Returns:
            Provider-specific ID string, or None if not found.
        """
        mapping = self.get_team_mapping(canonical_id)
        if not mapping:
            return None

        # Look up the field matching target_source
        value = getattr(mapping, target_source, None)
        if value is None:
            return None

        # nbacom_cdn returns an object; extract team_id
        if hasattr(value, "team_id"):
            return value.team_id

        return str(value)

    def get_canonical_id_by_provider(
        self, provider_id: str, source_id: str
    ) -> str | None:
        """Reverse lookup: given a provider-specific ID, find our canonical team ID.

        Args:
            provider_id: The provider's ID for the team (e.g., "9" for ESPN GSW)
            source_id: Which provider (e.g., "espn_nba")

        Returns:
            Our canonical team ID (e.g., "nba-gsw"), or None.
        """
        if not self._id_mappings:
            return None

        for canonical_id, mapping in self._id_mappings.nba_teams.mappings.items():
            value = getattr(mapping, source_id, None)
            if value is None:
                continue
            # Handle nbacom_cdn object
            if hasattr(value, "team_id"):
                if value.team_id == provider_id or value.tricode == provider_id:
                    return canonical_id
            elif str(value) == provider_id:
                return canonical_id

        return None
