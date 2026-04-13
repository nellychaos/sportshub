# Sportshub

Sports data aggregation platform that ingests from 18 upstream providers, deduplicates events via entity resolution, and serves a unified REST API. Covers NBA, League of Legends esports, and FIFA World Cup 2026.

## What It Does

1. **Ingests** schedules, rosters, stats, and odds from ESPN, NBA.com, LoL Esports, PandaScore, Cloudbet, Mollybet, and more
2. **Deduplicates** events across sources using fuzzy matching and optional LLM-backed resolution (Claude)
3. **Enriches** events with player stats (4 tiers for NBA), injury reports, team records, and betting odds
4. **Serves** a clean REST API with confidence scores showing how many sources confirm each event
5. **Monitors** data quality via an operations dashboard with real-time ingestion tracking

---

## Quick Start

### Docker (recommended)

```bash
git clone <repo-url> && cd Sportshub
cp .env.example .env        # Edit credentials as needed
docker compose up -d         # Starts app + PostgreSQL 16 + Redis 7
```

The API is available at `http://localhost:8000`. Interactive docs at `/docs`.

### Native

**Prerequisites:** Python 3.12+, PostgreSQL 16, Redis 7 (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env         # Edit with your local DB credentials

# Database setup
createdb sportshub
psql sportshub -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
alembic upgrade head

# Seed reference data
python scripts/seed_competitions.py
python scripts/seed_teams.py

# Run
uvicorn sportshub.main:app --reload --port 8000
```

---

## Authentication

All API endpoints (except `/health`) require an API key via the `X-API-Key` header:

```bash
curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/api/v1/events?sport=nba&per_page=5
```

API keys are configured as a comma-separated list in `SPORTSHUB_API_KEYS`. Invalid keys receive a `401 Unauthorized` response.

Rate limit: **60 requests/minute** per API key (10 burst). Health endpoint is exempt.

---

## API Endpoints

Base path: `/api/v1`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Platform status, DB/Redis health, entity counts |
| GET | `/events` | Yes | List events with filters (sport, team, competition, date range, status) |
| GET | `/events/{id}` | Yes | Event detail with sources, enrichment (rosters, stats, injuries) |
| GET | `/events/{id}/odds` | Yes | Combined betting odds from Cloudbet + Mollybet |
| GET | `/events/by-source/{source_id}/{source_event_id}` | Yes | Reverse lookup: source-specific ID to canonical event |
| GET | `/teams` | Yes | List teams with sport filter and fuzzy search |
| GET | `/teams/{id}/players` | Yes | Team roster with position/active filters |
| GET | `/players` | Yes | List players with sport, team, search, position filters |
| GET | `/players/{id}` | Yes | Player detail with cross-provider aliases |
| GET | `/competitions` | Yes | List competitions with sport filter |

All list endpoints support `?page=` and `?per_page=` pagination (max 100).

### Web Pages (no auth)

| Path | Description |
|------|-------------|
| `/dashboard` | Operations dashboard -- ingestion status, data quality, provider health |
| `/demo` | Interactive data explorer -- player stats, team cards, live API playground |
| `/docs` | OpenAPI interactive docs (Swagger UI) |
| `/redoc` | ReDoc API reference |

---

## Data Sources

18 providers across 3 sports:

| Source | Sport | Type | Description |
|--------|-------|------|-------------|
| ESPN NBA | NBA | API | Schedule, rosters, player stats, injuries |
| NBA.com CDN | NBA | CDN | Official schedule feed |
| BallDontLie | NBA | API | Player/team stats (free tier) |
| Basketball Reference | NBA | Scrape | Advanced, play-by-play, adjusted shooting |
| TeamRankings NBA | NBA | Scrape | Power ratings, ATS/O-U trends |
| ESPN FIFA | Football | API | World Cup schedule and rosters |
| FIFA API | Football | API | Official FIFA data |
| Football-Data.org | Football | API | Standings and fixtures |
| Reep Entity Register | Football | CSV | Cross-provider player ID mappings |
| LoL Esports API | LoL | API | Official Riot schedule and standings |
| PandaScore LoL | LoL | API | Player stats and team data |
| Liquipedia LoL | LoL | Scrape | Champion pools and career stats |
| Cloudbet Basketball | NBA | API | Live betting odds |
| Cloudbet Soccer | Football | API | Live betting odds |
| Cloudbet LoL | LoL | API | Live betting odds |
| Mollybet Football | Football | WebSocket | Odds confirmation |
| Mollybet Basketball | NBA | WebSocket | Odds confirmation |
| Mollybet Esports | LoL | WebSocket | Odds confirmation |

Events confirmed by multiple sources receive higher `confidence_score` values (0.0-1.0). The `source_count` field shows how many independent sources confirm each event.

---

## Environment Variables

All prefixed with `SPORTSHUB_`. Copy `.env.example` and fill in your values.

### Required

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL async connection string | `postgresql+asyncpg://sportshub:sportshub_dev@localhost:5432/sportshub` |
| `API_KEYS` | Comma-separated API keys | `sh_dev_changeme_in_production` |

### Optional -- Infrastructure

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection (app works without it) | `redis://localhost:6379/0` |
| `DB_POOL_SIZE` | SQLAlchemy pool size | `10` |
| `DB_MAX_OVERFLOW` | SQLAlchemy max overflow | `5` |
| `ENVIRONMENT` | `development`, `staging`, or `production` | `development` |
| `LOG_LEVEL` | Python log level | `INFO` |

### Optional -- Source Credentials

| Variable | Description |
|----------|-------------|
| `BALLDONTLIE_API_KEY` | BallDontLie NBA stats |
| `PANDASCORE_TOKEN` | PandaScore LoL data |
| `LOLESPORTS_API_KEY` | LoL Esports API (has default public key) |
| `FOOTBALLDATA_API_KEY` | Football-Data.org |
| `CLOUDBET_API_KEY` | Cloudbet odds ingestion |
| `CLOUDBET_API_URL` | Cloudbet base URL (default: `https://sports-api.cloudbet.com/pub`) |
| `MOLLYBET_USERNAME` | Mollybet credentials |
| `MOLLYBET_PASSWORD` | Mollybet credentials |
| `MOLLYBET_API_URL` | Mollybet base URL (default: `https://api.mollybet.com`) |
| `ODDS_API_KEY` | The Odds API (external confirmation) |
| `ODDS_API_URL` | Odds API base URL (default: `https://api.the-odds-api.com/v4`) |

### Optional -- LLM Entity Resolution

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for AI-backed matching | |
| `LLM_RESOLUTION_ENABLED` | Enable LLM dedup | `false` |
| `LLM_MODEL` | Model to use | `claude-haiku-4-5-20251001` |
| `LLM_ACCEPTANCE_THRESHOLD` | Min confidence to accept LLM match | `0.40` |
| `LLM_COOLDOWN_MINUTES` | Cooldown between LLM calls for same entity | `30` |

---

## Development

### Tests

```bash
pytest                       # Full suite (requires PostgreSQL + Redis)
pytest tests/test_api/       # API tests only
pytest -x -q                 # Stop on first failure, quiet output
```

### Lint and Type Check

```bash
ruff check src/ scripts/ tests/    # Lint
ruff format src/ scripts/ tests/   # Format
mypy src/sportshub/                # Type check
```

### Database Migrations

```bash
alembic upgrade head          # Apply all migrations
alembic revision --autogenerate -m "description"   # Create new migration
```

### Pre-commit Hooks

```bash
pre-commit install           # One-time setup
pre-commit run --all-files   # Manual run
```

---

## Deployment

The project includes a `Dockerfile` and `docker-compose.prod.yml` with production resource limits. The Dockerfile runs migrations automatically on startup.

For Railway: push to `main` triggers auto-deploy. Set all `SPORTSHUB_*` env vars in the Railway service dashboard.

```bash
# Production Docker
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## Project Structure

```
src/sportshub/
  main.py               FastAPI app factory, middleware, lifespan
  config.py             Pydantic Settings (all env vars)
  api/v1/               REST endpoints and Pydantic schemas
  db/                   SQLAlchemy tables, engine, repositories
  models/               Domain models (Event, Team, Player, Competition)
  ingestion/            Source adapters and adapter registry
  providers/            Provider integrations (Cloudbet, Mollybet)
  resolution/           Entity resolution (fuzzy matching, LLM dedup)
  reconciliation/       Cross-source post-event validation
  validation/           Sport-specific data constraints
  scheduling/           APScheduler jobs with circuit breakers
  cache/                Redis client with graceful fallback
  dashboard/            Ops dashboard (HTMX + Jinja2 templates)
  demo/                 Interactive data explorer
  monitoring/           Health checks
  templates/            Jinja2 HTML templates
  scripts/              Shared utilities (HTTP client, normalization, IO)
data/                   Seed data and provider registry (JSON)
alembic/               Database migrations (6 revisions)
scripts/               Data fetch, merge, and enrichment scripts
tests/                 pytest suite (adapters, API, integration, resolution)
```

---

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`) runs on push to `main` and PRs:

1. **Lint** -- Ruff format + lint checks
2. **Type Check** -- MyPy
3. **Test** -- Full pytest suite with PostgreSQL 16 + Redis 7 services
4. **Data Validation** -- JSON schema checks on data files
