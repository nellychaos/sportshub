"""FastAPI application factory and lifespan management."""

import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.templating import Jinja2Templates

from sportshub.config import get_settings
from sportshub.db.engine import close_db, init_db
from sportshub.cache.client import RedisClient

logger = structlog.get_logger()

# Module-level Redis client (initialized in lifespan)
redis_client = RedisClient()

# Template directory
TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialize and tear down resources."""
    settings = get_settings()
    logger.info("starting_sportshub", environment=settings.environment)

    # Record start time for uptime tracking
    app.state.start_time = time.time()

    # Initialize database
    await init_db()
    logger.info("database_initialized")

    # Initialize Redis cache
    await redis_client.connect(settings.redis_url)
    logger.info("redis_initialized")

    # Store redis client on app state for dependency injection
    app.state.redis = redis_client

    # Set up Jinja2 templates
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    yield

    # Shutdown
    logger.info("shutting_down_sportshub")
    await redis_client.close()
    await close_db()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Sportshub API",
        description=(
            "Sports data aggregation platform serving deduplicated, normalized event data "
            "from multiple upstream sources.\n\n"
            "## Sports Covered\n"
            "- **NBA** - 2025-26 Regular Season & Playoffs (126 events, cross-verified from 2 sources)\n"
            "- **LoL Esports** - LCK, LPL, LEC, LCS, MSI, Worlds (148 events)\n"
            "- **Football** - FIFA World Cup 2026 group stage (54 events)\n\n"
            "## Authentication\n"
            "All endpoints except `/health` require an API key via the `X-API-Key` header.\n\n"
            "## Features\n"
            "- Cross-source deduplication with confidence scoring\n"
            "- Full data provenance (see every source's raw data per event)\n"
            "- Player enrichment with rosters, season stats, bios, and injury reports\n"
            "- Pagination on all list endpoints\n"
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # Import and mount API v1 router (deferred to avoid circular imports)
    from sportshub.api.v1.router import v1_router

    app.include_router(v1_router, prefix="/api/v1")

    # Mount dashboard views (HTML) and API (JSON) — no auth required
    from sportshub.dashboard.views import router as dashboard_views_router
    from sportshub.dashboard.api import router as dashboard_api_router

    app.include_router(dashboard_views_router)
    app.include_router(dashboard_api_router, prefix="/api/v1")

    return app


# uvicorn entrypoint
app = create_app()
