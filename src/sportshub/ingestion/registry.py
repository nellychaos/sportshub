"""Adapter registry: discover, register, and manage source adapters."""

import structlog

from sportshub.config import Settings
from sportshub.ingestion.base import SourceAdapter
from sportshub.models.common import Sport

logger = structlog.get_logger()


class AdapterRegistry:
    """Central registry of all source adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters[adapter.source_id] = adapter
        logger.info("adapter_registered", source_id=adapter.source_id, sport=adapter.sport.value)

    def get(self, source_id: str) -> SourceAdapter | None:
        return self._adapters.get(source_id)

    def get_all(self) -> list[SourceAdapter]:
        return list(self._adapters.values())

    def get_by_sport(self, sport: Sport) -> list[SourceAdapter]:
        return [a for a in self._adapters.values() if a.sport == sport]

    async def initialize_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.initialize()
                logger.info("adapter_initialized", source_id=adapter.source_id)
            except Exception as e:
                logger.error("adapter_init_failed", source_id=adapter.source_id, error=str(e))

    async def shutdown_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.shutdown()
            except Exception as e:
                logger.warning("adapter_shutdown_error", source_id=adapter.source_id, error=str(e))


def create_registry(settings: Settings) -> AdapterRegistry:
    """Create and populate the adapter registry based on available credentials."""
    from sportshub.ingestion.adapters.espn_nba import ESPNNBAAdapter
    from sportshub.ingestion.adapters.nbacom_cdn import NBAComCDNAdapter
    from sportshub.ingestion.adapters.balldontlie import BallDontLieAdapter
    from sportshub.ingestion.adapters.lolesports import LoLEsportsAdapter
    from sportshub.ingestion.adapters.pandascore_lol import PandaScoreLoLAdapter
    from sportshub.ingestion.adapters.liquipedia_lol import LiquipediaLoLAdapter
    from sportshub.ingestion.adapters.espn_fifa import ESPNFIFAAdapter
    from sportshub.ingestion.adapters.footballdata_wc import FootballDataWCAdapter
    from sportshub.ingestion.adapters.fifa_api import FIFAApiAdapter

    registry = AdapterRegistry()

    # NBA adapters (always available — no auth required for ESPN and CDN)
    registry.register(ESPNNBAAdapter())
    registry.register(NBAComCDNAdapter())

    if settings.balldontlie_api_key:
        registry.register(BallDontLieAdapter(api_key=settings.balldontlie_api_key))
    else:
        logger.warning("balldontlie_skipped", reason="No API key configured")

    # LoL adapters
    registry.register(LoLEsportsAdapter(api_key=settings.lolesports_api_key))

    if settings.pandascore_token:
        registry.register(PandaScoreLoLAdapter(token=settings.pandascore_token))
    else:
        logger.warning("pandascore_skipped", reason="No token configured")

    registry.register(LiquipediaLoLAdapter())

    # FIFA World Cup adapters
    registry.register(ESPNFIFAAdapter())
    registry.register(FIFAApiAdapter())

    if settings.footballdata_api_key:
        registry.register(FootballDataWCAdapter(api_key=settings.footballdata_api_key))
    else:
        logger.warning("footballdata_skipped", reason="No API key configured")

    return registry
