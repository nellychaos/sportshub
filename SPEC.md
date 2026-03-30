# Sportshub: Production-Grade Sports Data Aggregation Platform -- Complete Specification

## 1. System Overview and Goals

### The Problem

Sports data is fragmented. An NBA game between the Lakers and Celtics exists simultaneously as:

- A JSON blob on `cdn.nba.com` with team abbreviations "LAL" and "BOS" and an Eastern Time start
- A record on BallDontLie with numeric team IDs and UTC timestamps
- An ESPN event object with nested competition structures and ESPN-specific event IDs
- Potentially additional records on sportsbook feeds, analytics sites, and social APIs

Each source uses different identifiers, different schemas, different time zones, different team naming conventions, and different update cadences. A downstream consumer (a betting frontend, an analytics dashboard, an entertainment app) should not have to reconcile these differences. That is wasted, duplicated effort across every consumer.

**Sportshub solves this by being the single canonical source of truth for upcoming sports events**, consuming from multiple upstream sources, deduplicating, normalizing, and serving a clean, consistent API.

### MVP Scope

- **Sports**: NBA (traditional sports), League of Legends (esports), and the **2026 FIFA World Cup** (international soccer). These three represent fundamentally different data structures — fixed season schedules, online tournament brackets, and a large-scale international tournament with group stages and knockout rounds — proving the platform generalizes across diverse formats.
- **Data**: Upcoming/scheduled events only. Not live scores, not historical results.
- **Sources**: Free APIs and public endpoints only. No paid subscriptions, no restrictively licensed feeds.
- **Consumers**: REST API serving deduplicated canonical events with full provenance metadata.

### Future Vision (Post-MVP)

Live game data via WebSocket streaming, historical backfill, additional sports (NFL, MLB, Premier League, CS2, Valorant, Dota 2), expanded soccer coverage (club leagues, Champions League), odds/betting market data, ML-based quality scoring, multi-region deployment, event-driven push architecture.

### Key Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Event Coverage | 95%+ of actual NBA games, major LoL tournament matches, and all FIFA World Cup 2026 matches appear in our system | Cross-reference against official league schedules weekly |
| Dedup Accuracy | <1% false merges (two different events merged), <5% missed merges (same event as two records) | Manual audit of 100 random events monthly |
| Data Freshness | Events appear within 2 hours of source publication | Timestamp comparison: source publish time vs. our ingestion time |
| API Latency (p95) | <200ms for list endpoints, <100ms for detail endpoints | Application metrics |
| System Uptime | 99.5% (allows ~3.6 hours downtime/month) | Health check monitoring |
| Source Coverage Ratio | Each canonical event backed by 2+ sources | Database query on source_records per event |

---

## 2. Architecture

### Why a Layered Architecture?

Sportshub's core challenge is transforming messy, heterogeneous upstream data into clean, consistent downstream data. Each transformation step has different concerns, failure modes, and change frequencies. Coupling them would mean a change to one source's authentication breaks the dedup pipeline, or a new API endpoint requires modifying the ingestion scheduler. Layers enforce separation of concerns along natural fault boundaries.

### Layer 1: Ingestion

**Purpose**: Isolate all source-specific complexity (authentication, rate limiting, response parsing, error handling) behind a uniform interface. When we add a new source, we write one new adapter class. Nothing else changes.

**Why this separation exists**: Data sources are the most volatile part of the system. ESPN can change their undocumented API tomorrow. BallDontLie can change rate limits. The LoL Esports API can alter response schemas. By encapsulating each source in an adapter with a stable output contract, these changes are contained to a single file.

**Source Adapter Interface**:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator
from enum import Enum

class Sport(str, Enum):
    NBA = "nba"
    LOL = "lol"
    SOCCER = "soccer"

class SourceReliability(str, Enum):
    OFFICIAL = "official"      # League-operated source
    ESTABLISHED = "established" # Well-known third party with track record
    COMMUNITY = "community"    # Scraped / unofficial / community-maintained

@dataclass
class RawEvent:
    """The universal output of every adapter. Contains the raw fields
    exactly as the source provided them, plus metadata about the fetch."""
    source_id: str              # Adapter's unique name (e.g., "espn_nba")
    source_event_id: str        # The source's own ID for this event
    sport: Sport
    raw_home_team: str          # Team name exactly as source provides it
    raw_away_team: str          # Team name exactly as source provides it
    raw_competition: str        # League/tournament name as source provides it
    scheduled_at: datetime      # Always converted to UTC by the adapter
    venue: str | None = None
    raw_metadata: dict = field(default_factory=dict)  # Source-specific extras
    fetched_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class AdapterHealth:
    is_healthy: bool
    last_success: datetime | None
    last_error: str | None
    consecutive_failures: int

class SourceAdapter(ABC):
    """Every data source implements this interface."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this source, e.g. 'espn_nba'."""

    @property
    @abstractmethod
    def sport(self) -> Sport:
        """Which sport this adapter covers."""

    @property
    @abstractmethod
    def reliability(self) -> SourceReliability:
        """How much we trust this source's data."""

    @abstractmethod
    async def initialize(self) -> None:
        """Called once at startup. Validate credentials, warm caches, etc."""

    @abstractmethod
    async def fetch_upcoming(self) -> list[RawEvent]:
        """Fetch all upcoming events this source knows about.
        Must handle its own retries, rate limiting, and timeouts internally.
        Returns an empty list on total failure (after retries exhausted)."""

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        """Lightweight check that the source is reachable."""

    async def shutdown(self) -> None:
        """Called on graceful shutdown. Close connections, flush buffers."""
        pass
```

**Why every adapter handles its own rate limiting and retries**: Because rate limits and retry strategies differ fundamentally per source. ESPN has no documented limits but may block aggressive callers. BallDontLie has 5 req/min on free tier. PandaScore has 1000 req/hour. Centralizing this into a generic retry/rate-limit layer would require parameterizing every possible rate limiting scheme (per-minute, per-hour, sliding window, token bucket). It is simpler and more correct for each adapter to own its behavior.

**Circuit Breaking**: Each adapter maintains a failure counter. After 5 consecutive failures, the adapter enters a "circuit open" state and skips the next 3 scheduled runs before retrying. This prevents a downed source from consuming scheduler slots and log space.

### Layer 2: Normalization and Entity Resolution

**Purpose**: Transform the heterogeneous `RawEvent` objects from different adapters into canonical, deduplicated `Event` records. This is the intellectual core of the system.

**Why this separation exists**: Normalization logic is sport-specific and algorithmically complex. It changes when we discover new team name aliases or refine our matching heuristics. It must not be entangled with source-specific parsing (Layer 1) or storage mechanics (Layer 3).

**The Pipeline**:

```
RawEvent -> normalize_team_names() -> normalize_competition() ->
normalize_time() -> find_matching_canonical() -> merge_or_create()
```

Each step is a pure function (except the final database interaction), making them individually testable.

**Entity Resolution detail is in Section 5.**

### Layer 3: Storage

**Purpose**: Persist canonical events, source records, team aliases, and audit logs. Serve queries efficiently.

**Why this separation exists**: Storage technology choices (PostgreSQL, Redis) and schema design should not leak into business logic. If we later add a read replica, switch to CockroachDB, or add Elasticsearch for full-text search, only this layer changes.

**PostgreSQL schema design and index strategy detailed in Section 4.**

**Redis Cache Strategy**:

What goes in Redis:
- Serialized API response bodies for the most common queries: upcoming events by sport (3 keys: `events:nba:upcoming`, `events:lol:upcoming`, `events:soccer:upcoming`)
- Individual event detail by canonical ID: `event:{uuid}`
- Team and competition directory pages: `teams:all`, `competitions:all`

Why these specific items: These are the queries consumers will hit most frequently. The underlying data changes only when an ingestion run produces new or modified events, which happens on a known schedule (hourly to daily). Caching avoids repeated database queries for data that is identical across requests within a window.

Invalidation strategy: **Write-through invalidation**. When the normalization pipeline writes to PostgreSQL, it also deletes the affected cache keys. The next API request triggers a cache miss and repopulates. This is simpler than TTL-based expiry because our data changes on a known schedule (ingestion runs), not unpredictably.

Fallback: If Redis is unavailable, the API falls back to direct PostgreSQL queries. Redis is a performance optimization, not a correctness requirement.

### Layer 4: Scheduling and Operations

**Purpose**: Orchestrate periodic ingestion runs, dedup passes, cache maintenance, and health checks.

**Why this separation exists**: Scheduling concerns (cron expressions, job locking, failure handling) are orthogonal to what the jobs actually do. The scheduler should not know that the NBA adapter calls ESPN; it only knows "run this coroutine at this interval."

**Detail in Section 6.**

### Layer 5: API and Serving

**Purpose**: Expose canonical data to downstream consumers via REST.

**Why this separation exists**: API design (endpoint structure, pagination, authentication, rate limiting) is consumer-facing and changes based on consumer needs, not data internals. Decoupling it from storage means we can reshape responses, add fields, or version the API without touching the database or ingestion pipeline.

**Detail in Section 9.**

---

## 3. Data Sources -- MVP

### 3.1 NBA Source: ESPN Public API

**Adapter name**: `espn_nba`

**Base URL**: `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard`

**Authentication**: None. Completely open.

**Rate Limits**: Undocumented. Conservative approach: max 1 request per 5 seconds.

**What it provides**:
- Today's games with full team names, abbreviations, venue, broadcast info
- Game status (scheduled, in progress, final)
- Event IDs, competition IDs
- Date filtering via `?dates=YYYYMMDD`

**What it lacks**:
- Cannot query more than ~7 days ahead easily
- No official documentation or stability guarantee
- No historical schedule access beyond current season

**Exact endpoints to call**:
- Today's games: `GET /apis/site/v2/sports/basketball/nba/scoreboard`
- Specific date: `GET /apis/site/v2/sports/basketball/nba/scoreboard?dates=20260401`
- Team list: `GET /apis/site/v2/sports/basketball/nba/teams`

**Key response fields to extract**:
```
events[].id                          -> source_event_id
events[].name                        -> "Team A at Team B" (parse both teams)
events[].date                        -> ISO 8601 UTC timestamp
events[].competitions[].venue.fullName -> venue
events[].competitions[].competitors[].team.displayName -> full team name
events[].competitions[].competitors[].team.abbreviation -> e.g. "LAL"
events[].competitions[].competitors[].homeAway -> "home" or "away"
events[].season.slug                 -> competition context (e.g. "regular-season")
```

**Parsing notes**:
- The `events[].name` field uses format "Team A at Team B" -- the "at" team is the away team
- Dates are ISO 8601 with Z suffix (UTC)
- Competitors array always has exactly 2 entries; `homeAway` field distinguishes them

**Data quality**: HIGH. ESPN is the most widely used sports data source in the US. Team names are consistent, timestamps are accurate. However, since this is an undocumented API, we must handle unexpected schema changes gracefully.

**Licensing/ToS**: Undocumented API. No explicit terms. Standard practice is to use respectfully (low request volume, proper User-Agent header, no commercial redistribution of ESPN's editorial content). We extract only factual schedule data (not copyrightable).

**Reliability**: ESTABLISHED. Has been stable for years despite being unofficial.

### 3.2 NBA Source: BallDontLie API

**Adapter name**: `balldontlie_nba`

**Base URL**: `https://api.balldontlie.io/v1`

**Authentication**: API key via `Authorization` header. Free tier available at `app.balldontlie.io`.

**Rate Limits**: Free tier: 5 requests/minute. ALL-STAR tier ($9.99/mo): 60 req/min.

**What it provides**:
- Full season game schedule with filtering by date range, team, season
- Team directory with conference, division, city, name, abbreviation
- Game details including quarter scores
- Cursor-based pagination

**What it lacks**:
- Very tight rate limit on free tier (5/min)
- No venue information in the free tier
- Live data refreshed only every 10 minutes

**Exact endpoints to call**:
- Games list: `GET /v1/games?start_date=2026-03-26&end_date=2026-04-10&per_page=100`
- Teams list: `GET /v1/teams`
- Single game: `GET /v1/games/{id}`

**Key response fields**:
```
data[].id                -> source_event_id
data[].date              -> "YYYY-MM-DD" format (date only, no time)
data[].home_team.full_name -> e.g. "Los Angeles Lakers"
data[].visitor_team.full_name -> e.g. "Boston Celtics"
data[].home_team.abbreviation -> e.g. "LAL"
data[].season            -> integer year (e.g. 2025)
data[].postseason        -> boolean
```

**Parsing notes**:
- Date format is `YYYY-MM-DD` with NO time component. This is a significant limitation -- we cannot determine game start time from this source alone. BallDontLie serves as a confirmation source for event existence, not a primary source for start times.
- Pagination uses `meta.next_cursor` -- must follow cursor chain to get all results.

**Data quality**: MEDIUM. Accurate team data, but missing start times makes it supplementary.

**Licensing/ToS**: Commercial API with explicit free tier. Terms permit non-commercial use. Appropriate for MVP.

**Reliability**: ESTABLISHED. Well-maintained API with versioned endpoints.

### 3.3 NBA Source: NBA.com CDN

**Adapter name**: `nbacom_cdn`

**Base URL**: `https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json` (current season full schedule)

**Authentication**: None.

**Rate Limits**: None documented. CDN-served static file. Conservative: 1 request per 10 minutes.

**What it provides**:
- Complete season schedule as a single JSON file
- Game dates, times, team information, arena details, broadcast info
- Official NBA data -- highest authority

**What it lacks**:
- Single monolithic file (must parse entire season to find upcoming games)
- Schema undocumented and may change between seasons
- CDN caching means updates may lag by hours

**Key response fields**:
```
leagueSchedule.gameDates[].games[].gameId -> source_event_id
leagueSchedule.gameDates[].games[].gameDateTimeUTC -> full UTC datetime
leagueSchedule.gameDates[].games[].homeTeam.teamName -> e.g. "Lakers"
leagueSchedule.gameDates[].games[].homeTeam.teamTricode -> e.g. "LAL"
leagueSchedule.gameDates[].games[].awayTeam.teamName
leagueSchedule.gameDates[].games[].arenaName -> venue
leagueSchedule.gameDates[].games[].seriesText -> playoff context
```

**Parsing notes**:
- The JSON file is large (~2-5 MB for a full season). Parse once, extract upcoming games, discard the rest.
- `gameDateTimeUTC` is the most authoritative start time available.

**Data quality**: HIGHEST for NBA. This is the official league source.

**Licensing/ToS**: No explicit API terms. Public CDN content. Factual schedule data.

**Reliability**: OFFICIAL. Direct from the league.

### 3.4 LoL Source: LoL Esports API (Unofficial)

**Adapter name**: `lolesports`

**Base URL**: `https://esports-api.lolesports.com/persisted/gw`

**Authentication**: Static API key via `x-api-key` header: `0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z` (publicly known, embedded in the lolesports.com frontend).

**Rate Limits**: Undocumented. Conservative: 1 request per 5 seconds.

**What it provides**:
- Official esports schedule for all Riot-operated leagues (LCK, LPL, LEC, LCS, etc.)
- League, tournament, team, and match data
- Match status, scheduled times, best-of format
- Team logos and metadata

**What it lacks**:
- Not officially public (internal API used by lolesports.com frontend)
- No stability guarantees; Riot can change or remove endpoints at any time
- Does not cover third-party tournaments (e.g., Esports World Cup LoL segment)

**Exact endpoints to call**:
- Leagues: `GET /getLeagues?hl=en-US`
- Schedule: `GET /getSchedule?hl=en-US&leagueId={id}`
- Event details: `GET /getEventDetails?hl=en-US&id={matchId}`
- Teams: `GET /getTeams?hl=en-US&id={teamSlug}`
- Standings: `GET /getStandings?hl=en-US&tournamentId={id}`

**Key response fields (from getSchedule)**:
```
data.schedule.events[].match.id -> source_event_id
data.schedule.events[].startTime -> ISO 8601 UTC
data.schedule.events[].match.teams[].name -> team name
data.schedule.events[].match.teams[].code -> team abbreviation (e.g. "T1")
data.schedule.events[].match.strategy.type -> "bestOf"
data.schedule.events[].match.strategy.count -> 1, 3, or 5
data.schedule.events[].league.name -> e.g. "LCK"
data.schedule.events[].blockName -> e.g. "Week 5"
```

**Parsing notes**:
- The `hl` parameter controls locale. Use `en-US` for English team names.
- Schedule endpoint paginates via `pageToken` in the response.
- Match strategy indicates Bo1/Bo3/Bo5 -- important for understanding that a "match" may contain multiple games.

**Data quality**: HIGH for Riot-operated leagues. Official data, just accessed via an unofficial channel.

**Licensing/ToS**: Unofficial API. The key is public but usage is technically unsanctioned. Acceptable for MVP; should monitor for deprecation and plan migration to the LoL Esports Data Portal.

**Reliability**: OFFICIAL (data provenance), but COMMUNITY (access method).

### 3.5 LoL Source: PandaScore API

**Adapter name**: `pandascore_lol`

**Base URL**: `https://api.pandascore.co`

**Authentication**: Bearer token via `Authorization: Bearer {token}` header or `?token={token}` query parameter. Free account at pandascore.co.

**Rate Limits**: Free tier: 1,000 requests/hour.

**What it provides**:
- Comprehensive esports data across many titles, including LoL
- Upcoming matches, tournaments, teams, leagues
- Match format (Bo1/Bo3/Bo5), participants, scheduled times
- Structured REST API with proper documentation

**What it lacks**:
- Free tier limited to schedules, results, and context data
- Historical post-match statistics require paid plan (starting at 150 EUR/month)
- Real-time data requires 500 EUR/month plan

**Exact endpoints to call**:
- Upcoming matches: `GET /lol/matches/upcoming?per_page=100&page=1`
- Upcoming tournaments: `GET /lol/tournaments/upcoming`
- Teams: `GET /lol/teams?per_page=100`
- Leagues: `GET /lol/leagues`
- Single match: `GET /lol/matches/{match_id_or_slug}`

**Key response fields (from /lol/matches/upcoming)**:
```
[].id                    -> source_event_id (integer)
[].name                  -> match name (e.g. "T1 vs Gen.G")
[].scheduled_at          -> ISO 8601 UTC
[].opponents[].opponent.name -> team name
[].opponents[].opponent.acronym -> team abbreviation
[].tournament.name       -> tournament context
[].league.name           -> league name (e.g. "LCK")
[].number_of_games       -> total games in match (1, 3, or 5)
[].match_type            -> "best_of"
```

**Parsing notes**:
- Pagination via `X-Page`, `X-Per-Page`, `X-Total` response headers
- `opponents` array has exactly 2 entries for standard matches; may have 0 for TBD matches in bracket stages
- `scheduled_at` can be null for TBD schedule matches

**Data quality**: HIGH. PandaScore is a professional esports data provider. Well-structured, consistent data.

**Licensing/ToS**: Explicit free tier with clear terms. Non-betting usage explicitly permitted on free/stats plans.

**Reliability**: ESTABLISHED. Documented, versioned API.

### 3.6 LoL Source: Liquipedia (MediaWiki API)

**Adapter name**: `liquipedia_lol`

**Base URL**: `https://liquipedia.net/leagueoflegends/api.php`

**Authentication**: None required, but a descriptive `User-Agent` header is MANDATORY. Generic user agents are blocked.

**Rate Limits**: Strict -- 1 request per 2 seconds for normal endpoints, 1 request per 30 seconds for `action=parse`. Violations result in IP bans.

**What it provides**:
- The most comprehensive esports wiki in existence: 150k+ tournaments, 3M+ matches
- Tournament brackets, team rosters, match results
- Coverage of third-party tournaments that official APIs miss

**What it lacks**:
- MediaWiki API returns wikitext/HTML, not structured JSON. Requires parsing.
- Data structure varies between pages (community-edited wiki)
- Very strict rate limits make bulk ingestion slow
- Content licensed CC-BY-SA 3.0 (must attribute Liquipedia)

**Endpoints to call**:
- Parse tournament page: `GET /api.php?action=parse&page=LCK/2026_Season/Spring_Season&format=json`
- Search for pages: `GET /api.php?action=query&list=search&srsearch=LoL+2026+tournament&format=json`

**Parsing notes**:
- The response contains rendered HTML in `parse.text.*`. Must parse HTML tables to extract match schedules.
- Tournament page naming conventions are inconsistent (e.g., `LCK/2026_Season/Spring_Season` vs. `LEC/2026/Spring`).
- HTML table parsing is fragile -- wiki templates change.

**Data quality**: HIGH for breadth (covers everything), MEDIUM for structured extraction (requires HTML parsing).

**Licensing/ToS**: CC-BY-SA 3.0. Must attribute. API usage explicitly permitted with rate limit compliance.

**Reliability**: COMMUNITY. Content accuracy depends on wiki editors. Tournament schedules are generally accurate within hours of announcement.

**MVP Decision**: Use Liquipedia as a SUPPLEMENTARY source only for tournament context and team rosters. Do NOT rely on it for match schedules due to rate limits and parsing complexity. Primary LoL schedule data should come from LoL Esports API and PandaScore.

### 3.7 FIFA World Cup Source: ESPN Soccer API

**Adapter name**: `espn_fifa`

**Base URL**: `https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard`

**Authentication**: None. Same undocumented public API as ESPN NBA, just a different sport path.

**Rate Limits**: Undocumented. Conservative: max 1 request per 5 seconds.

**What it provides**:
- Match schedules for FIFA World Cup (group stage + knockout rounds)
- Full team names, abbreviations, venue info, broadcast data
- Match status, competition stage/group context
- Date filtering via `?dates=YYYYMMDD`

**What it lacks**:
- No official documentation or stability guarantee
- Limited lookahead (typically 7-14 days)
- May not have full schedule published until closer to tournament

**Key response fields**:
```
events[].id                          -> source_event_id
events[].date                        -> ISO 8601 UTC timestamp
events[].competitions[].venue.fullName -> venue
events[].competitions[].competitors[].team.displayName -> "United States", "Brazil"
events[].competitions[].competitors[].team.abbreviation -> "USA", "BRA"
events[].competitions[].competitors[].homeAway -> "home" or "away"
events[].competitions[].notes[].headline -> stage/group context
```

**Data quality**: HIGH. Consistent team naming, accurate timestamps.

**Reliability**: ESTABLISHED. ESPN soccer coverage is extensive and well-maintained.

### 3.8 FIFA World Cup Source: Football-Data.org API

**Adapter name**: `footballdata_wc`

**Base URL**: `https://api.football-data.org/v4`

**Authentication**: API key via `X-Auth-Token` header. Free tier at football-data.org/client/register.

**Rate Limits**: Free tier: 10 requests/minute. Sufficient for scheduled ingestion.

**What it provides**:
- Complete World Cup match schedule via `/competitions/WC/matches`
- Team details including FIFA codes (TLA), crests, country info
- Match status filtering (`SCHEDULED`, `LIVE`, `FINISHED`)
- Stage, group, and matchday metadata
- Venue information

**What it lacks**:
- Free tier limited to major competitions (World Cup is included)
- No broadcast information
- Venue data may be sparse until closer to tournament

**Exact endpoints**:
- Scheduled matches: `GET /v4/competitions/WC/matches?status=SCHEDULED`
- Competition info: `GET /v4/competitions/WC`
- Teams: `GET /v4/competitions/WC/teams`

**Key response fields**:
```
matches[].id                -> source_event_id
matches[].utcDate           -> ISO 8601 UTC timestamp
matches[].homeTeam.name     -> "Brazil", "Germany"
matches[].homeTeam.tla      -> "BRA", "GER" (FIFA 3-letter code)
matches[].awayTeam.name     -> full team name
matches[].stage             -> "GROUP_STAGE", "ROUND_OF_32", etc.
matches[].group             -> "GROUP_A", "GROUP_B", etc.
matches[].matchday          -> integer
matches[].venue             -> venue name string
```

**Data quality**: HIGH. Well-structured, uses FIFA standard team codes, reliable timestamps.

**Licensing/ToS**: Free tier for non-commercial use. Terms permit reasonable usage with attribution.

**Reliability**: ESTABLISHED. Well-documented API with versioned endpoints and stable schemas.

### 3.9 FIFA World Cup Source: FIFA Official API

**Adapter name**: `fifa_api`

**Base URL**: `https://api.fifa.com/api/v3`

**Authentication**: None. Public API used by FIFA+ web application.

**Rate Limits**: Not documented. Conservative: 1 request per 3 seconds.

**What it provides**:
- Official match schedules directly from FIFA
- Complete venue/stadium details (name, city, capacity)
- Match numbers, stage information, group assignments
- Team names in multiple languages
- Most authoritative source for World Cup data

**What it lacks**:
- Undocumented API (reverse-engineered from FIFA+ web app)
- Complex response format with nested localized strings
- Date format may use `/Date(millis)/` Microsoft JSON format
- Schema may change without notice

**Key response fields**:
```
Results[].IdMatch                  -> source_event_id
Results[].Date                     -> /Date(millis)/ or ISO 8601
Results[].Home.TeamName[]          -> localized team names
Results[].Away.TeamName[]          -> localized team names
Results[].Home.Abbreviation        -> "USA", "BRA"
Results[].Stadium.Name[]           -> localized venue names
Results[].StageName[]              -> localized stage names
Results[].GroupName[]              -> localized group names
Results[].MatchNumber              -> sequential match number
```

**Parsing notes**:
- Team names and venue names use a localized array format: `[{"Locale": "en", "Description": "Brazil"}, ...]`. Must extract English locale.
- Dates may use Microsoft JSON date format `/Date(1718100000000)/` — requires special parsing.
- Match status uses numeric codes (0/1 = scheduled).

**Data quality**: HIGHEST. This is the official source of truth for FIFA World Cup matches.

**Reliability**: OFFICIAL. Direct from FIFA, though API is undocumented and may change.

**Source priority (soccer)**: `fifa_api` (10) > `footballdata_wc` (8) > `espn_fifa` (7)

---

## 4. Data Model

### Core Entities and Their Relationships

```
Competition (1) ──< (N) Event (N) >── (1) Team (home)
                            │                 │
                            │          Team (away)
                            │
                     SourceRecord (N) >── (1) Event
                            │
                     IngestionRun (1) ──< (N) SourceRecord

Team (1) ──< (N) TeamAlias
Team (1) ──< (N) Player (1) ──< (N) PlayerAlias
```

### Python / Pydantic Models

```python
import uuid
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class Sport(str, Enum):
    NBA = "nba"
    LOL = "lol"
    SOCCER = "soccer"


class EventStatus(str, Enum):
    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    # Future: IN_PROGRESS = "in_progress", COMPLETED = "completed"


class MatchFormat(str, Enum):
    SINGLE = "single"    # Standard NBA game, LoL Bo1
    BEST_OF_3 = "bo3"
    BEST_OF_5 = "bo5"
    BEST_OF_7 = "bo7"    # NBA playoffs


class Team(BaseModel):
    """Canonical team entity. One record per real-world team."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str                    # "Los Angeles Lakers"
    short_name: str              # "Lakers"
    abbreviation: str            # "LAL"
    sport: Sport
    active: bool = True
    metadata: dict = Field(default_factory=dict)
    # metadata examples: {"conference": "Western", "division": "Pacific",
    #                      "logo_url": "...", "founded": 1947}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TeamAlias(BaseModel):
    """Maps a source-specific team name to a canonical team."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    team_id: uuid.UUID           # FK to Team
    alias: str                   # "LA Lakers", "L.A. Lakers", "LAL"
    source_id: str               # Which source uses this alias
    is_primary: bool = False     # Is this the source's preferred name?


class Player(BaseModel):
    """Canonical player entity. One record per real-world player."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    team_id: uuid.UUID | None = None  # FK to Team (null if free agent)
    name: str                    # "LeBron James" or "Faker"
    sport: Sport
    position: str | None = None  # NBA: "PG", "SG", "SF", "PF", "C"
                                 # LoL: "Top", "Jungle", "Mid", "Bot", "Support"
    role: str | None = None      # LoL-specific: "Captain", "Sub"
                                 # NBA: "Starter", "Bench"
    jersey_number: str | None = None  # NBA only
    nationality: str | None = None
    active: bool = True
    metadata: dict = Field(default_factory=dict)
    # metadata examples:
    #   NBA: {"height_cm": 206, "weight_kg": 113, "draft_year": 2003}
    #   LoL: {"summoner_name": "Faker", "champion_pool": ["Azir", "LeBlanc"]}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PlayerAlias(BaseModel):
    """Maps a source-specific player name to a canonical player.

    Critical for LoL where players are known by gamer tags that change,
    and for NBA where sources vary (e.g., "LeBron James" vs "L. James").
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    player_id: uuid.UUID         # FK to Player
    alias: str                   # "Faker", "Hide on Bush", "L. James"
    source_id: str               # Which source uses this alias
    is_primary: bool = False


class Competition(BaseModel):
    """A league or tournament. NBA Regular Season, LCK Spring 2026, etc."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str                    # "NBA 2025-26 Regular Season"
    short_name: str              # "NBA Regular Season"
    sport: Sport
    season: str | None = None    # "2025-26" or "Spring 2026"
    region: str | None = None    # "North America", "Korea", "Global"
    tier: str | None = None      # "major", "minor", "qualifier"
    metadata: dict = Field(default_factory=dict)
    start_date: datetime | None = None
    end_date: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Event(BaseModel):
    """The canonical, deduplicated event. THE core entity."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    sport: Sport
    competition_id: uuid.UUID    # FK to Competition
    home_team_id: uuid.UUID      # FK to Team
    away_team_id: uuid.UUID      # FK to Team
    scheduled_at: datetime       # UTC
    status: EventStatus = EventStatus.SCHEDULED
    match_format: MatchFormat = MatchFormat.SINGLE
    venue: str | None = None
    confidence_score: float = 1.0  # 0.0-1.0, how confident in dedup
    source_count: int = 1        # How many sources confirm this event
    metadata: dict = Field(default_factory=dict)
    # metadata examples: {"broadcast": ["ESPN", "TNT"],
    #                      "block_name": "Week 5", "series_text": "Game 3"}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SourceRecord(BaseModel):
    """A single source's version of an event. Preserves raw provenance."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_id: uuid.UUID | None = None  # FK to Event (null if unmatched)
    source_id: str               # "espn_nba", "pandascore_lol", etc.
    source_event_id: str         # The source's own ID
    sport: Sport
    raw_home_team: str
    raw_away_team: str
    raw_competition: str
    scheduled_at: datetime       # UTC, as reported by this source
    venue: str | None = None
    raw_data: dict = Field(default_factory=dict)  # Full raw response
    match_confidence: float | None = None  # Confidence of match to Event
    ingestion_run_id: uuid.UUID  # FK to IngestionRun
    created_at: datetime = Field(default_factory=datetime.utcnow)


class IngestionRun(BaseModel):
    """Audit log of each fetch from a source."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str                  # "success", "partial", "failed"
    records_fetched: int = 0
    records_new: int = 0
    records_updated: int = 0
    records_matched: int = 0     # Matched to canonical events
    error_message: str | None = None
    metadata: dict = Field(default_factory=dict)
```

### PostgreSQL Schema

```sql
-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For fuzzy text matching

-- ============================================================
-- TEAMS
-- ============================================================
CREATE TABLE teams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(200) NOT NULL,
    short_name VARCHAR(100) NOT NULL,
    abbreviation VARCHAR(10) NOT NULL,
    sport VARCHAR(20) NOT NULL CHECK (sport IN ('nba', 'lol', 'soccer')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(abbreviation, sport)
);

CREATE INDEX idx_teams_sport ON teams(sport);
CREATE INDEX idx_teams_sport_active ON teams(sport) WHERE active = TRUE;
CREATE INDEX idx_teams_name_trgm ON teams USING gin(name gin_trgm_ops);

-- ============================================================
-- TEAM ALIASES
-- ============================================================
CREATE TABLE team_aliases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    alias VARCHAR(200) NOT NULL,
    alias_normalized VARCHAR(200) NOT NULL,  -- lowercased, stripped
    source_id VARCHAR(50) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    
    UNIQUE(alias_normalized, source_id)
);

CREATE INDEX idx_team_aliases_normalized ON team_aliases(alias_normalized);
CREATE INDEX idx_team_aliases_team_id ON team_aliases(team_id);
-- Enable fuzzy matching on team aliases
CREATE INDEX idx_team_aliases_trgm ON team_aliases USING gin(alias_normalized gin_trgm_ops);

-- ============================================================
-- PLAYERS
-- ============================================================
CREATE TABLE players (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    team_id UUID REFERENCES teams(id) ON DELETE SET NULL,
    name VARCHAR(200) NOT NULL,
    sport VARCHAR(20) NOT NULL CHECK (sport IN ('nba', 'lol', 'soccer')),
    position VARCHAR(50),          -- NBA: "PG","SG","SF","PF","C"
                                   -- LoL: "Top","Jungle","Mid","Bot","Support"
    role VARCHAR(50),              -- "Starter","Bench","Sub","Captain"
    jersey_number VARCHAR(10),     -- NBA only
    nationality VARCHAR(100),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(sport, name, team_id)
);

CREATE INDEX idx_players_team ON players(team_id);
CREATE INDEX idx_players_sport ON players(sport);
CREATE INDEX idx_players_sport_active ON players(sport) WHERE active = TRUE;
CREATE INDEX idx_players_metadata ON players USING gin(metadata);

-- ============================================================
-- PLAYER ALIASES
-- ============================================================
CREATE TABLE player_aliases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    alias VARCHAR(200) NOT NULL,
    alias_normalized VARCHAR(200) NOT NULL,  -- lowercased, stripped
    source_id VARCHAR(50) NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,

    UNIQUE(alias_normalized, source_id)
);

CREATE INDEX idx_player_aliases_normalized ON player_aliases(alias_normalized);
CREATE INDEX idx_player_aliases_player_id ON player_aliases(player_id);
-- Enable fuzzy matching on player aliases (gamer tags, name variants)
CREATE INDEX idx_player_aliases_trgm ON player_aliases USING gin(alias_normalized gin_trgm_ops);

-- ============================================================
-- COMPETITIONS
-- ============================================================
CREATE TABLE competitions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(300) NOT NULL,
    short_name VARCHAR(100) NOT NULL,
    sport VARCHAR(20) NOT NULL CHECK (sport IN ('nba', 'lol', 'soccer')),
    season VARCHAR(50),
    region VARCHAR(100),
    tier VARCHAR(20),
    metadata JSONB NOT NULL DEFAULT '{}',
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_competitions_sport ON competitions(sport);

-- ============================================================
-- EVENTS (the core table)
-- ============================================================
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sport VARCHAR(20) NOT NULL CHECK (sport IN ('nba', 'lol', 'soccer')),
    competition_id UUID NOT NULL REFERENCES competitions(id),
    home_team_id UUID NOT NULL REFERENCES teams(id),
    away_team_id UUID NOT NULL REFERENCES teams(id),
    scheduled_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'postponed', 'cancelled')),
    match_format VARCHAR(10) NOT NULL DEFAULT 'single'
        CHECK (match_format IN ('single', 'bo3', 'bo5', 'bo7')),
    venue VARCHAR(300),
    confidence_score FLOAT NOT NULL DEFAULT 1.0
        CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
    source_count INTEGER NOT NULL DEFAULT 1,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Prevent exact duplicates at the DB level
    UNIQUE(sport, home_team_id, away_team_id, scheduled_at)
);

-- PRIMARY QUERY PATTERN: upcoming events by sport, sorted by time
CREATE INDEX idx_events_sport_scheduled ON events(sport, scheduled_at)
    WHERE status = 'scheduled';

-- Query by team (either home or away)
CREATE INDEX idx_events_home_team ON events(home_team_id, scheduled_at);
CREATE INDEX idx_events_away_team ON events(away_team_id, scheduled_at);

-- Query by competition
CREATE INDEX idx_events_competition ON events(competition_id, scheduled_at);

-- Query by date range (most common consumer query)
CREATE INDEX idx_events_scheduled_at ON events(scheduled_at)
    WHERE status = 'scheduled';

-- Composite for the full filter stack
CREATE INDEX idx_events_sport_status_scheduled 
    ON events(sport, status, scheduled_at);

-- ============================================================
-- SOURCE RECORDS (provenance tracking)
-- ============================================================
CREATE TABLE source_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id UUID REFERENCES events(id) ON DELETE SET NULL,
    source_id VARCHAR(50) NOT NULL,
    source_event_id VARCHAR(200) NOT NULL,
    sport VARCHAR(20) NOT NULL CHECK (sport IN ('nba', 'lol', 'soccer')),
    raw_home_team VARCHAR(200) NOT NULL,
    raw_away_team VARCHAR(200) NOT NULL,
    raw_competition VARCHAR(200) NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    venue VARCHAR(300),
    raw_data JSONB NOT NULL DEFAULT '{}',
    match_confidence FLOAT,
    ingestion_run_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Each source can only have one record per source-specific event ID
    UNIQUE(source_id, source_event_id)
);

CREATE INDEX idx_source_records_event_id ON source_records(event_id);
CREATE INDEX idx_source_records_source ON source_records(source_id, created_at);
CREATE INDEX idx_source_records_unmatched ON source_records(sport, created_at)
    WHERE event_id IS NULL;

-- ============================================================
-- INGESTION RUNS (audit log)
-- ============================================================
CREATE TABLE ingestion_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    records_fetched INTEGER NOT NULL DEFAULT 0,
    records_new INTEGER NOT NULL DEFAULT 0,
    records_updated INTEGER NOT NULL DEFAULT 0,
    records_matched INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_ingestion_runs_source ON ingestion_runs(source_id, started_at);

-- ============================================================
-- TRIGGER: auto-update updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_events_updated_at 
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_teams_updated_at 
    BEFORE UPDATE ON teams
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_competitions_updated_at 
    BEFORE UPDATE ON competitions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

**Index Strategy Justification**:

The most common consumer query is "give me upcoming NBA games this week, sorted by start time." That query filters on `sport = 'nba'`, `status = 'scheduled'`, and `scheduled_at >= NOW()`, sorted by `scheduled_at ASC`. The partial index `idx_events_sport_scheduled` is purpose-built for this: it only indexes scheduled events (the vast majority of queries), covering both the sport filter and the sort order in a single B-tree scan.

The team indexes (`idx_events_home_team`, `idx_events_away_team`) support "give me all upcoming Lakers games" -- a consumer queries by team_id. We need two separate indexes because a team can be home or away. The application layer UNIONs or ORs these.

The `UNIQUE(sport, home_team_id, away_team_id, scheduled_at)` constraint is a database-level safety net against exact duplicates. It will not catch near-duplicates (e.g., same game with 1-minute time difference between sources), which is why we need the algorithmic entity resolution in Layer 2.

---

## 5. Entity Resolution Algorithm

### Step 1: Team Name Normalization

```python
import unicodedata
import re

# Preloaded from team_aliases table at startup
ALIAS_CACHE: dict[str, uuid.UUID] = {}  # normalized_alias -> team_id

def normalize_team_name(raw_name: str) -> str:
    """Reduce a team name to a canonical form for matching."""
    name = raw_name.strip().lower()
    # Remove accents: "Geneve" == "Geneve"
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if not unicodedata.combining(c))
    # Remove common suffixes/prefixes that vary between sources
    name = re.sub(r'\b(esports?|gaming|team|club|fc|sc)\b', '', name)
    # Remove punctuation and extra whitespace
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def resolve_team(raw_name: str, source_id: str, sport: Sport) -> uuid.UUID | None:
    """Look up the canonical team for a raw team name.
    
    Priority:
    1. Exact match in alias table for this specific source
    2. Exact match in alias table for any source
    3. Fuzzy match against all known aliases (pg_trgm similarity > 0.6)
    4. None (unresolved -- flag for manual review)
    """
    normalized = normalize_team_name(raw_name)
    
    # Check source-specific alias first
    key = f"{normalized}:{source_id}"
    if key in ALIAS_CACHE:
        return ALIAS_CACHE[key]
    
    # Check global alias
    if normalized in ALIAS_CACHE:
        return ALIAS_CACHE[normalized]
    
    # Fuzzy match falls through to database query with pg_trgm
    return None  # Triggers manual review queue
```

**NBA-specific normalizations**:
- "LA Lakers" / "L.A. Lakers" / "Los Angeles Lakers" / "LAL" all map to the same canonical team
- 30 NBA teams are fixed and known. Pre-seed the alias table with all known variants.

**LoL-specific normalizations**:
- Team names change more frequently (orgs rebrand, regional leagues shuffle)
- Abbreviation-based matching is more important: "T1", "GEN" (Gen.G), "BLG" (Bilibili Gaming)
- Some sources use "Gen.G Esports" while others use "Gen.G" or "GENG"
- Pre-seed with current rosters from each league; update when new teams appear

### Step 2: Event Matching

Given a new `SourceRecord`, determine if it matches an existing canonical `Event`.

```python
from datetime import timedelta

# Per-source priority ranking for field conflicts
SOURCE_PRIORITY = {
    # NBA: official NBA CDN > ESPN > BallDontLie
    "nbacom_cdn": 10,
    "espn_nba": 8,
    "balldontlie_nba": 5,
    # LoL: LoL Esports (official) > PandaScore > Liquipedia
    "lolesports": 10,
    "pandascore_lol": 8,
    "liquipedia_lol": 5,
}

async def find_matching_event(
    record: SourceRecord,
    existing_events: list[Event],
) -> tuple[Event | None, float]:
    """Find the canonical event this source record corresponds to.
    
    Returns (matched_event, confidence_score) or (None, 0.0).
    """
    home_team_id = resolve_team(record.raw_home_team, record.source_id, record.sport)
    away_team_id = resolve_team(record.raw_away_team, record.source_id, record.sport)
    
    if home_team_id is None or away_team_id is None:
        return None, 0.0  # Can't match without resolved teams
    
    best_match: Event | None = None
    best_score: float = 0.0
    
    for event in existing_events:
        score = 0.0
        
        # Team matching (required -- both teams must match)
        teams_match = (
            {event.home_team_id, event.away_team_id} == 
            {home_team_id, away_team_id}
        )
        if not teams_match:
            continue
        
        score += 0.5  # Teams match is worth half the confidence
        
        # Home/away assignment matches
        if (event.home_team_id == home_team_id and 
            event.away_team_id == away_team_id):
            score += 0.1  # Bonus for matching home/away assignment
        
        # Time proximity scoring
        time_diff = abs(
            (event.scheduled_at - record.scheduled_at).total_seconds()
        )
        if time_diff == 0:
            score += 0.4
        elif time_diff <= 900:      # 15 minutes
            score += 0.35
        elif time_diff <= 3600:     # 1 hour
            score += 0.25
        elif time_diff <= 7200:     # 2 hours
            score += 0.10
        elif time_diff <= 86400:    # 24 hours (same day, different time)
            score += 0.05
        else:
            continue  # More than 24 hours apart -- not the same event
        
        if score > best_score:
            best_score = score
            best_match = event
    
    # Threshold: we need at least 0.70 confidence to auto-match
    if best_score >= 0.70:
        return best_match, best_score
    elif best_score >= 0.50:
        # Log for manual review but still match tentatively
        return best_match, best_score
    else:
        return None, 0.0
```

### Step 3: Confidence Scoring Breakdown

| Factor | Score Contribution | Notes |
|---|---|---|
| Both teams match (set equality) | +0.50 | Required. If teams don't match, skip. |
| Home/away assignment matches | +0.10 | Some sources may swap home/away |
| Exact time match (0 diff) | +0.40 | Maximum time score |
| Within 15 minutes | +0.35 | Source clock skew, timezone rounding |
| Within 1 hour | +0.25 | Possible schedule update not yet propagated |
| Within 2 hours | +0.10 | Suspicious, likely same event but flag it |
| Within 24 hours | +0.05 | Same-day doubleheader risk (NBA) |
| Beyond 24 hours | skip | Different event |

**Auto-match threshold**: 0.70 (teams match + time within 1 hour)
**Manual review threshold**: 0.50-0.69 (teams match but time discrepancy > 1 hour)
**No match**: below 0.50

### Step 4: Conflict Resolution (Merge Strategy)

When multiple sources provide data for the same event, conflicts are resolved by source priority ranking.

```python
async def merge_source_into_event(
    event: Event,
    record: SourceRecord,
    confidence: float,
) -> Event:
    """Update the canonical event with data from a new source record.
    
    Higher-priority sources override lower-priority sources.
    """
    source_priority = SOURCE_PRIORITY.get(record.source_id, 0)
    
    # Find the current highest-priority source for this event
    existing_records = await get_source_records_for_event(event.id)
    current_max_priority = max(
        SOURCE_PRIORITY.get(r.source_id, 0) for r in existing_records
    ) if existing_records else 0
    
    if source_priority >= current_max_priority:
        # This source wins -- update canonical fields
        event.scheduled_at = record.scheduled_at
        if record.venue:
            event.venue = record.venue
    
    # Always update aggregate fields regardless of priority
    event.source_count = len(existing_records) + 1
    event.confidence_score = max(event.confidence_score, confidence)
    event.updated_at = datetime.utcnow()
    
    return event
```

**Field-level priority rules**:
- `scheduled_at`: Highest-priority source wins. NBA CDN is the authority for NBA start times. LoL Esports API is the authority for LoL match times.
- `venue`: Take from any source that provides it. NBA CDN and ESPN both provide arena names. LoL matches are online (venue is the tournament/platform).
- `status`: ANY source reporting `postponed` or `cancelled` triggers an alert for manual verification. Do not auto-cancel based on a single source.
- `match_format`: Take from the most specific source. PandaScore and LoL Esports API both provide Bo1/Bo3/Bo5 for LoL.

### Step 5: LoL-Specific Challenges

**Team name variance**: LoL team names vary more than NBA because:
- Orgs use different formats: "T1" vs "T1 LoL" vs "SK Telecom T1"
- Regional leagues may prefix with region: "LCK T1"
- PandaScore uses `opponents[].opponent.acronym` while LoL Esports uses `teams[].code`

**Solution**: Maintain a richer alias table for LoL teams, updated quarterly or when a new team appears in ingestion that cannot be resolved.

**Bo3/Bo5 series vs. individual maps**: A "match" in LoL esports is a Bo3 or Bo5 series containing multiple games. The canonical `Event` represents the series, not individual games. If a source provides game-level data, aggregate to the series level.

**TBD matches**: Tournament brackets often have matches with "TBD" or null participants. Store these as source records but do NOT create canonical events until both participants are known.

---

## 6. Operational Schedule

### Ingestion Cadences

| Job | Cadence | Justification |
|---|---|---|
| `espn_nba` fetch | Every 1 hour | ESPN updates frequently; no auth means low cost. Primary NBA schedule source after CDN. Hourly is sufficient for upcoming event schedules. |
| `nbacom_cdn` fetch | Every 2 hours | Static CDN file; changes infrequently. Heavyweight fetch (full season JSON). |
| `balldontlie_nba` fetch | Every 6 hours | 5 req/min rate limit. Schedule data is stable; 4 fetches/day is sufficient. Conserves free tier quota. |
| `lolesports` fetch | Every 1 hour | Primary LoL source. Schedule changes happen when matches conclude or get rescheduled. |
| `pandascore_lol` fetch | Every 2 hours | 1000 req/hr limit. Each run uses ~5 requests. Schedule data doesn't change faster than this. |
| `liquipedia_lol` fetch | Daily at 08:00 UTC | Strict rate limits (1 req/2s). Only for supplementary tournament context, not match schedules. |
| Entity resolution pass | Every 1 hour | Process unmatched source records. Runs after ingestion jobs complete. |
| Cache invalidation sweep | After every ingestion run | Write-through: delete affected cache keys post-ingestion. |
| Health check (all sources) | Every 1 hour | Lightweight HEAD or minimal GET to verify source reachability. Runs alongside ingestion. |
| Player roster sync | Daily at 06:00 UTC | Sync player rosters from BallDontLie (NBA) and PandaScore (LoL). Roster changes are infrequent. |
| Stale event cleanup | Daily at 03:00 UTC | Mark events with `scheduled_at` in the past as stale (future: transition to COMPLETED). |

### Job Failure Handling

```python
# Retry policy per job type
RETRY_POLICIES = {
    "ingestion": {
        "max_retries": 3,
        "backoff_base": 30,      # seconds
        "backoff_multiplier": 2, # 30s, 60s, 120s
        "dead_letter_after": 3,  # Log to dead letter table after 3 failures
    },
    "entity_resolution": {
        "max_retries": 1,        # Idempotent, just retry next cycle
        "backoff_base": 10,
        "backoff_multiplier": 1,
        "dead_letter_after": 5,
    },
}
```

**Circuit breaker**: After 5 consecutive failures for a source, pause that source's ingestion for 1 hour. Log an alert. Resume automatically and attempt one fetch. If it fails again, extend pause to 4 hours.

### Health Monitoring and Alerting Thresholds

| Metric | Warning Threshold | Critical Threshold |
|---|---|---|
| Source consecutive failures | 3 | 5 (circuit breaker trips) |
| Time since last successful ingestion (per source) | 2x cadence | 4x cadence |
| Unmatched source records older than 4 hours | 10 records | 50 records |
| API response latency p95 | 500ms | 2000ms |
| Database connection pool utilization | 70% | 90% |
| Redis cache hit ratio | Below 60% | Below 30% |

### Data Freshness SLAs

| Metric | Target |
|---|---|
| New event appears in API after source publishes | Within 2 hours (next ingestion + resolution cycle) |
| Schedule change (time moved) reflected in API | Within 2 hours |
| Event cancellation reflected in API | Within 4 hours (requires multi-source confirmation) |

---

## 7. Cost Analysis

### Monthly MVP Cost Breakdown

| Component | Provider | Cost | Notes |
|---|---|---|---|
| **Compute** | Single VPS (4 vCPU, 8 GB RAM) | $24-48/mo | DigitalOcean or Hetzner. Runs API, scheduler, all adapters. |
| **PostgreSQL** | Same VPS (self-hosted) or managed | $0 (self-hosted) / $15 (managed) | Self-hosted for MVP. ~500 MB data for first year. |
| **Redis** | Same VPS (self-hosted) | $0 | <100 MB memory usage for cache. |
| **Domain + TLS** | Cloudflare (free tier) | $10-15/yr for domain | Free TLS, DNS, basic DDoS protection. |
| **API Sources** | | | |
| - ESPN | Free, no auth | $0 | |
| - NBA CDN | Free, no auth | $0 | |
| - BallDontLie | Free tier (5 req/min) | $0 | Consider ALL-STAR ($9.99/mo) if hitting limits |
| - LoL Esports | Free, public key | $0 | |
| - PandaScore | Free tier (1000 req/hr) | $0 | |
| - Liquipedia | Free, rate-limited | $0 | |
| **Monitoring** | Uptime Robot (free) + structured logging | $0 | Free tier: 50 monitors, 5-min checks |
| **Total MVP** | | **$24-63/mo** | |

### Growth Path Pricing

| Scale Point | Changes | Additional Cost |
|---|---|---|
| 5 sports, 10 sources | Larger VPS (8 CPU, 16 GB) | +$30-50/mo |
| Live data + WebSocket | Separate worker process, more Redis | +$20/mo |
| 10+ sports, high traffic | Managed PostgreSQL, dedicated Redis, load balancer | +$100-200/mo |
| Production betting backend | BallDontLie ALL-STAR, PandaScore Historical tier | +$160/mo APIs |
| Enterprise scale | Kubernetes, multi-region, CDN | $500-2000/mo |

---

## 8. Data Quality Framework

### Source Reliability Tracking

Each source maintains a rolling reliability score based on the last 30 days of ingestion runs.

```python
def calculate_source_reliability(source_id: str, runs: list[IngestionRun]) -> float:
    """0.0 to 1.0 reliability score for a source over recent history."""
    if not runs:
        return 0.0
    
    successful = sum(1 for r in runs if r.status == "success")
    total = len(runs)
    
    # Base score: success rate
    base = successful / total
    
    # Penalty for recent failures (last 3 runs weighted higher)
    recent = runs[-3:]
    recent_failures = sum(1 for r in recent if r.status != "success")
    recency_penalty = recent_failures * 0.1
    
    return max(0.0, min(1.0, base - recency_penalty))
```

### Anomaly Detection

**Schedule changes**: If an event's `scheduled_at` changes by more than 30 minutes between ingestion runs from the SAME source, flag it as a possible reschedule. If 2+ sources agree on the new time, update the canonical event. If only 1 source shows the change, hold for manual review.

**Disappearing events**: If a source previously reported an event and now no longer includes it (within the expected date range), flag it as potentially cancelled. Do NOT auto-cancel. Wait for confirmation from a second source or 24 hours.

**New entities**: If an ingestion run produces a team name that cannot be resolved to any existing team (even fuzzy), create an alert. This may indicate a new expansion team, a team rebrand, or a parsing error.

### Reconciliation

Weekly automated reconciliation job:
1. For NBA: compare our events for the current week against the official NBA CDN schedule. Flag any mismatches.
2. For LoL: compare our events against PandaScore's upcoming matches. Flag any events we have that PandaScore doesn't, and vice versa.
3. Generate a reconciliation report with: total events, matched count, unmatched count, conflict details.

---

## 9. API Specification

### Base URL

`https://api.sportshub.dev/v1` (production)
`http://localhost:8000/api/v1` (development)

### Authentication

API key-based authentication via `X-API-Key` header.

```
X-API-Key: sh_live_abc123def456
```

MVP: Single shared API key. Post-MVP: Per-consumer keys with usage tracking and tiered rate limits.

### Rate Limiting

| Tier | Requests/Minute | Burst |
|---|---|---|
| Free (MVP default) | 60 | 10 |
| Standard (post-MVP) | 300 | 50 |
| Premium (post-MVP) | 1200 | 200 |

Rate limit headers in every response:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 57
X-RateLimit-Reset: 1711497600
```

### Error Format

All errors follow a consistent structure:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid value for parameter 'sport'. Must be one of: nba, lol, soccer",
    "details": {
      "parameter": "sport",
      "provided": "football",
      "allowed": ["nba", "lol", "soccer"]
    }
  }
}
```

Error codes: `VALIDATION_ERROR`, `NOT_FOUND`, `RATE_LIMITED`, `UNAUTHORIZED`, `INTERNAL_ERROR`.

### Endpoints

---

#### `GET /api/v1/events`

List upcoming events with filtering, pagination, and sorting.

**Query Parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `sport` | string | (all) | Filter by sport: `nba`, `lol`, `soccer` |
| `team_id` | uuid | (none) | Filter events involving this team (home or away) |
| `competition_id` | uuid | (none) | Filter by competition |
| `status` | string | `scheduled` | Filter by status: `scheduled`, `postponed`, `cancelled` |
| `from` | ISO 8601 | NOW() | Start of date range (inclusive) |
| `to` | ISO 8601 | +30 days | End of date range (inclusive) |
| `sort` | string | `scheduled_at` | Sort field: `scheduled_at`, `-scheduled_at`, `created_at` |
| `page` | integer | 1 | Page number |
| `per_page` | integer | 25 | Items per page (max 100) |

**Example Request**:
```
GET /api/v1/events?sport=nba&from=2026-03-26T00:00:00Z&to=2026-04-02T00:00:00Z&per_page=10
```

**Example Response** (200 OK):
```json
{
  "data": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "sport": "nba",
      "status": "scheduled",
      "scheduled_at": "2026-03-27T00:00:00Z",
      "match_format": "single",
      "venue": "Crypto.com Arena",
      "confidence_score": 0.95,
      "source_count": 3,
      "home_team": {
        "id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
        "name": "Los Angeles Lakers",
        "short_name": "Lakers",
        "abbreviation": "LAL"
      },
      "away_team": {
        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "name": "Boston Celtics",
        "short_name": "Celtics",
        "abbreviation": "BOS"
      },
      "competition": {
        "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
        "name": "NBA 2025-26 Regular Season",
        "short_name": "NBA Regular Season"
      },
      "metadata": {
        "broadcast": ["ESPN", "TNT"]
      },
      "created_at": "2026-03-20T10:30:00Z",
      "updated_at": "2026-03-26T14:15:00Z"
    },
    {
      "id": "d4e5f6a7-b8c9-0123-defg-234567890123",
      "sport": "lol",
      "status": "scheduled",
      "scheduled_at": "2026-03-27T09:00:00Z",
      "match_format": "bo3",
      "venue": null,
      "confidence_score": 0.98,
      "source_count": 2,
      "home_team": {
        "id": "e5f6a7b8-c9d0-1234-efgh-345678901234",
        "name": "T1",
        "short_name": "T1",
        "abbreviation": "T1"
      },
      "away_team": {
        "id": "f6a7b8c9-d0e1-2345-fghi-456789012345",
        "name": "Gen.G",
        "short_name": "Gen.G",
        "abbreviation": "GEN"
      },
      "competition": {
        "id": "a7b8c9d0-e1f2-3456-ghij-567890123456",
        "name": "LCK 2026 Spring",
        "short_name": "LCK Spring"
      },
      "metadata": {
        "block_name": "Week 8",
        "best_of": 3
      },
      "created_at": "2026-03-15T08:00:00Z",
      "updated_at": "2026-03-26T09:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 10,
    "total": 47,
    "total_pages": 5
  }
}
```

---

#### `GET /api/v1/events/{id}`

Get a single event with full source provenance.

**Example Response** (200 OK):
```json
{
  "data": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "sport": "nba",
    "status": "scheduled",
    "scheduled_at": "2026-03-27T00:00:00Z",
    "match_format": "single",
    "venue": "Crypto.com Arena",
    "confidence_score": 0.95,
    "source_count": 3,
    "home_team": {
      "id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
      "name": "Los Angeles Lakers",
      "short_name": "Lakers",
      "abbreviation": "LAL"
    },
    "away_team": {
      "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "name": "Boston Celtics",
      "short_name": "Celtics",
      "abbreviation": "BOS"
    },
    "competition": {
      "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
      "name": "NBA 2025-26 Regular Season",
      "short_name": "NBA Regular Season"
    },
    "sources": [
      {
        "source_id": "nbacom_cdn",
        "source_event_id": "0022501034",
        "scheduled_at": "2026-03-27T00:00:00Z",
        "raw_home_team": "Lakers",
        "raw_away_team": "Celtics",
        "match_confidence": 0.95,
        "fetched_at": "2026-03-26T12:00:00Z"
      },
      {
        "source_id": "espn_nba",
        "source_event_id": "401654321",
        "scheduled_at": "2026-03-27T00:00:00Z",
        "raw_home_team": "Los Angeles Lakers",
        "raw_away_team": "Boston Celtics",
        "match_confidence": 0.98,
        "fetched_at": "2026-03-26T14:15:00Z"
      },
      {
        "source_id": "balldontlie_nba",
        "source_event_id": "98765",
        "scheduled_at": "2026-03-27T00:00:00Z",
        "raw_home_team": "Los Angeles Lakers",
        "raw_away_team": "Boston Celtics",
        "match_confidence": 0.90,
        "fetched_at": "2026-03-26T14:30:00Z"
      }
    ],
    "metadata": {
      "broadcast": ["ESPN", "TNT"]
    },
    "created_at": "2026-03-20T10:30:00Z",
    "updated_at": "2026-03-26T14:15:00Z"
  }
}
```

---

#### `GET /api/v1/teams`

**Query Parameters**: `sport` (optional), `search` (optional, fuzzy name search), `page`, `per_page`

**Example Response** (200 OK):
```json
{
  "data": [
    {
      "id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
      "name": "Los Angeles Lakers",
      "short_name": "Lakers",
      "abbreviation": "LAL",
      "sport": "nba",
      "active": true,
      "metadata": {
        "conference": "Western",
        "division": "Pacific"
      }
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total": 30,
    "total_pages": 2
  }
}
```

---

#### `GET /api/v1/teams/{id}/players`

**Description**: Get the roster of players for a specific team.

**Query Parameters**: `active` (optional, boolean, default `true`), `position` (optional), `page`, `per_page`

**Example Response** (200 OK):
```json
{
  "data": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "name": "LeBron James",
      "sport": "nba",
      "position": "SF",
      "role": "Starter",
      "jersey_number": "23",
      "nationality": "United States",
      "active": true,
      "team": {
        "id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
        "name": "Los Angeles Lakers",
        "abbreviation": "LAL"
      },
      "metadata": {
        "height_cm": 206,
        "weight_kg": 113,
        "draft_year": 2003
      }
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total": 15,
    "total_pages": 1
  }
}
```

---

#### `GET /api/v1/players`

**Description**: Search and list players across all teams and sports.

**Query Parameters**: `sport` (optional), `team_id` (optional), `search` (optional, fuzzy name/alias search), `position` (optional), `active` (optional, boolean), `page`, `per_page`

**Example Response** (200 OK):
```json
{
  "data": [
    {
      "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
      "name": "Faker",
      "sport": "lol",
      "position": "Mid",
      "role": "Captain",
      "jersey_number": null,
      "nationality": "South Korea",
      "active": true,
      "team": {
        "id": "d4e5f6a7-b8c9-0123-defg-345678901234",
        "name": "T1",
        "abbreviation": "T1"
      },
      "aliases": ["Faker", "Hide on Bush"],
      "metadata": {
        "summoner_name": "Faker",
        "worlds_titles": 4
      }
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total": 247,
    "total_pages": 10
  }
}
```

---

#### `GET /api/v1/players/{id}`

**Description**: Get detailed information about a specific player, including all known aliases.

**Example Response** (200 OK):
```json
{
  "data": {
    "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    "name": "Faker",
    "sport": "lol",
    "position": "Mid",
    "role": "Captain",
    "jersey_number": null,
    "nationality": "South Korea",
    "active": true,
    "team": {
      "id": "d4e5f6a7-b8c9-0123-defg-345678901234",
      "name": "T1",
      "abbreviation": "T1"
    },
    "aliases": [
      {"alias": "Faker", "source": "lolesports", "is_primary": true},
      {"alias": "Hide on Bush", "source": "lolesports", "is_primary": false},
      {"alias": "Faker", "source": "pandascore_lol", "is_primary": true}
    ],
    "metadata": {
      "summoner_name": "Faker",
      "worlds_titles": 4,
      "champion_pool": ["Azir", "LeBlanc", "Ryze"]
    },
    "created_at": "2026-01-15T00:00:00Z",
    "updated_at": "2026-03-20T12:00:00Z"
  }
}
```

---

#### `GET /api/v1/competitions`

**Query Parameters**: `sport` (optional), `page`, `per_page`

**Example Response** (200 OK):
```json
{
  "data": [
    {
      "id": "c3d4e5f6-a7b8-9012-cdef-123456789012",
      "name": "NBA 2025-26 Regular Season",
      "short_name": "NBA Regular Season",
      "sport": "nba",
      "season": "2025-26",
      "region": "North America",
      "tier": "major",
      "start_date": "2025-10-21T00:00:00Z",
      "end_date": "2026-04-12T00:00:00Z"
    },
    {
      "id": "a7b8c9d0-e1f2-3456-ghij-567890123456",
      "name": "LCK 2026 Spring",
      "short_name": "LCK Spring",
      "sport": "lol",
      "season": "Spring 2026",
      "region": "Korea",
      "tier": "major",
      "start_date": "2026-01-15T00:00:00Z",
      "end_date": "2026-04-05T00:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total": 8,
    "total_pages": 1
  }
}
```

---

#### `GET /api/v1/health`

System health endpoint. No authentication required.

**Example Response** (200 OK):
```json
{
  "status": "healthy",
  "timestamp": "2026-03-26T15:00:00Z",
  "version": "0.1.0",
  "components": {
    "database": {
      "status": "healthy",
      "latency_ms": 2
    },
    "cache": {
      "status": "healthy",
      "latency_ms": 1,
      "hit_ratio": 0.85
    },
    "sources": {
      "espn_nba": {
        "status": "healthy",
        "last_success": "2026-03-26T14:55:00Z",
        "reliability_30d": 0.98
      },
      "nbacom_cdn": {
        "status": "healthy",
        "last_success": "2026-03-26T14:00:00Z",
        "reliability_30d": 0.99
      },
      "balldontlie_nba": {
        "status": "healthy",
        "last_success": "2026-03-26T14:45:00Z",
        "reliability_30d": 0.95
      },
      "lolesports": {
        "status": "healthy",
        "last_success": "2026-03-26T14:50:00Z",
        "reliability_30d": 0.97
      },
      "pandascore_lol": {
        "status": "healthy",
        "last_success": "2026-03-26T14:48:00Z",
        "reliability_30d": 0.99
      },
      "liquipedia_lol": {
        "status": "degraded",
        "last_success": "2026-03-26T12:00:00Z",
        "reliability_30d": 0.88
      }
    }
  },
  "stats": {
    "total_events": 342,
    "events_scheduled": 287,
    "total_teams": 62,
    "total_competitions": 8,
    "unmatched_source_records": 3
  }
}
```

---

## 10. Tech Stack (Justified from First Principles)

### Python 3.12+

**Why Python**: This system is I/O-bound, not CPU-bound. It spends most of its time waiting on HTTP responses from data sources and database queries. Python's async/await with `asyncio` handles concurrent I/O efficiently. The data transformation pipeline (parsing JSON, normalizing strings, comparing records) is straightforward business logic where Python's readability and rapid iteration speed outweigh raw compute performance. The alternative -- Go or Rust -- would provide faster execution but slower development velocity for a team building an MVP.

Python 3.12 specifically because: improved error messages, `tomllib` built in (for configuration), performance improvements from the adaptive specializing interpreter, and it is the current stable release.

### FastAPI

**Why FastAPI**: Three properties matter for our API layer: (1) async-native, because our API handlers may need to query PostgreSQL and Redis concurrently; (2) automatic OpenAPI schema generation, because downstream consumers (betting sites, analytics dashboards) need machine-readable API documentation to build integrations; (3) Pydantic integration for request/response validation, which eliminates an entire class of bugs where malformed data passes through the API.

FastAPI is the only Python framework that provides all three natively. Flask is synchronous by default. Django REST Framework is heavy and synchronous. Starlette is the underlying ASGI layer but lacks the validation/documentation layer.

### PostgreSQL 16

**Why PostgreSQL**: Our data model has well-defined entities with clear relationships (events belong to competitions, have two teams, map to source records). This is a relational data model. PostgreSQL provides: JSONB columns for flexible metadata without schema migrations (critical when different sports have different metadata shapes), `pg_trgm` for fuzzy text matching in entity resolution, partial indexes for efficient filtered queries (upcoming events only), and `TIMESTAMPTZ` for correct timezone handling (all our sources use different timezone conventions).

Why not MongoDB: Our core queries (upcoming events by sport, sorted by time, filtered by team) are relational joins. MongoDB would denormalize these into event documents, duplicating team data across events and making team-level updates expensive.

Why not SQLite: No concurrent write support. Our ingestion scheduler and API server write simultaneously.

### Redis

**Why a cache layer**: Our API serves the same data to many consumers between ingestion runs. An hourly ingestion cycle means the response to "upcoming NBA events" is identical for hundreds of requests within that window. Caching eliminates redundant PostgreSQL queries. Redis specifically because: in-memory speed (<1ms reads), native TTL support for automatic expiry, and the `DEL` command for surgical cache invalidation after ingestion runs.

Why not just PostgreSQL query caching: PostgreSQL's query cache (via shared_buffers) helps with identical queries but does not help with response serialization overhead. Redis caches the fully serialized JSON response body, eliminating both the query and the serialization.

### APScheduler

**Why APScheduler**: We need to run heterogeneous jobs at different intervals (hourly ESPN fetches, 2-hour CDN fetches, daily Liquipedia scrapes). APScheduler supports cron-style and interval-based scheduling with async executors, job persistence (via database backend), and missed-fire handling. It runs in-process with the FastAPI application, avoiding the operational overhead of a separate Celery/Redis queue system for MVP.

Why not Celery: Celery requires a message broker (Redis/RabbitMQ), a separate worker process, and operational monitoring of the broker. For 6 ingestion jobs and a few maintenance tasks, this is over-engineered. APScheduler runs as a background thread in the same process.

Why not cron: OS-level cron cannot manage Python async coroutines, has no built-in retry logic, and requires separate process invocations (cold starts) for each job.

### SQLAlchemy 2.0 (async)

**Why SQLAlchemy**: SQLAlchemy 2.0 provides async session management via `asyncpg`, type-safe query building, and Alembic integration for migrations. It sits at the right abstraction level: we write Python rather than raw SQL for standard CRUD, but can drop to raw SQL for complex queries (entity resolution matching).

Why not raw asyncpg: Raw SQL for all queries means no schema validation at the Python level, manual parameter binding everywhere, and no migration tooling.

Why not Tortoise ORM or SQLModel: SQLAlchemy has the largest ecosystem, most battle-tested codebase, and best async support in 2.0.

### Pydantic v2

**Why Pydantic**: Every data flow boundary in our system (adapter output, normalization input/output, API request/response) needs validation. Pydantic v2 provides: 50x faster validation than v1 (Rust core), JSON Schema generation for API documentation, and coercion rules that handle messy source data (e.g., string timestamps auto-parsed to datetime). It is the natural companion to FastAPI and SQLAlchemy 2.0.

### Docker Compose

**Why Docker Compose for dev**: Three services (app, PostgreSQL, Redis) need to run together. Docker Compose provides reproducible local environments, single-command startup (`docker compose up`), and a migration path to production orchestration. The same `Dockerfile` used in Compose deploys to any container hosting (Railway, Fly.io, ECS, Kubernetes).

Why not Kubernetes for MVP: Kubernetes solves multi-node orchestration, auto-scaling, and service mesh problems we do not have. A single-node deployment with Docker Compose is operationally simpler and costs nothing extra.

### Alembic

**Why Alembic**: Schema evolution is inevitable. We will add sports, add fields to events, modify indexes. Alembic provides version-controlled, reversible migrations that integrate with SQLAlchemy models. The alternative -- hand-written SQL migration scripts -- lacks dependency tracking, reversibility testing, and auto-generation from model changes.

---

## 11. Project Structure

```
sportshub/
|-- pyproject.toml                    # Project metadata, dependencies, tool config
|-- Dockerfile                        # Multi-stage build: builder + runtime
|-- docker-compose.yml                # App + PostgreSQL + Redis
|-- alembic.ini                       # Alembic migration configuration
|-- README.md                         # Setup instructions, architecture overview
|
|-- alembic/
|   |-- env.py                        # Alembic environment (async engine setup)
|   |-- versions/                     # Migration files (auto-generated + manual)
|       |-- 001_initial_schema.py
|
|-- src/
|   |-- sportshub/
|       |-- __init__.py
|       |-- main.py                   # FastAPI app factory, lifespan events
|       |-- config.py                 # Settings via pydantic-settings (env vars)
|       |
|       |-- models/                   # Pydantic domain models (NOT ORM)
|       |   |-- __init__.py
|       |   |-- event.py              # Event, EventStatus, MatchFormat
|       |   |-- team.py               # Team, TeamAlias
|       |   |-- player.py             # Player, PlayerAlias
|       |   |-- competition.py        # Competition
|       |   |-- source.py             # SourceRecord, IngestionRun, RawEvent
|       |   |-- common.py             # Sport enum, pagination, shared types
|       |
|       |-- db/                       # Database layer
|       |   |-- __init__.py
|       |   |-- engine.py             # Async engine + session factory
|       |   |-- tables.py             # SQLAlchemy Table definitions
|       |   |-- repositories/         # Data access objects
|       |       |-- __init__.py
|       |       |-- event_repo.py     # CRUD + query for events
|       |       |-- team_repo.py      # CRUD + alias lookup for teams
|       |       |-- player_repo.py    # CRUD + alias lookup for players
|       |       |-- competition_repo.py
|       |       |-- source_repo.py    # SourceRecord + IngestionRun CRUD
|       |
|       |-- cache/                    # Redis cache layer
|       |   |-- __init__.py
|       |   |-- client.py             # Redis connection + helpers
|       |   |-- keys.py               # Cache key patterns + invalidation
|       |
|       |-- ingestion/                # Layer 1: Source adapters
|       |   |-- __init__.py
|       |   |-- base.py               # SourceAdapter ABC, RawEvent, AdapterHealth
|       |   |-- registry.py           # Adapter registry (discover + instantiate)
|       |   |-- adapters/
|       |       |-- __init__.py
|       |       |-- espn_nba.py       # ESPN NBA adapter
|       |       |-- nbacom_cdn.py     # NBA.com CDN adapter
|       |       |-- balldontlie.py    # BallDontLie NBA adapter
|       |       |-- lolesports.py     # LoL Esports API adapter
|       |       |-- pandascore_lol.py # PandaScore LoL adapter
|       |       |-- liquipedia_lol.py # Liquipedia LoL adapter
|       |
|       |-- resolution/              # Layer 2: Entity resolution
|       |   |-- __init__.py
|       |   |-- normalizer.py         # Team name normalization functions
|       |   |-- matcher.py            # Event matching algorithm
|       |   |-- merger.py             # Conflict resolution + canonical merge
|       |   |-- pipeline.py           # Orchestrates: normalize -> match -> merge
|       |
|       |-- scheduling/              # Layer 4: Job scheduling
|       |   |-- __init__.py
|       |   |-- scheduler.py          # APScheduler setup + job registration
|       |   |-- jobs.py               # Individual job definitions
|       |
|       |-- api/                      # Layer 5: REST API
|       |   |-- __init__.py
|       |   |-- dependencies.py       # FastAPI dependencies (auth, DB session, etc.)
|       |   |-- middleware.py          # Rate limiting, CORS, request logging
|       |   |-- v1/
|       |       |-- __init__.py
|       |       |-- router.py         # Mounts all v1 routers
|       |       |-- events.py         # GET /events, GET /events/{id}
|       |       |-- teams.py          # GET /teams, GET /teams/{id}/players
|       |       |-- players.py        # GET /players, GET /players/{id}
|       |       |-- competitions.py   # GET /competitions
|       |       |-- health.py         # GET /health
|       |       |-- schemas.py        # API request/response Pydantic models
|       |
|       |-- monitoring/              # Observability
|           |-- __init__.py
|           |-- health.py             # Health check logic
|           |-- metrics.py            # In-memory counters (future: Prometheus)
|           |-- logging.py            # Structured logging config (JSON format)
|
|-- tests/
|   |-- conftest.py                   # Shared fixtures (test DB, test client)
|   |-- test_adapters/               # Unit tests per adapter (mocked HTTP)
|   |   |-- test_espn_nba.py
|   |   |-- test_balldontlie.py
|   |   |-- test_lolesports.py
|   |   |-- test_pandascore.py
|   |-- test_resolution/             # Unit tests for entity resolution
|   |   |-- test_normalizer.py
|   |   |-- test_matcher.py
|   |   |-- test_merger.py
|   |-- test_api/                    # Integration tests for API endpoints
|   |   |-- test_events.py
|   |   |-- test_teams.py
|   |   |-- test_health.py
|   |-- test_integration/            # End-to-end: ingest -> resolve -> serve
|       |-- test_full_pipeline.py
|
|-- scripts/
|   |-- seed_teams.py                # Pre-seed canonical teams + aliases
|   |-- seed_players.py              # Pre-seed canonical players + aliases (NBA rosters, LoL pros)
|   |-- seed_competitions.py         # Pre-seed competitions
|   |-- manual_reconcile.py          # CLI tool for manual dedup review
|
|-- data/
    |-- nba_teams.json               # Canonical NBA team data + known aliases
    |-- nba_players.json             # NBA player rosters with positions + aliases
    |-- lol_teams.json               # Canonical LoL team data + known aliases
    |-- lol_players.json             # LoL pro players with roles, gamer tags + aliases
    |-- competitions.json            # Initial competition definitions
```

---

## 12. MVP Milestones

### Phase 1: Foundation (Week 1-2)

**Deliverables**:
- Project scaffolding: pyproject.toml, Dockerfile, docker-compose.yml
- Config management via pydantic-settings
- PostgreSQL schema via Alembic (initial migration)
- Database engine + session management (async)
- Repository layer for all 4 core tables
- Seed scripts for NBA teams (30 teams + aliases) and LoL teams (top 40 teams across major leagues)
- Seed data for competitions (NBA regular season, 5 major LoL leagues)

**Done criteria**: `docker compose up` starts the system, migrations run, seed data populates, repository unit tests pass.

### Phase 2: Ingestion Layer (Week 3-4)

**Deliverables**:
- `SourceAdapter` base class with health checking
- ESPN NBA adapter (including response parsing, UTC conversion)
- NBA CDN adapter (full season JSON parsing)
- BallDontLie adapter (with rate limiting and cursor pagination)
- LoL Esports API adapter
- PandaScore LoL adapter
- Unit tests for each adapter with mocked HTTP responses (save real response fixtures)
- Adapter registry for dynamic discovery

**Done criteria**: Each adapter can be invoked independently and returns valid `RawEvent` objects. All unit tests pass with fixture data.

### Phase 3: Entity Resolution (Week 5-6)

**Deliverables**:
- Team name normalizer with alias table lookup
- Event matching algorithm (team set matching + time proximity scoring)
- Conflict resolution and canonical merge logic
- Resolution pipeline: ingest raw -> normalize -> match -> merge/create
- Unit tests with synthetic scenarios: exact match, fuzzy match, time discrepancy, no match
- Integration test: feed known ESPN + BallDontLie fixtures and verify dedup produces correct canonical events

**Done criteria**: Feed 3 NBA sources with overlapping events, entity resolution produces correct canonical events with <1% error on test fixtures. LoL sources similarly deduplicated.

### Phase 4: Scheduling and Operations (Week 7)

**Deliverables**:
- APScheduler configuration with all ingestion jobs
- Per-source cadences as defined in Section 6
- Circuit breaker logic (consecutive failure tracking, pause/resume)
- IngestionRun audit logging
- Health check job
- Cache invalidation hooks (post-ingestion)
- Structured logging (JSON format) for all jobs

**Done criteria**: System runs unattended for 24 hours, all sources ingested on schedule, no unhandled exceptions, IngestionRun table shows audit trail.

### Phase 5: API Layer (Week 8-9)

**Deliverables**:
- All 5 endpoints as specified in Section 9
- Pagination, filtering, sorting
- Redis caching with write-through invalidation
- API key authentication middleware
- Rate limiting middleware
- OpenAPI documentation auto-generated
- Integration tests: full flow from seeded database to API responses
- Error handling middleware (consistent error format)

**Done criteria**: All API endpoints return correctly formatted responses. p95 latency <200ms with cache warm. Rate limiting functions correctly. OpenAPI spec downloadable at `/docs`.

### Phase 6: Hardening and Launch (Week 10)

**Deliverables**:
- End-to-end integration test: cold start -> seed -> ingest -> resolve -> serve via API
- Reconciliation script (compare our data vs. source-of-truth)
- Monitoring setup: health endpoint, structured log aggregation, uptime checks
- Documentation: README with setup instructions, API guide, architecture diagram
- Production deployment config (environment variables, secrets management)
- Load test: 100 concurrent requests sustained for 5 minutes

**Done criteria**: System runs in Docker Compose for 7 days with <1 hour total downtime. API serves accurate data for both NBA and LoL. Reconciliation report shows >95% event coverage.

---

## 13. Edge Cases and Operational Runbook

### Game Postponed

**Detection**: A source reports an event that was previously `scheduled` is now `postponed` or removed from the upcoming schedule.

**Handling**:
1. If the official source (NBA CDN for NBA, LoL Esports API for LoL) reports postponement: update canonical event status to `postponed` immediately.
2. If only a secondary source reports it: flag for manual review. Set a 1-hour timer. If no confirmation from a second source within 1 hour, auto-update to `postponed`.
3. Log the status change in the event's metadata with timestamp and source.
4. Invalidate cache for this event and any list queries that include it.

### Two Sources Disagree on Start Time by >1 Hour

**Detection**: During merge, the `time_diff` exceeds 3600 seconds between the new source record and the existing canonical event.

**Handling**:
1. The match confidence score will be in the 0.50-0.69 range (manual review zone).
2. Log an alert: "Time discrepancy detected for event {id}: source {A} says {time_A}, source {B} says {time_B}."
3. Do NOT auto-update the canonical time. The higher-priority source's time remains canonical.
4. If the higher-priority source later publishes the new time, update canonically. This is the normal flow for schedule changes (the official source updates, then secondary sources lag).

### Provider Goes Down for Extended Period

**Detection**: Circuit breaker trips after 5 consecutive failures. Health check reports source as unhealthy.

**Handling**:
1. Log alert with source ID and last error.
2. Circuit breaker pauses that source's ingestion (1 hour, then 4 hour escalation).
3. Existing canonical events are NOT affected (they persist in PostgreSQL).
4. Data freshness for that source degrades. If all sources for a sport go down, the health endpoint reports `degraded` status.
5. When the source recovers (circuit breaker probe succeeds), resume normal cadence. The first run will catch up on any missed data.

### New Team Added Mid-Season

**Detection**: An ingestion run produces a team name that cannot be resolved via alias table lookup or fuzzy matching.

**Handling**:
1. The source record is created with `event_id = NULL` (unmatched).
2. An alert is logged: "Unresolved team: '{raw_name}' from source {source_id}."
3. Manual intervention: operator runs `scripts/manual_reconcile.py`, sees the unresolved record, creates a new canonical team entry with aliases.
4. On the next entity resolution pass, the previously unmatched records will match.
5. Post-MVP: auto-detect and auto-create teams with low confidence, flagged for review.

### Player Trade or Transfer

**Detection**: Daily player roster sync detects a player on a different team than currently recorded, or a new player name appears.

**Handling**:
1. If the player is recognized (via alias), update `players.team_id` to the new team. Log the transfer.
2. If the player is new and unrecognized, create a new player record with aliases from the source. Flag for manual alias seeding.
3. LoL-specific: gamer tag changes are common. If a known player_id appears with a new alias from the same source, add the alias automatically.
4. NBA-specific: mid-season trades are well-reported. BallDontLie and ESPN both update rosters promptly; cross-validate before updating.

### LoL Tournament Schedule Changed

**Detection**: PandaScore or LoL Esports API returns different `scheduled_at` for previously known matches.

**Handling**:
1. The source record is updated (upsert on `source_id + source_event_id`).
2. Entity resolution detects the change: existing source record for this event has a new time.
3. If the source is the highest-priority source for this event, update the canonical `scheduled_at`.
4. Log the change: "Event {id} rescheduled: {old_time} -> {new_time} per {source_id}."
5. Invalidate cache.

### Duplicate Canonical Events Discovered After Merge

**Detection**: Manual review or reconciliation script identifies two canonical events that represent the same real-world game (entity resolution failed to match them).

**Handling**:
1. Choose the canonical event with higher `source_count` and `confidence_score` as the survivor.
2. Reassign all `source_records` from the duplicate to the survivor.
3. Recalculate the survivor's `source_count` and `confidence_score`.
4. Delete the duplicate canonical event.
5. Log the merge: "Merged duplicate event {dup_id} into {survivor_id}."
6. Invalidate cache.
7. Post-mortem: investigate why entity resolution missed this. Likely a missing team alias. Add it.

---

## 14. Scaling Considerations (Post-MVP)

### Live Data (WebSocket Streaming)

**Architecture change**: Add a `LiveDataAdapter` interface extending `SourceAdapter` with a `subscribe()` method that returns an `AsyncIterator[RawEvent]`. Use WebSocket connections to sources that support it (PandaScore live plan, or polling with sub-second intervals for others).

**Serving**: FastAPI supports WebSocket endpoints natively. Add `WS /api/v1/events/{id}/live` that pushes score updates. Use Redis Pub/Sub to fan out from ingestion workers to API WebSocket handlers.

**Database**: Add `event_updates` table for time-series score data. Consider TimescaleDB extension for PostgreSQL for efficient time-series queries on historical live data.

### Historical Backfill

**Architecture change**: Backfill is a one-time batch operation per source, not a recurring job. Add a `backfill_historical()` method to adapters that returns paginated historical data. Rate-limit aggressively to avoid source bans.

**Storage**: Historical events will be 10-100x the volume of upcoming events. Add table partitioning on `events` by `scheduled_at` (monthly partitions). Partition pruning keeps upcoming-event queries fast.

### Additional Sports (10+)

**Architecture change**: The `SourceAdapter` interface is already sport-agnostic. Add new adapters. The entity resolution pipeline needs sport-specific team alias tables but the algorithm is generic.

**Organizational**: Group adapters by sport in subdirectories: `adapters/nba/`, `adapters/lol/`, `adapters/nfl/`, etc.

**Database**: No schema change needed. `sport` column and indexes already support multi-sport queries.

### Odds/Betting Data

**Architecture change**: Add an `Odds` table linked to events. Odds are time-series data (they change frequently). Schema:

```sql
CREATE TABLE odds (
    id UUID PRIMARY KEY,
    event_id UUID NOT NULL REFERENCES events(id),
    bookmaker VARCHAR(100) NOT NULL,
    market_type VARCHAR(50) NOT NULL,  -- "moneyline", "spread", "total"
    home_odds DECIMAL(10,4),
    away_odds DECIMAL(10,4),
    line DECIMAL(10,2),               -- For spread/total
    recorded_at TIMESTAMPTZ NOT NULL,
    source_id VARCHAR(50) NOT NULL
);
```

Partition by `recorded_at` (daily). Index on `(event_id, market_type, recorded_at DESC)` for "latest odds for this event" queries.

### ML-Based Quality Scoring

Replace the rule-based confidence scoring (Section 5) with a trained model. Features: source reliability history, time discrepancy, team name match quality (edit distance), historical merge accuracy for similar events. Train on manually-verified merge decisions from the reconciliation audit.

### Multi-Region Deployment

PostgreSQL read replicas in each region. Redis replicas for cache. Application deployed on Fly.io or Kubernetes with regional affinity. Single-writer primary in the region closest to data sources (US East for NBA, a split between US/Asia for LoL).

### Event-Driven Architecture (Replace Polling)

For sources that support webhooks or streaming: replace APScheduler polling with an event bus. Architecture: source pushes to webhook endpoint -> message queue (Redis Streams or SQS) -> consumer processes through normalization pipeline. Polling remains as fallback for sources without push support.

---
