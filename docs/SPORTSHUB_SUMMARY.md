# Sportshub: Project Summary

## 1. Project Overview

Sportshub is a sports data aggregation platform that ingests upcoming event data from multiple independent sources, deduplicates and normalizes it into a single canonical dataset, and exposes it through a REST API with full data provenance.

### Problem

No single sports data source is both comprehensive and reliable. Official APIs have gaps, community sources have errors, and betting feeds use non-standard naming. A downstream consumer (frontend, analytics pipeline, or trading system) needs a single, high-confidence view of "what events are happening, when, and where" without worrying about source inconsistencies.

### Solution

Sportshub pulls from 11 upstream adapters across three sports, resolves entity mismatches (team names, competition labels, timezones) using alias tables and LLM fallback, scores each event by how many independent sources confirm it, and serves the result through a paginated API.

### Sports Covered

| Sport | Season | Adapters | Competitions |
|-------|--------|----------|--------------|
| **NBA** | 2025-26 | ESPN NBA, NBA.com CDN, BallDontLie, Mollybet | Regular Season, Playoffs |
| **League of Legends** | 2026 | LoLEsports, PandaScore, Liquipedia, Mollybet | LCK, LEC, LCS, MSI, Worlds |
| **Football (FIFA)** | WC 2026 | FIFA API, Football-Data.org, ESPN FIFA, Mollybet | FIFA World Cup 2026 |

---

## 2. Architecture

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Web framework | FastAPI (async) |
| Database | PostgreSQL 16 via SQLAlchemy Core (async) + asyncpg |
| Cache | Redis 7 with hiredis |
| Scheduling | APScheduler 3.x (AsyncIOScheduler) |
| HTTP client | httpx (async) |
| LLM | Anthropic Claude Haiku via `anthropic` SDK |
| WebSocket | `websockets` library (Mollybet stream) |
| Migrations | Alembic |
| Deployment | Railway (Docker) |

### Data Flow

```
                                  11 Source Adapters
                                  (ESPN, NBA.com, LoLEsports,
                                   PandaScore, Liquipedia,
                                   FIFA API, Football-Data,
                                   ESPN FIFA, Mollybet x3)
                                         |
                                         v
                                  +-------------+
                                  | Raw Events  |  --> source_records table
                                  +-------------+
                                         |
                                         v
                              +---------------------+
                              | Entity Resolution   |
                              |  1. Team lookup     |
                              |  2. Alias match     |
                              |  3. LLM fallback    |
                              |  4. Timezone norm   |
                              +---------------------+
                                         |
                                         v
                              +---------------------+
                              | Event Matching      |
                              |  Confidence scoring |
                              |  Merge or create    |
                              +---------------------+
                                         |
                           +-------------+-------------+
                           |                           |
                           v                           v
                  +----------------+          +------------------+
                  | Canonical      |          | Confirmation     |
                  | Events table   |          | (Mollybet, Odds) |
                  | (deduplicated) |          | Boosts confidence|
                  +----------------+          +------------------+
                           |
                           v
                  +----------------+
                  | Validation     |
                  | Sport-specific |
                  | constraints    |
                  +----------------+
                           |
                           v
                  +----------------+
                  | Reconciliation |
                  | Post-event     |
                  | accuracy check |
                  +----------------+
                           |
                           v
                  +----------------+
                  | REST API       |
                  | /api/v1/...    |
                  +----------------+
```

### Application Lifecycle

The FastAPI lifespan context manager (`main.py`) orchestrates startup and shutdown:

**Startup:**
1. Initialize async database engine and run migrations
2. Connect Redis client (graceful fallback if unavailable)
3. Build adapter registry (11 adapters, credential-guarded)
4. Build confirmation registry (Mollybet + OddsAPI)
5. Start APScheduler with all job cadences

**Shutdown:**
1. Stop scheduler
2. Shutdown all adapters (close HTTP/WS clients)
3. Shutdown confirmation sources
4. Close Redis and database connections

---

## 3. Core Subsystems

### 3.1 Ingestion

**11 adapters** inherit from `SourceAdapter` (abstract base in `ingestion/base.py`):

| Adapter | Source ID | Sport | Method | Cadence |
|---------|-----------|-------|--------|---------|
| ESPN NBA | `espn_nba` | NBA | REST | 1h |
| NBA.com CDN | `nbacom_cdn` | NBA | REST | 2h |
| BallDontLie | `balldontlie_nba` | NBA | REST | 6h |
| LoLEsports | `lolesports` | LoL | REST | 1h |
| PandaScore | `pandascore_lol` | LoL | REST | 2h |
| Liquipedia | `liquipedia_lol` | LoL | HTML scrape | Daily 08:00 |
| FIFA API | `fifa_api` | Football | REST | 2h |
| Football-Data | `footballdata_wc` | Football | REST | 3h |
| ESPN FIFA | `espn_fifa` | Football | REST | 1h |
| Mollybet (FB) | `mollybet_fb` | Football | WebSocket | 2h |
| Mollybet (NBA) | `mollybet_basket` | NBA | WebSocket | 2h |
| Mollybet (LoL) | `mollybet_esports` | LoL | WebSocket | 2h |

Each adapter's `fetch_upcoming()` returns a list of `RawEvent` with the source's raw team names, competition labels, and timestamps untouched. These are persisted as `source_records` for full provenance.

**Key files:**
- `src/sportshub/ingestion/base.py` -- SourceAdapter interface
- `src/sportshub/ingestion/registry.py` -- AdapterRegistry + factory
- `src/sportshub/ingestion/adapters/` -- All 10 adapter files

### 3.2 Entity Resolution

The resolution pipeline (`resolution/pipeline.py`) processes unmatched source records through these stages:

1. **Team Resolution** (`normalizer.py`): Normalize raw names (lowercase, strip accents, remove suffixes like "FC", "Esports", "National Team"), look up in alias cache. O(1) for cached aliases.

2. **LLM Fallback** (`llm_resolver.py`): When alias lookup fails, send the raw name + candidate list to Claude Haiku. If confidence >= 0.70, accept the match and auto-create an alias for future runs. Circuit breaker opens if acceptance rate drops below 40% or latency exceeds 10s.

3. **Event Matching** (`matcher.py`): Find candidate canonical events by sport + teams + time window (24h). Score confidence:
   - Team set match: +0.50
   - Home/away correct: +0.10
   - Time proximity: +0.05 to +0.40 (sliding scale)
   - Auto-match threshold: 0.70
   - Tentative (LLM promotion eligible): 0.50-0.70

4. **Merge or Create** (`merger.py`): If matched, merge source data into existing event (higher-priority sources override time/venue). If unmatched, create a new canonical event.

5. **Validation** (`validation/engine.py`): Run sport-specific constraints (e.g., NBA roster sizes, LoL match formats). Confidence reduced by 0.30 on error-level violations.

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
```

### Deduplication Strategy

Events are unique by the tuple `(sport, home_team_id, away_team_id, scheduled_at)`. When multiple sources report the same fixture, they are merged into a single canonical event with an incrementing `source_count` and the maximum `confidence_score` across sources. Each source's raw data is preserved in `source_records` for full auditability.

---

## 5. API Layer

**Base URL:** `/api/v1`
**Auth:** `X-API-Key` header (required for all endpoints except health)
**Deployment:** `https://joyful-peace-production-d7f2.up.railway.app`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | DB/Redis status, event counts (no auth) |
| `GET` | `/events` | Paginated event list with filters |
| `GET` | `/events/{id}` | Full event detail with all source records |
| `GET` | `/teams` | Paginated team list with fuzzy search |
| `GET` | `/teams/{id}/players` | Team roster |
| `GET` | `/players` | Player search |
| `GET` | `/competitions` | Competition list |

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

## 8. Testing and Lessons Learned

### WebSocket Message Format

**Problem:** Initial parser expected bare arrays `["event", {...}]`. Actual messages are batched: `{"ts": 1234.5, "data": [["event", {...}], ["event", {...}], ...]}`.

**Fix:** Updated parsing loop to unwrap the `data` array from the wrapper object.

**Lesson:** WebSocket APIs often batch messages for efficiency. Always capture and log a raw message sample before writing the parser.

### Bracket Structure Bugs

**Problem:** The initial FIFA WC 2026 knockout bracket had two critical bugs:
1. Groups E-L winners bypassed R32 entirely, going directly to R16 (R16-2 had "1st Group E vs 1st Group F")
2. R32 mixed runners-up vs ambiguous 3rd-placed team pools ("3rd Group C/D/E") that couldn't resolve to a single team

**Impact:** Bookmaker market references like "Winner of Playoff D" couldn't map to any R32 match because the bracket was structurally inconsistent.

**Fix:** Rewrote the entire knockout bracket:
- All 12 group winners play in R32 (paired with runners-up from mirrored groups)
- 8 best 3rd-placed teams play each other in 4 additional R32 matches
- Every R16 match references only R32 winners
- Added `bracket_label` (Playoff A-P) and `feeds_into` fields

**Lesson:** Tournament bracket structures are deceptively complex. The 48-team, 12-group format with 8 best 3rd-placed teams creates a non-trivial seeding matrix. Always validate the bracket by checking that every team appears exactly once and every later-round slot references exactly two feeder matches.

### Batch Data Management (1,300 Players)

**Problem:** Writing 1,300 player records required managing context window limits.

**Approach:** Split into 13 batch files (78-312 players each), wrote sequentially, then merged via Python script and deleted batch files.

**Lesson:** For large seed datasets, a batch-and-merge workflow keeps each write manageable. Version the merged file, not the batches.

### Competition Country Code

**Problem:** Mollybet reports `.f` as the `competition_country` for FIFA World Cup events, which isn't a valid ISO code.

**Fix:** Manually mapped to "XX" (international) in the seed file.

**Lesson:** Betting APIs use non-standard country codes for international competitions. Build a normalization layer rather than trusting raw values.

---

## 9. Production Considerations

The current implementation is a working prototype deployed on Railway. Below are the key areas to address for a production-grade system.

### 9.1 Secrets Management

**Current state:** Credentials in Railway environment variables and `.env` files.

**Production needs:**
- Use a secrets manager (AWS Secrets Manager, Vault, or Railway's encrypted variables)
- Rotate Mollybet session tokens programmatically (current 23h expiry is handled but not monitored)
- Audit log for credential access
- Separate credentials per environment (dev/staging/prod)

### 9.2 Database

**Current state:** Single PostgreSQL instance on Railway.

**Production needs:**
- Connection pooling via PgBouncer (current pool_size=10 is adequate for low traffic but won't scale)
- Read replicas for API queries (separate read/write connection strings)
- Automated backups and point-in-time recovery
- Index monitoring: the `events` table has good indexes but query plans should be reviewed under load
- Consider partitioning `source_records` by `created_at` (high-volume table)

### 9.3 Horizontal Scaling

**Current state:** Single container running both the API server and scheduler.

**Production needs:**
- Separate the **API** (stateless, horizontally scalable) from the **scheduler** (singleton)
- Use a distributed lock (Redis-based) to ensure only one scheduler instance runs ingestion jobs
- API workers: multiple uvicorn workers or multiple containers behind a load balancer
- Consider separating the resolution pipeline into a background worker process

### 9.4 Monitoring and Alerting

**Current state:** Structured logging via `structlog`. Basic health endpoint.

**Production needs:**
- Metrics export (Prometheus/Datadog): ingestion latency, resolution success rate, LLM token usage, event confidence distribution
- Alerting on: circuit breaker opens, ingestion job failures, resolution backlog growth, LLM acceptance rate drops
- Dashboard for source health: which adapters are healthy, last successful fetch, failure count
- Request tracing (OpenTelemetry) for API latency debugging
- Log aggregation (the existing structlog output is well-structured for tools like Datadog or Loki)

### 9.5 Error Recovery and Resilience

**Current state:** Circuit breaker pauses failing adapters. Redis failures are gracefully handled.

**Production needs:**
- Dead letter queue for permanently unresolvable source records (currently they stay in the resolution backlog forever)
- Retry with backoff for transient failures (currently adapters either succeed or fail with no retry)
- Graceful degradation: if the database is slow, the API should return cached responses rather than timing out
- Health endpoint should report degraded state (not just up/down) when adapters are circuit-broken

### 9.6 Data Freshness and Consistency

**Current state:** Ingestion runs on fixed intervals (1-6h depending on adapter).

**Production needs:**
- Event-driven ingestion for sources that support webhooks or push notifications
- Staleness monitoring: alert if an adapter hasn't produced new data in 2x its expected cadence
- Consistency checks: detect when source records conflict (e.g., two sources report different kickoff times for the same event)
- Deduplication edge cases: handle events that are rescheduled (same teams, different date) without creating duplicates

### 9.7 Test Coverage

**Current state:** Tests exist for the matcher and normalizer. Integration test structure is in place but coverage is limited.

**Production needs:**
- Unit tests for all adapters (mock HTTP/WebSocket responses)
- Integration tests for the full resolution pipeline (source record in, canonical event out)
- Contract tests for external APIs (detect when upstream response formats change)
- Load tests for the API layer (target: 100 concurrent users, p95 < 200ms)
- End-to-end test: seed data, run ingestion, run resolution, query API, verify results

### 9.8 CI/CD

**Current state:** Direct push to main, Railway auto-deploys.

**Production needs:**
- Branch protection: require PR reviews before merging to main
- CI pipeline: lint (ruff), type check (mypy), unit tests, integration tests
- Staging environment that mirrors production
- Database migration safety checks (no destructive migrations without manual approval)
- Canary deployments or blue/green for zero-downtime releases

### 9.9 Rate Limiting and API Security

**Current state:** Single API key for all consumers. No rate limiting on the API itself. Adapter-level rate limiting exists for OddsAPI.

**Production needs:**
- Per-consumer API keys with usage tracking
- Rate limiting on API endpoints (e.g., 100 req/min per key)
- Request validation and input sanitization (FastAPI handles most of this via Pydantic)
- CORS policy tightened from "allow all origins" to specific frontends
- API versioning strategy for breaking changes

### 9.10 Data Completeness

**Current state:** NBA and LoL player data files are empty placeholders. FIFA WC 2026 has full data.

**Production needs:**
- Populate NBA roster data (30 teams x 15 players = 450 players)
- Populate LoL roster data (50+ teams x 5-10 players = 250-500 players)
- Automated roster sync from upstream APIs (players transfer, rosters change mid-season)
- Handle mid-season competition changes (new tournaments, schedule modifications)

### 9.11 LLM Cost Control

**Current state:** Claude Haiku used for team/competition resolution fallback. Circuit breaker limits runaway usage.

**Production needs:**
- Token usage tracking and budget alerts (currently logged but not aggregated)
- Alias hit rate monitoring: as the alias table grows, LLM calls should decrease over time
- Model evaluation: periodically test if cheaper/faster models maintain acceptable accuracy
- Batch LLM resolution (group multiple unresolved entities into fewer API calls)
- Cost allocation by sport/adapter for budget planning

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
    adapters/                          # 10 adapter implementations
    confirmation/                      # Mollybet + OddsAPI confirmation
  resolution/
    pipeline.py                        # Full resolution orchestration
    normalizer.py                      # Team name normalization + alias lookup
    matcher.py                         # Confidence scoring + source priority
    merger.py                          # Event merge/create logic
    llm_resolver.py                    # Claude Haiku fallback
    reliability.py                     # Dynamic source scoring
    timezone.py                        # Venue timezone resolution
  scheduling/
    scheduler.py                       # APScheduler job registration
    jobs.py                            # Job implementations
    circuit_breaker.py                 # Adapter fault tolerance
  reconciliation/                      # Post-event verification
  validation/                          # Sport-specific constraints
  monitoring/                          # Logging, metrics, health
```

### Data Files

```
data/
  fifa_wc_schedule.json                # 104 matches with bracket labels
  fifa_wc_teams.json                   # 58 teams with full metadata
  fifa_wc_players.json                 # 1,300 players across 50 squads
  nba_teams.json                       # 30 NBA teams with aliases
  lol_teams.json                       # 50+ LoL teams with aliases
  nba_players.json                     # Placeholder (empty)
  lol_players.json                     # Placeholder (empty)
  competitions.json                    # 9 competition definitions
  mollybet_competitions.json           # Mollybet competition ID mapping
  venue_timezones.json                 # Venue-to-IANA timezone lookup
```

### Scripts

```
scripts/
  seed_teams.py                        # Load teams + aliases into DB
  seed_players.py                      # Load players + aliases into DB
  seed_competitions.py                 # Load competitions into DB
  discover_mollybet_competitions.py    # WebSocket scan for competition IDs
  manual_reconcile.py                  # CLI for manual dedup + alias management
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
