# Sportshub: Project Summary

## 1. Project Overview

Sportshub is a sports data aggregation platform that ingests upcoming event data from multiple independent sources, deduplicates and normalises it into a single canonical dataset, and exposes it through a REST API with full data provenance. It serves as the internal data backbone for multiple front-end sites and products.

### Problem

No single sports data source is both comprehensive and reliable. Official APIs have gaps, community sources have errors, and betting feeds use non-standard naming. A downstream consumer (frontend, analytics pipeline, or model) needs a single, high-confidence view of "what events are happening, when, and where" without worrying about source inconsistencies.

### Solution

Sportshub pulls from 15 registered providers (9 with live automated adapters) across three sports, resolves entity mismatches (team names, competition labels, timezones) using alias tables and AI fallback, scores each event by how many independent sources confirm it, and continuously validates data quality through post-event reconciliation and dynamic reliability scoring.

### Design Principles

- **Multi-source consensus over single-source trust.** Every data point should be confirmed by 2+ independent sources. Confidence scores reflect how many sources agree, not which source is "best."
- **Self-healing over manual curation.** Successful entity matches create aliases automatically. Failed matches route to AI. The alias table grows over time, reducing future failures without human intervention.
- **Declarative configuration over code changes.** Provider metadata (endpoints, rate limits, abbreviation maps) lives in `data/providers.json`, not in Python code. Adding a source means editing JSON and writing a thin adapter.
- **Transparency over opacity.** Every data point traces back to specific sources with timestamps and confidence scores. The operational dashboard exposes data quality metrics internally rather than hiding them.
- **Official APIs for core data, scraping for enrichment.** Schedule and fixture data comes from documented APIs (FIFA, NBA.com CDN, LoL Esports). Player stats and betting trends come from scraped sources. If scraping breaks, core data continues.

### Sports Covered

| Sport | Season | Automated Adapters | Supplementary Sources | Competitions |
|-------|--------|-------------------|----------------------|--------------|
| **NBA** | 2025-26 | ESPN NBA, NBA.com CDN, BallDontLie, Mollybet | BRef (stats), TeamRankings (ratings/trends) | Regular Season, Playoffs |
| **League of Legends** | 2026 | LoL Esports, PandaScore, Mollybet | Liquipedia (health check only) | LCK, LEC, LCS, MSI, Worlds |
| **Football (FIFA)** | WC 2026 | FIFA API, Football-Data.org, ESPN FIFA, Mollybet | Reep (entity register, 23 provider cross-refs) | FIFA World Cup 2026 |

---

## 2. Architecture

### Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Web framework | FastAPI (async) | Native async, auto-generated OpenAPI docs, Pydantic integration |
| Database | PostgreSQL 16 + SQLAlchemy Core (async) + asyncpg | JSONB for flexible metadata, trigram indexes for fuzzy search, partial indexes for query performance |
| Cache | Redis 7 with hiredis | Reliability score cache, rate limiting, graceful fallback if unavailable |
| Scheduling | APScheduler 3.x (AsyncIOScheduler) | In-process async scheduler, lightweight for MVP (Celery for scale) |
| HTTP client | httpx (async) | Connection pooling, timeout handling, async-native |
| AI resolution | Anthropic Claude Haiku via `anthropic` SDK | Cheap ($0.80/1M input tokens), fast (~2s), good enough for entity matching |
| WebSocket | `websockets` library | Mollybet real-time stream ingestion |
| Dashboard | HTMX + Jinja2 templates | Server-rendered, no frontend build step, auto-refresh partials |
| Provider config | JSON registry (`data/providers.json`) | Declarative, version-controlled, no code changes to add sources |
| Migrations | Alembic | Standard SQLAlchemy migration tool |

### Data Flow

```
   +-------------------+
   | Provider Registry  |  data/providers.json (15 providers)
   | (config, endpoints,|  data/provider_id_mappings.json
   |  abbreviation maps)|
   +-------------------+
            |
            v
   +-------------------+       +--------------------+
   | 9 Automated       |       | 6 Supplementary    |
   | Adapters           |       | Sources             |
   | (ESPN, NBA.com,   |       | (BRef, TeamRankings,|
   |  FIFA, LoLEsports,|       |  Reep, Liquipedia)  |
   |  PandaScore,      |       | via manual scripts  |
   |  Mollybet x3)     |       +--------------------+
   +-------------------+                |
            |                           v
            v                  +-------------------+
   +-------------------+       | Reference Data    |
   | Raw Events        |       | 22 JSON files     |
   | source_records    |       | (players, stats,  |
   | table             |       |  ratings, trends) |
   +-------------------+       +-------------------+
            |
            v
   +-------------------+
   | Entity Resolution  |
   |  1. Normalise      |
   |  2. Alias lookup   |
   |  3. AI fallback    |
   |  4. Timezone norm  |
   +-------------------+
            |
            v
   +-------------------+
   | Event Matching     |
   |  Confidence score  |
   |  Merge or create   |
   +-------------------+
            |
      +-----+-----+
      |           |
      v           v
   +----------+ +---------------+
   | Canonical| | Confirmation  |
   | Events   | | (Mollybet,    |
   |          | |  OddsAPI)     |
   +----------+ +---------------+
      |
      v
   +-------------------+        +-------------------+
   | Validation         |        | Reconciliation    |
   | Sport-specific     | -----> | Post-event        |
   | constraints        |        | accuracy check    |
   +-------------------+        +-------------------+
      |                                   |
      v                                   v
   +-------------------+        +-------------------+
   | REST API           |        | Dynamic Reliability|
   | /api/v1/...        |        | Scoring            |
   +-------------------+        +-------------------+
      |
      v
   +-------------------+
   | Operational        |
   | Dashboard          |
   | /dashboard         |
   +-------------------+
```

### Application Lifecycle

The FastAPI lifespan context manager (`main.py`) orchestrates startup and shutdown:

**Startup:**
1. Initialise async database engine and run migrations
2. Connect Redis client (graceful fallback if unavailable)
3. Configure Jinja2 templates for dashboard
4. Build adapter registry (9 adapters, credential-guarded)
5. Build confirmation registry (Mollybet + OddsAPI)
6. Start APScheduler with all job cadences

**Shutdown:**
1. Stop scheduler
2. Shutdown all adapters (close HTTP/WS clients)
3. Shutdown confirmation sources
4. Close Redis and database connections

---

## 3. Core Subsystems

### 3.1 Ingestion

**9 automated adapters** inherit from `SourceAdapter` (abstract base in `ingestion/base.py`):

| Adapter | Source ID | Sport | Method | Cadence |
|---------|-----------|-------|--------|---------|
| ESPN NBA | `espn_nba` | NBA | REST | 1h |
| NBA.com CDN | `nbacom_cdn` | NBA | CDN | 2h |
| BallDontLie | `balldontlie_nba` | NBA | REST | 6h |
| LoLEsports | `lolesports` | LoL | REST | 1h |
| PandaScore | `pandascore_lol` | LoL | REST | 2h |
| FIFA API | `fifa_api` | Football | REST | 2h |
| Football-Data | `footballdata_wc` | Football | REST | 3h |
| ESPN FIFA | `espn_fifa` | Football | REST | 1h |
| Mollybet (x3) | `mollybet_fb/basket/esports` | All | WebSocket | 2h |

Each adapter's `fetch_upcoming()` returns a list of `RawEvent` with the source's raw team names, competition labels, and timestamps untouched. These are persisted as `source_records` for full provenance.

**Why separate adapters per source**: Each API has its own quirks (date formats, pagination, auth, rate limits). A thin adapter per source isolates these quirks. The adapter is ~120 lines; the complexity lives in the resolution pipeline, not the ingestion layer.

**Key files:**
- `src/sportshub/ingestion/base.py` -- SourceAdapter interface
- `src/sportshub/ingestion/registry.py` -- AdapterRegistry + factory
- `src/sportshub/ingestion/adapters/` -- 10 adapter files

### 3.2 Entity Resolution

The resolution pipeline (`resolution/pipeline.py`) processes unmatched source records through these stages:

1. **Team Resolution** (`normalizer.py`): Normalise raw names (lowercase, strip accents, remove suffixes like "FC", "Esports", "National Team"), look up in alias cache. O(1) for cached aliases.

2. **AI Fallback** (`llm_resolver.py`): When alias lookup fails, send the raw name + candidate list to Claude Haiku. If confidence >= 0.70, accept the match and auto-create an alias for future runs. Circuit breaker opens if acceptance rate drops below 40% or latency exceeds 10s.

3. **Event Matching** (`matcher.py`): Find candidate canonical events by sport + teams + time window (24h). Score confidence:
   - Team set match: +0.50
   - Home/away correct: +0.10
   - Time proximity: +0.05 to +0.40 (sliding scale)
   - Auto-match threshold: 0.70
   - Tentative (AI promotion eligible): 0.50-0.70

4. **Merge or Create** (`merger.py`): If matched, merge source data into existing event (higher-priority sources override time/venue). If unmatched, create a new canonical event.

5. **Validation** (`validation/engine.py`): Run sport-specific constraints (e.g., NBA no same-day doubleheaders, LoL 3-day team spacing, FIFA 5-day rest period). Confidence reduced by 0.30 on error-level violations.

**Why hybrid resolution**: Rule-based matching handles 95%+ of cases cheaply. The AI fallback exists for the long tail (international name variants, abbreviation mismatches). Every AI success creates a new alias, so the rule-based hit rate improves over time. This is the "self-healing" mechanism.

**Source Priority** (determines which source's data wins on conflict):

```
Official (10):  nbacom_cdn, lolesports, fifa_api
High (8-9):     espn_nba, pandascore_lol, mollybet_fb, mollybet_basket, mollybet_esports
Medium (7-8):   espn_fifa, footballdata_wc
Community (5):  balldontlie_nba, liquipedia_lol
```

### 3.3 Confirmation

Two external confirmation sources cross-check canonical events:

| Source | Confidence Boost | Coverage | Cache TTL |
|--------|-----------------|----------|-----------|
| **Mollybet** | +0.20 | All 3 sports | 1h (Redis) |
| **OddsAPI** | +0.15 | NBA, Football | 1h (Redis) |

Matching: Fuzzy team name substring + kickoff within +/-90 minutes. Results stored in `confirmation_checks` table. Runs every 6 hours for events in the next 48 hours.

### 3.4 Reconciliation

After events pass their scheduled time (+3h buffer), the reconciliation subsystem:

1. **Collects** observed results from adapters that support `fetch_result()`
2. **Compares** predicted vs observed: Did the event happen? Was the time accurate? Were the teams correct? Was the venue correct?
3. **Persists** reconciliation reports and per-source accuracy records
4. **Updates** event status (completed, cancelled, postponed) via majority vote
5. **Feeds** accuracy data into the dynamic reliability scorer for future priority adjustments

**Why reconciliation matters**: This is the system's primary quality assurance mechanism. It turns each completed event into a test case that validates (or penalises) every source that reported on it. Over time, source reliability scores converge on reality.

### 3.5 Dynamic Reliability Scoring

`DynamicReliabilityScorer` computes source priorities from historical accuracy data:
- Exponential decay weighting (14-day half-life)
- Minimum 10 samples before trusting dynamic scores (falls back to hardcoded priorities)
- Per-field breakdown: time accuracy, team correctness, venue correctness
- Pre-computed daily at 04:00 UTC and cached in Redis

### 3.6 Scheduling

APScheduler manages all recurring jobs:

| Job | Cadence | Description |
|-----|---------|-------------|
| Ingestion (per adapter) | 1-6h | Fetch upstream events |
| Entity resolution | 1h 15m | Match unresolved source records |
| Health check | 1h | Adapter availability probes |
| Stale cleanup | Daily 03:00 | Log past-due scheduled events |
| Validation sweep | 4h | Re-validate all scheduled events |
| Reconciliation | 2h | Post-event accuracy checks |
| Reliability refresh | Daily 04:00 | Pre-compute source reliability scores |
| Confirmation check | 6h | External event verification |

**Circuit Breaker** (`scheduling/circuit_breaker.py`): Pauses adapters after 5 consecutive failures. Initial pause: 1 hour, escalates to 4 hours. Auto-probes after pause expires.

### 3.7 Provider Registry

The provider registry (`src/sportshub/providers/`) centralises all source metadata in a declarative JSON file (`data/providers.json`). Each of the 15 providers has:

- Identity: source_id, display_name, sport, type (api/cdn/web_scrape/api_websocket/csv_github)
- Reliability: official/established/community classification, numeric priority
- Access: base_url, endpoints, auth requirements, rate_limit_seconds
- Entity mapping: abbreviation_map (our_to_provider, provider_to_ours), entity_id_format

Cross-provider ID mappings (`data/provider_id_mappings.json`) link the same team across sources (e.g., NBA team ATL has ESPN ID "1", NBA.com ID "1610612737", BallDontLie ID "1", BRef code "ATL").

**Why declarative JSON**: Adding a new provider means editing a JSON file, not writing Python. The registry provides typed lookups (`get_provider()`, `get_abbreviation_map()`, `translate_abbreviation()`) that adapters, the dashboard, and scripts all consume from one source of truth.

### 3.8 Operational Dashboard

The dashboard (`/dashboard`) is a server-rendered HTMX application that provides real-time visibility into the data pipeline.

**Sections:**
- **Status banner** -- event/team/competition counts, system health
- **Data Sources** -- all 15 providers grouped by sport, with type badges and reliability indicators
- **Reference Data** -- inventory of all 22 JSON data files with record counts, sizes, schema validation status
- **Activity** -- tabbed view of automated ingestion runs (24h) and manual script activity
- **Alerts** -- stale source warnings, circuit breaker state
- **Data Quality** -- confidence score distribution, source coverage, unmatched records
- **Data Completeness** -- player stat coverage (base/advanced/PBP/adjusted shooting), team data counts, event enrichment rates
- **Reconciliation** -- post-event accuracy metrics (teams, venue, time)
- **Reliability Scoring** -- per-source dynamic scores vs fallback priorities

Each section is an HTMX partial that auto-refreshes on 30-120s intervals. No JavaScript framework; no separate frontend build.

**Why server-rendered**: The dashboard is an internal operations tool, not a consumer product. HTMX + Jinja2 keeps the dependency footprint minimal, avoids a separate frontend deployment, and makes each section independently refreshable without full page reloads.

### 3.9 Script Activity Tracking

Manual data operations (BRef merges, NBA stat fetches, Reep enrichment) run as standalone Python scripts outside the main app process. The activity log (`src/sportshub/scripts/activity_log.py`) tracks these runs:

```python
with log_script_run("merge_bref_pbp") as run:
    # do work
    run.records_processed = 527
    run.summary = "Merged play-by-play for 527/535 players"
```

Logs append to `data/script_activity.json` (trimmed to 200 entries). The dashboard reads this file to display script history alongside automated ingestion runs.

**Why file-based logging**: Scripts run outside the app process and may not have database access. A JSON file on disk is the simplest mechanism that works everywhere. The context manager pattern ensures runs are always recorded even if the script crashes.

### 3.10 Reference Data Layer

22 JSON files in `data/` provide static and semi-static reference data:

**NBA:**
- `nba_teams.json` -- 30 teams with metadata, aliases, arena, coach, colours
- `nba_players.json` -- 538 players with position, height, draft info
- `nba_player_stats.json` -- 535 players with 4 stat tiers: base per-game, advanced (PER/WS/BPM/VORP), play-by-play (on/off, turnover/foul breakdowns), adjusted shooting (FG+/TS+/shooting value added)
- `nba_schedule.json` -- full 2025-26 season schedule
- `nba_team_stats.json` -- team-level aggregates
- `teamrankings_power_ratings_2026.json` -- predictive power ratings
- `teamrankings_ats_trends_2026.json` -- against-the-spread records
- `teamrankings_ou_trends_2026.json` -- over/under trends

**Football:**
- `fifa_wc_teams.json` -- 58 World Cup teams
- `fifa_wc_players.json` -- 1,300 players across 50 squads
- `fifa_wc_schedule.json` -- 104 matches with bracket labels

**LoL:**
- `lol_teams.json` -- 40 teams across 5 leagues

**Infrastructure:**
- `providers.json` -- 15 provider configurations
- `provider_id_mappings.json` -- cross-provider team ID mappings
- `competitions.json` -- 9 competition definitions
- `venue_timezones.json` -- venue-to-IANA timezone lookup
- `mollybet_competitions.json` -- Mollybet competition ID mapping
- `script_activity.json` -- manual script run log

**Raw imports** (pre-merge):
- `bref_advanced_2026_raw.json` -- 721 players
- `bref_adj_shooting_2026_raw.json` -- 728 players
- `bref_pbp_2026_raw.json` -- 727 players

**Why JSON files over database**: Reference data changes infrequently (once per season for rosters, weekly for stats). JSON files are version-controlled in git, readable without database access, and trivial to diff. The merge scripts (`scripts/merge_bref_*.py`) enrich `nba_player_stats.json` progressively, building up from base stats to fully enriched records.

---

## 4. Data Model

### Key Tables

```
teams                     players                  competitions
  id (UUID PK)             id (UUID PK)             id (UUID PK)
  name                     name                     name
  short_name               team_id (FK)             short_name
  abbreviation             sport                    sport
  sport                    position                 season
  metadata (JSONB)         jersey_number            region, tier
  active                   nationality              metadata (JSONB)
                           metadata (JSONB)

team_aliases              player_aliases
  alias                    alias
  alias_normalized         source_id
  source_id                player_id (FK)
  team_id (FK)
  is_primary

events (canonical)        source_records (raw)      ingestion_runs
  id (UUID PK)             id (UUID PK)              id (UUID PK)
  sport                    event_id (FK, nullable)   source_id
  competition_id (FK)      source_id                 started_at
  home_team_id (FK)        source_event_id           status
  away_team_id (FK)        raw_home_team             records_fetched
  scheduled_at             raw_away_team             records_new
  status                   raw_competition           records_updated
  match_format             scheduled_at              records_matched
  confidence_score         raw_data (JSONB)          error_message
  source_count             ingestion_run_id (FK)
  venue                    match_confidence
  metadata (JSONB)

confirmation_checks       llm_resolutions           reconciliation_results
  event_id (FK)            source_record_id (FK)     event_id (FK)
  source_id                resolution_type           event_occurred
  exists (bool)            input_text                time_diff_seconds
  confidence_boost         candidates (JSONB)        teams_correct
  checked_at               llm_output (JSONB)        venue_correct
                           confidence                metadata (JSONB)
                           accepted (bool)
                           alias_created (bool)
                           latency_ms, tokens_used

source_accuracy_records
  reconciliation_id (FK)
  source_id, sport
  time_accurate, teams_correct, venue_correct
  recorded_at
```

### Key Design Decisions

- **SQLAlchemy Core over Declarative ORM.** Table objects + Pydantic domain models keeps the data layer clean. No ORM magic; queries are explicit.
- **JSONB for metadata.** Rosters, raw source data, and enrichment data stored as JSONB. Flexible without schema migrations. Queryable and indexable in PostgreSQL.
- **Nullable event_id on source_records.** Allows storing unmatched records for later resolution or manual review, rather than discarding them.
- **Trigram GIN indexes on names.** Enables fuzzy text search for entity resolution without external search infrastructure.
- **Partial indexes on scheduled events.** `(sport, scheduled_at) WHERE status = 'scheduled'` keeps upcoming-event queries fast as the table grows.

### Deduplication Strategy

Events are unique by the tuple `(sport, home_team_id, away_team_id, scheduled_at)`. When multiple sources report the same fixture, they are merged into a single canonical event with an incrementing `source_count` and the maximum `confidence_score` across sources. Each source's raw data is preserved in `source_records` for full auditability.

---

## 5. API Layer

**Base URL:** `/api/v1`
**Auth:** `X-API-Key` header (required for all endpoints except health and dashboard)
**Total endpoints:** 35

### Data API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | DB/Redis status, event counts (no auth) |
| `GET` | `/api/v1/events` | Paginated event list with filters |
| `GET` | `/api/v1/events/{id}` | Full event detail with all source records |
| `GET` | `/api/v1/teams` | Paginated team list with fuzzy search |
| `GET` | `/api/v1/teams/{id}/players` | Team roster |
| `GET` | `/api/v1/players` | Player search |
| `GET` | `/api/v1/players/{id}` | Player detail |
| `GET` | `/api/v1/competitions` | Competition list |

### Dashboard API (JSON)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/dashboard/overview` | System health, event/team/competition counts |
| `GET` | `/api/v1/dashboard/sources` | Per-source status, last success, failure count |
| `GET` | `/api/v1/dashboard/ingestion` | 24h ingestion timeline per source |
| `GET` | `/api/v1/dashboard/quality` | Confidence distribution, source coverage, unmatched |
| `GET` | `/api/v1/dashboard/alerts` | Stale sources, circuit breaker state |
| `GET` | `/api/v1/dashboard/violations` | Constraint violation counts |
| `GET` | `/api/v1/dashboard/reconciliation` | Post-event accuracy metrics |
| `GET` | `/api/v1/dashboard/llm` | AI resolution effectiveness and cost |
| `GET` | `/api/v1/dashboard/reliability` | Per-source dynamic reliability scores |
| `GET` | `/api/v1/dashboard/reference-data` | File inventory, record counts, schema status |
| `GET` | `/api/v1/dashboard/script-activity` | Manual script run history |
| `GET` | `/api/v1/dashboard/completeness` | Player/team/event data coverage |

### Dashboard HTML (HTMX partials)

`GET /dashboard` serves the main page. Each section has a corresponding `GET /dashboard/partials/{section}` endpoint that returns an HTML fragment for HTMX swap.

### Event List Filters

- `sport` (nba, lol, football)
- `team_id`, `competition_id`
- `status` (scheduled, postponed, cancelled, completed)
- `from`, `to` (date range)
- `sort` (asc/desc by scheduled_at)
- `page`, `per_page` (pagination)

### Response Format

```json
{
  "data": [
    {
      "id": "uuid",
      "sport": "football",
      "home_team": { "id": "uuid", "name": "Mexico", "abbreviation": "MEX" },
      "away_team": { "id": "uuid", "name": "South Africa", "abbreviation": "RSA" },
      "competition": { "id": "uuid", "name": "FIFA World Cup 2026" },
      "scheduled_at": "2026-06-11T02:00:00Z",
      "status": "scheduled",
      "confidence_score": 0.95,
      "source_count": 4,
      "venue": "Estadio Azteca"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total": 104,
    "total_pages": 5
  }
}
```

Event detail responses include enriched team data (rosters, stats, injuries) and the full list of source records showing what each upstream adapter reported.

---

## 6. FIFA World Cup 2026 Dataset

### Teams (`data/fifa_wc_teams.json`)

**58 teams total:** 48 with group assignments + 10 historical/reserve teams (group_2026: null).

Each team includes:
- Identity: name, short_name, abbreviation, FIFA code
- Organization: confederation, FIFA ranking, group_2026
- Personnel: coach, captain, nickname, kit_supplier
- Visuals: colors (primary/secondary/tertiary), home/away kit descriptions, logo URL
- History: world_cup_appearances, best_world_cup_result
- Aliases: 3-5 per team across sources (fifa_api, espn_fifa, footballdata_wc, mollybet_fb)

**12 Groups (A-L):**

| Group | Teams |
|-------|-------|
| A | Mexico, South Africa, Jamaica, Chile |
| B | USA, England, Bolivia, Bahrain |
| C | Brazil, Serbia, Australia, New Zealand |
| D | France, Colombia, Saudi Arabia, China PR |
| E | Argentina, Morocco, Uzbekistan, Iceland |
| F | Spain, Uruguay, DR Congo, Ecuador |
| G | Germany, Japan, Costa Rica, Kenya |
| H | Portugal, Denmark, Paraguay, Peru |
| I | Netherlands, Canada, Cameroon, Albania |
| J | Belgium, Iran, Honduras, Senegal |
| K | Italy, Turkey, Nigeria, Guatemala |
| L | Croatia, Poland, Egypt, Panama |

### Players (`data/fifa_wc_players.json`)

**1,300 players** across 50 teams (26 per squad).

Each player includes:
- Identity: name, position (GK/DEF/MID/FWD), jersey_number, nationality
- Club: current club, club country
- Physical: age, height_cm, preferred_foot, date_of_birth
- International: caps, World Cup history (list of years), notable achievements
- Aliases: 2-3 per player across sources

### Schedule (`data/fifa_wc_schedule.json`)

**104 matches** from June 11 to July 19, 2026 across 16 venues in USA, Mexico, and Canada.

| Stage | Matches | Dates |
|-------|---------|-------|
| Group Stage | 72 | Jun 11 - Jun 28 |
| Round of 32 (Playoffs A-P) | 16 | Jun 29 - Jul 3 |
| Round of 16 | 8 | Jul 4 - Jul 7 |
| Quarter-Finals | 4 | Jul 9 - Jul 10 |
| Semi-Finals | 2 | Jul 13 - Jul 14 |
| Third Place Play-off | 1 | Jul 18 |
| Final | 1 | Jul 19 |

### Knockout Bracket Structure

The bracket was a significant area of work. Groups are paired: A-B, C-D, E-F, G-H, I-J, K-L.

**Round of 32 (16 matches = Playoffs A through P):**

- **Playoffs A-L** (12 matches): Each group winner faces the runner-up from the paired group.
  - Playoff A: 1st Group A vs 2nd Group B
  - Playoff B: 1st Group B vs 2nd Group A
  - Playoff C: 1st Group C vs 2nd Group D
  - (and so on through Playoff L: 1st Group L vs 2nd Group K)

- **Playoffs M-P** (4 matches): The 8 best 3rd-placed teams face each other, seeded by group stage performance (1st-ranked vs 8th-ranked, 2nd vs 7th, etc.). Actual matchups resolved after group stage.

**Round of 16 onward:** Each R16 match pairs winners from the same bracket sub-section:
- R16-1: Winner Playoff A vs Winner Playoff B (A-B bracket)
- R16-2: Winner Playoff C vs Winner Playoff D (C-D bracket)
- R16-7: Winner Playoff M vs Winner Playoff N (3rd-place bracket 1)
- R16-8: Winner Playoff O vs Winner Playoff P (3rd-place bracket 2)

Every knockout match has:
- `bracket_label`: e.g., "Playoff D", "Quarter-Final 2" (for bookmaker market mapping)
- `feeds_into`: e.g., "R16-2" (for bracket traversal)

This structure directly solves the market mapping issue where bookmaker references like "Winner of Playoff D" can now resolve to the specific R32-4 match (1st Group D vs 2nd Group C).

---

## 7. Mollybet Integration

### What is Mollybet

Mollybet is a multi-bookmaker betting aggregator. If a real-money market exists on Mollybet for a fixture, that fixture is definitively happening. This makes it an authoritative confirmation source with excellent data quality.

### Integration Architecture

Mollybet is wired in at four levels:

**Level 1 -- Ingestion Adapter** (`ingestion/adapters/mollybet.py`)

Three instances registered (one per sport: `mollybet_fb`, `mollybet_basket`, `mollybet_esports`). Each connects to the Mollybet WebSocket stream to discover events:

1. REST auth: `POST /v1/sessions/` with username/password, token returned in `data` key
2. WebSocket connect: `wss://api.mollybet.com/v1/stream/?token=TOKEN`
3. Receive batched messages `{"ts": float, "data": [["event", {...}], ...]}` until `"sync"` marker
4. Filter: `event_type == "normal"` (skip outrights) + matching sport code
5. Convert to `RawEvent` and return

**Level 2 -- Confirmation Source** (`ingestion/confirmation/mollybet.py`)

Same WebSocket approach, caches event snapshots per sport in Redis (1h TTL). Provides +0.20 confidence boost via fuzzy team name matching + kickoff within 90 minutes.

**Level 3 -- Priority Resolver** (`resolution/matcher.py`)

Mollybet sources scored at priority 9 (football/NBA) and 8 (esports), placing them just below official APIs (10) but above community sources (5).

**Level 4 -- Competition Mapping** (`data/mollybet_competitions.json`)

Pre-seeded mapping of Sportshub competitions to Mollybet IDs, discovered via a WebSocket stream scan of 4,529 live events:

| Competition | Mollybet Sport | Mollybet ID |
|-------------|---------------|-------------|
| FIFA WC 2026 | fb | 301 |
| NBA Regular Season | basket | 1182 |
| LCK 2026 | esports | 10001720 |
| LEC 2026 | esports | 10002142 |
| LCS 2026 | esports | 10005097 |
| Worlds 2026 | esports | 10005788 |

---

## 8. Lessons Learned

### WebSocket Message Format

**Problem:** Initial parser expected bare arrays `["event", {...}]`. Actual messages are batched: `{"ts": 1234.5, "data": [["event", {...}], ["event", {...}], ...]}`.

**Fix:** Updated parsing loop to unwrap the `data` array from the wrapper object.

**Lesson:** WebSocket APIs often batch messages for efficiency. Always capture and log a raw message sample before writing the parser.

### Bracket Structure Bugs

**Problem:** The initial FIFA WC 2026 knockout bracket had two critical bugs:
1. Groups E-L winners bypassed R32 entirely, going directly to R16
2. R32 mixed runners-up vs ambiguous 3rd-placed team pools that could not resolve to a single team

**Fix:** Rewrote the entire knockout bracket with correct group pairing, `bracket_label` (Playoff A-P), and `feeds_into` fields for traversal.

**Lesson:** Tournament bracket structures are deceptively complex. The 48-team, 12-group format with 8 best 3rd-placed teams creates a non-trivial seeding matrix. Always validate the bracket by checking that every team appears exactly once and every later-round slot references exactly two feeder matches.

### Batch Data Management (1,300 Players)

**Problem:** Writing 1,300 player records required managing context window limits.

**Approach:** Split into 13 batch files (78-312 players each), wrote sequentially, then merged via Python script.

**Lesson:** For large seed datasets, a batch-and-merge workflow keeps each write manageable. Version the merged file, not the batches.

### Provider Abbreviation Mismatches

**Problem:** Different sources use different abbreviations for the same team. ESPN uses "GS" for Golden State; NBA.com uses "GSW"; BallDontLie uses "GSW". TeamRankings uses "Okla City" for Oklahoma City Thunder.

**Fix:** Each provider in `providers.json` has an `abbreviation_map` with bidirectional lookups (`our_to_provider`, `provider_to_ours`). The provider registry exposes `translate_abbreviation()` for adapters and merge scripts.

**Lesson:** Abbreviation mismatches are the most common entity resolution failure. A per-provider map in declarative config handles this without code changes.

### Scraping Fragility

**Problem:** Basketball Reference and TeamRankings are Cloudflare-protected. Standard HTTP requests return challenge pages. Browser automation works but is slow and fragile.

**Approach:** Used Chrome browser automation for initial data extraction. Data saved to JSON files and merged via scripts.

**Lesson:** Scraping should be used for supplementary enrichment, not core data. Managed proxy services (ScrapingBee, Zenrows) can replace browser automation with more reliable API-based scraping at $50-250/month.

---

## 9. Current Production State (April 10, 2026)

| Metric | Value |
|--------|-------|
| Events tracked | 428 (NBA 126, Football 126, LoL 176) |
| Teams | 141 |
| Competitions | 18 |
| Ingestion runs (last 24h) | 72 runs across 9 adapters |
| Records fetched (last 24h) | 17,512 |
| Events reconciled | 199 (100% team accuracy, 0.0s avg time drift) |
| AI resolution attempts | 1,980 calls, $7.10 total |
| Unmatched source records | 10,627 (mostly Mollybet coverage beyond our 3 sports) |
| NBA player stat coverage | 535/535 base, 527 advanced, 527 PBP, 526 adj. shooting |
| Team data completeness | Power ratings 30/30, ATS trends 30/30, O/U trends 30/30 |
| Reference data files | 22 JSON files, 5.8 MB total |
| Production code | ~11,000 lines across ~80 files |

Production readiness analysis and scaling architecture are documented in `docs/STRATEGIC_EVALUATION.md`.

---

## 10. File Reference

### Source Code

```
src/sportshub/
  main.py                              # App factory, lifespan management
  config.py                            # Pydantic settings (env vars)
  api/
    v1/events.py, teams.py, ...        # REST endpoints
    dependencies.py                    # Auth, pagination, DB session
    v1/schemas.py                      # Response models
  db/
    engine.py                          # Async SQLAlchemy engine
    tables.py                          # All table definitions
    repositories/                      # Data access layer (6 repos)
  models/                              # Pydantic domain models
  ingestion/
    base.py                            # SourceAdapter interface
    registry.py                        # Adapter registration
    adapters/                          # 10 adapter files
    confirmation/                      # Mollybet + OddsAPI confirmation
  resolution/
    pipeline.py                        # Full resolution orchestration
    normalizer.py                      # Team name normalization + alias lookup
    matcher.py                         # Confidence scoring + source priority
    merger.py                          # Event merge/create logic
    llm_resolver.py                    # Claude Haiku fallback
    reliability.py                     # Dynamic source scoring
    timezone.py                        # Venue timezone resolution
  providers/
    registry.py                        # Central provider config loader
    models.py                          # Provider Pydantic models
    abbreviations.py                   # Cross-provider abbreviation translation
  dashboard/
    service.py                         # Dashboard data aggregation (922 lines)
    api.py                             # Dashboard JSON endpoints
    views.py                           # HTMX HTML views
    schemas.py                         # Dashboard response models
  scheduling/
    scheduler.py                       # APScheduler job registration
    jobs.py                            # Job implementations
    circuit_breaker.py                 # Adapter fault tolerance
  scripts/
    activity_log.py                    # Script run tracking (context manager)
    http.py                            # HTTP utilities with retry
    io.py                              # File I/O utilities
    normalization.py                   # Data normalization helpers
  reconciliation/                      # Post-event verification
  validation/                          # Sport-specific constraints
  monitoring/                          # Logging, metrics, health
  templates/
    dashboard/
      index.html                       # Main dashboard layout
      partials/                        # 11 HTMX partial templates
```

### Data Files

```
data/
  providers.json                       # 15 provider configurations
  provider_id_mappings.json            # Cross-provider team ID mappings (30 NBA teams)
  competitions.json                    # 9 competition definitions
  venue_timezones.json                 # Venue-to-IANA timezone lookup
  mollybet_competitions.json           # Mollybet competition ID mapping
  script_activity.json                 # Manual script run log

  nba_teams.json                       # 30 teams with metadata + aliases
  nba_players.json                     # 538 players
  nba_player_stats.json                # 535 players, 4 stat tiers
  nba_schedule.json                    # 2025-26 season schedule
  nba_team_stats.json                  # Team-level aggregates
  teamrankings_power_ratings_2026.json # Power ratings (30 teams)
  teamrankings_ats_trends_2026.json    # ATS records (30 teams)
  teamrankings_ou_trends_2026.json     # O/U trends (30 teams)

  fifa_wc_teams.json                   # 58 teams with full metadata
  fifa_wc_players.json                 # 1,300 players across 50 squads
  fifa_wc_schedule.json                # 104 matches with bracket labels

  lol_teams.json                       # 40 teams across 5 leagues
  lol_players.json                     # Empty (not yet populated)

  bref_advanced_2026_raw.json          # 721 players (pre-merge)
  bref_adj_shooting_2026_raw.json      # 728 players (pre-merge)
  bref_pbp_2026_raw.json              # 727 players (pre-merge)
```

### Scripts

```
scripts/
  seed_teams.py                        # Load teams + aliases into DB
  seed_players.py                      # Load players + aliases into DB
  seed_competitions.py                 # Load competitions into DB
  discover_mollybet_competitions.py    # WebSocket scan for competition IDs
  manual_reconcile.py                  # CLI for manual dedup + alias management

  fetch_nba_players.py                 # Fetch NBA player list from ESPN
  fetch_nba_player_stats.py            # Fetch per-game + advanced stats
  fetch_nba_schedule.py                # Fetch NBA schedule
  fetch_nba_team_metadata.py           # Enrich team metadata
  fetch_nba_team_stats.py              # Fetch team-level stats

  merge_bref_advanced.py               # Merge BRef advanced stats into player_stats
  merge_bref_pbp.py                    # Merge BRef play-by-play into player_stats
  merge_bref_adj_shooting.py           # Merge BRef adjusted shooting into player_stats

  enrich_from_reep.py                  # Cross-provider player aliases from Reep (23 providers)
```

### Schema Validation

```
data/schemas/
  providers.schema.json
  nba_players.schema.json
  nba_teams.schema.json
  competitions.schema.json
  provider_id_mappings.schema.json
```

---

## 11. Commit History

| Commit | Description |
|--------|-------------|
| `d373a11` | Initial commit + enable Claude Haiku LLM resolution |
| `f6fa1da` | Add FIFA WC 2026 opening match team and player data |
| `e015e0b` | Fix: start scheduler on app startup |
| `9547118` | Add Mollybet as ingestion adapter, confirmation source, and priority resolver |
| `2c620ee` | Fix: switch Mollybet adapter from REST to WebSocket event stream |
| `cfd8fac` | Comprehensive FIFA World Cup 2026 dataset (48 teams, 1,300 players, 104 matches) |
| `4d5f05a` | Fix: correct FIFA WC 2026 knockout bracket structure and add bracket labels |
| `3e7be9c` | Add country flags to all 58 FIFA WC teams |
| `4fde9e9` | Add Reep enrichment script for cross-provider player aliases |
| `999ab39` | Add LoL match data and project summary |
| `7bb80ba` | Comprehensive NBA data expansion -- rosters, schedule, and statistics |
| `c91a153` | Merge Basketball Reference advanced stats into player statistics |
| `2e27c0d` | Add centralized provider registry and cross-provider ID mappings |
| `b4599c0` | Five architectural improvements for consistency and quality |
| `4845702` | Add TeamRankings provider and BRef play-by-play/adjusted shooting support |
| `5a8f88b` | Merge BRef play-by-play and adjusted shooting into player stats |
| `d89b753` | Overhaul operational dashboard with provider registry, data inventory, and activity tracking |
