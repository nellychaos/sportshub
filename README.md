# Sportshub API

Sports data aggregation platform that consumes from multiple upstream sources, deduplicates events, and serves a clean, consistent REST API. Covers NBA, League of Legends esports, and the FIFA World Cup 2026.

**Live API**: `https://joyful-peace-production-d7f2.up.railway.app`
**Interactive Docs**: `https://joyful-peace-production-d7f2.up.railway.app/docs`
**ReDoc**: `https://joyful-peace-production-d7f2.up.railway.app/redoc`

---

## Quick Start

All endpoints (except `/health`) require an API key passed via the `X-API-Key` header.

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://joyful-peace-production-d7f2.up.railway.app/api/v1/events?sport=nba&per_page=5"
```

---

## Authentication

Include your API key in every request:

```
X-API-Key: YOUR_API_KEY
```

Requests without a valid key receive a `403 Forbidden` response.

---

## Base URL

```
https://joyful-peace-production-d7f2.up.railway.app/api/v1
```

All endpoints below are relative to this base.

---

## Pagination

All list endpoints support pagination via query parameters:

| Parameter  | Type | Default | Description              |
|------------|------|---------|--------------------------|
| `page`     | int  | 1       | Page number (1-indexed)  |
| `per_page` | int  | 20      | Items per page (max 100) |

Every list response includes a `pagination` object:

```json
{
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 328,
    "total_pages": 17
  }
}
```

---

## Sports

The API covers three sports, each identified by a string value:

| Sport Value  | Description                        |
|--------------|------------------------------------|
| `nba`        | NBA basketball (2025-26 season)    |
| `lol`        | League of Legends esports          |
| `football`   | FIFA World Cup 2026                |

Use these values in `?sport=` query filters.

---

## Endpoints

### Health Check

```
GET /health
```

No authentication required. Returns system status including database, cache, and aggregate data counts.

**Response:**

```json
{
  "status": "healthy",
  "database": { "status": "healthy" },
  "cache": { "status": "healthy" },
  "sources": [],
  "stats": {
    "total_events": 328,
    "events_scheduled": 328,
    "total_teams": 141,
    "total_competitions": 18,
    "unmatched_source_records": 50
  }
}
```

---

### Events

#### List Events

```
GET /events
```

Returns upcoming sports events, deduplicated and normalized across sources.

**Query Parameters:**

| Parameter       | Type     | Default     | Description                                             |
|-----------------|----------|-------------|---------------------------------------------------------|
| `sport`         | string   | —           | Filter by sport: `nba`, `lol`, `football`               |
| `team_id`       | UUID     | —           | Filter to events involving this team                    |
| `competition_id`| UUID     | —           | Filter to events in this competition                    |
| `status`        | string   | `scheduled` | Event status filter: `scheduled`, `live`, `completed`   |
| `from`          | datetime | now         | Start of date range (ISO 8601)                          |
| `to`            | datetime | now + 30d   | End of date range (ISO 8601)                            |
| `sort`          | string   | `scheduled_at` | Sort field                                           |

**Example:**

```bash
# NBA games in April 2026
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://joyful-peace-production-d7f2.up.railway.app/api/v1/events?sport=nba&from=2026-04-01T00:00:00Z&to=2026-04-30T23:59:59Z"
```

**Response:**

```json
{
  "data": [
    {
      "id": "0f3a1bf0-3b34-43ed-87f6-5a41c00fe307",
      "sport": "nba",
      "home_team": {
        "id": "58275661-ef80-4b09-a52b-db10c2a16f91",
        "name": "Milwaukee Bucks",
        "short_name": "Bucks",
        "abbreviation": "MIL",
        "sport": "nba"
      },
      "away_team": {
        "id": "ea7c5b54-00b1-4575-8de2-9b34df7210ae",
        "name": "Los Angeles Clippers",
        "short_name": "Clippers",
        "abbreviation": "LAC",
        "sport": "nba"
      },
      "competition": {
        "id": "69d8b3bf-dde0-4263-b845-b088bd55f8e0",
        "name": "NBA 2025-26 Regular Season",
        "short_name": "NBA-RS",
        "sport": "nba",
        "season": "2025-26"
      },
      "scheduled_at": "2026-03-29T19:30:00Z",
      "status": "scheduled",
      "match_format": "single",
      "venue": "Fiserv Forum",
      "confidence_score": 1.0,
      "source_count": 2,
      "created_at": "2026-03-28T06:04:30.778920Z",
      "updated_at": "2026-03-28T06:24:03.814270Z"
    }
  ],
  "pagination": { "page": 1, "per_page": 20, "total": 120, "total_pages": 6 }
}
```

#### Get Event Detail

```
GET /events/{event_id}
```

Returns full event detail including data sources and enrichment data (rosters, player stats, injuries) when available.

**Example:**

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://joyful-peace-production-d7f2.up.railway.app/api/v1/events/86bd9bdd-16be-4c18-b1cd-f36c38e4a98c"
```

**Response** (truncated for brevity):

```json
{
  "id": "86bd9bdd-16be-4c18-b1cd-f36c38e4a98c",
  "sport": "nba",
  "home_team": {
    "name": "Brooklyn Nets",
    "abbreviation": "BKN"
  },
  "away_team": {
    "name": "Charlotte Hornets",
    "abbreviation": "CHA"
  },
  "scheduled_at": "2026-03-31T23:30:00Z",
  "venue": "Barclays Center",
  "confidence_score": 1.0,
  "source_count": 2,
  "sources": [
    {
      "source_id": "espn_nba",
      "source_event_id": "401810955",
      "raw_home_team": "Brooklyn Nets",
      "raw_away_team": "Charlotte Hornets",
      "scheduled_at": "2026-03-31T23:30:00Z",
      "venue": "Barclays Center",
      "match_confidence": 1.0
    },
    {
      "source_id": "nbacom_cdn",
      "source_event_id": "0022501100",
      "raw_home_team": "Nets",
      "raw_away_team": "Hornets",
      "scheduled_at": "2026-03-31T23:30:00Z",
      "venue": "Barclays Center",
      "match_confidence": 1.0
    }
  ],
  "enrichment": {
    "home_team": {
      "record": "17-57",
      "stats": {
        "wins": 17, "losses": 57, "win_pct": 0.23,
        "points_per_game": 106.2, "rebounds_per_game": 39.7,
        "assists_per_game": 25.2,
        "fg_pct": 0.443, "fg3_pct": 0.341, "ft_pct": 0.777
      },
      "roster": [
        {
          "name": "Nic Claxton",
          "jersey_number": "33",
          "position": "C",
          "bio": {
            "height": "6' 11\"", "weight": "215 lbs",
            "age": 26, "college": "Georgia",
            "headshot_url": "https://a.espncdn.com/i/headshots/nba/players/full/4278067.png"
          },
          "season_stats": {
            "games_played": 66, "minutes_per_game": 28.0,
            "points_per_game": 11.8, "rebounds_per_game": 7.0,
            "assists_per_game": 3.7, "fg_pct": 0.571
          }
        }
      ],
      "injuries": []
    },
    "away_team": {
      "record": "39-34",
      "stats": { "wins": 39, "losses": 34, "points_per_game": 116.3 },
      "roster": ["... 18 players with full stats ..."],
      "injuries": []
    }
  },
  "metadata": { "... raw enrichment data ..." }
}
```

**Key fields:**

| Field               | Description                                                                 |
|---------------------|-----------------------------------------------------------------------------|
| `confidence_score`  | 0.0-1.0 — how confident the system is this event is correctly deduplicated  |
| `source_count`      | Number of independent sources confirming this event                         |
| `sources`           | Raw data from each upstream source, including their original team names     |
| `enrichment`        | Typed roster, stats, and injury data (present on enriched events only)      |
| `metadata`          | Raw JSONB enrichment data as stored                                         |

---

### Teams

#### List Teams

```
GET /teams
```

**Query Parameters:**

| Parameter | Type   | Description                         |
|-----------|--------|-------------------------------------|
| `sport`   | string | Filter by sport: `nba`, `lol`, `football` |
| `search`  | string | Fuzzy search by team name           |

**Example:**

```bash
# Search for teams matching "lakers"
curl -H "X-API-Key: YOUR_API_KEY" \
  "https://joyful-peace-production-d7f2.up.railway.app/api/v1/teams?search=lakers"
```

**Response:**

```json
{
  "data": [
    {
      "id": "a1b2c3d4-...",
      "name": "Los Angeles Lakers",
      "short_name": "Lakers",
      "abbreviation": "LAL",
      "sport": "nba",
      "active": true,
      "metadata": {}
    }
  ],
  "pagination": { "page": 1, "per_page": 20, "total": 1, "total_pages": 1 }
}
```

#### List Team Players

```
GET /teams/{team_id}/players
```

Returns the roster for a specific team.

**Query Parameters:**

| Parameter  | Type   | Default | Description                     |
|------------|--------|---------|---------------------------------|
| `active`   | bool   | `true`  | Filter by active status         |
| `position` | string | —       | Filter by position (e.g. `G`, `F`, `C`) |

---

### Players

#### List Players

```
GET /players
```

**Query Parameters:**

| Parameter  | Type   | Default | Description                              |
|------------|--------|---------|------------------------------------------|
| `sport`    | string | —       | Filter by sport                          |
| `team_id`  | UUID   | —       | Filter by team                           |
| `search`   | string | —       | Search by player name                    |
| `position` | string | —       | Filter by position                       |
| `active`   | bool   | `true`  | Filter by active status                  |

#### Get Player Detail

```
GET /players/{player_id}
```

Returns player details including aliases from different data sources.

**Response:**

```json
{
  "id": "...",
  "name": "LaMelo Ball",
  "sport": "nba",
  "team_id": "89e601c3-...",
  "position": "G",
  "jersey_number": "1",
  "nationality": "",
  "active": true,
  "metadata": {
    "height": "6' 7\"",
    "weight": "180 lbs",
    "age": 24,
    "college": "",
    "season_stats": {
      "gp": 63, "pts": 19.7, "reb": 4.8, "ast": 7.1,
      "stl": 1.2, "blk": 0.3, "fg_pct": 0.407
    }
  },
  "aliases": [
    { "alias": "LaMelo Ball", "source_id": "espn_nba", "is_primary": true }
  ]
}
```

---

### Competitions

#### List Competitions

```
GET /competitions
```

**Query Parameters:**

| Parameter | Type   | Description     |
|-----------|--------|-----------------|
| `sport`   | string | Filter by sport |

**Response:**

```json
{
  "data": [
    {
      "id": "69d8b3bf-...",
      "name": "NBA 2025-26 Regular Season",
      "short_name": "NBA-RS",
      "sport": "nba",
      "season": "2025-26",
      "region": "North America",
      "tier": "tier_1",
      "start_date": null,
      "end_date": null,
      "metadata": {
        "teams": 30,
        "format": "82-game regular season",
        "governing_body": "NBA"
      }
    },
    {
      "id": "e4f2b00e-...",
      "name": "FIFA World Cup 2026",
      "short_name": "WC2026",
      "sport": "football",
      "season": "2026",
      "region": "International",
      "tier": "tier_1",
      "metadata": {
        "teams": 48,
        "host_countries": ["United States", "Mexico", "Canada"],
        "total_matches": 104,
        "format": "48-team tournament: 12 groups of 4, knockout rounds from Round of 32"
      }
    }
  ]
}
```

---

## Enrichment Data

Some events include enrichment data with detailed roster and statistical information. This is available in the `enrichment` field of the event detail response.

### Enrichment Structure

```
enrichment
  home_team / away_team
    record          — season record (e.g. "39-34")
    stats           — team aggregate stats
      wins, losses, win_pct
      points_per_game, rebounds_per_game, assists_per_game
      fg_pct, fg3_pct, ft_pct
    roster[]        — player roster
      name, jersey_number, position
      bio
        height, weight, age, birthdate, college, headshot_url
      season_stats
        games_played, minutes_per_game
        points_per_game, rebounds_per_game, assists_per_game
        steals_per_game, blocks_per_game, turnovers_per_game
        fg_pct, fg3_pct, ft_pct, plus_minus
    injuries[]      — injury reports
      player_name, position, status, injury, details
  enriched_at       — when the enrichment was last updated
  enrichment_sources — which APIs provided the data
```

Not all events have enrichment data. The `enrichment` field is `null` for events that haven't been enriched.

---

## Data Sources

Events are aggregated and deduplicated from multiple independent sources:

| Source ID       | Sport      | Description                    |
|-----------------|------------|--------------------------------|
| `espn_nba`      | NBA        | ESPN public API                |
| `nbacom_cdn`    | NBA        | NBA.com CDN schedule feed      |
| `lolesports`    | LoL        | Riot Games LoL Esports API     |
| `espn_fifa`     | Football   | ESPN FIFA World Cup coverage   |

Events confirmed by multiple sources have a higher `confidence_score` and `source_count`. The `sources` array on event detail shows each source's raw data, allowing you to trace provenance.

---

## Error Responses

All errors follow a consistent format:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Event not found"
  }
}
```

| HTTP Status | Code           | Description                    |
|-------------|----------------|--------------------------------|
| 403         | `FORBIDDEN`    | Missing or invalid API key     |
| 404         | `NOT_FOUND`    | Resource does not exist        |
| 422         | `VALIDATION`   | Invalid query parameters       |

---

## Current Data Coverage

| Sport      | Events | Teams | Competitions | Sources per Event |
|------------|--------|-------|--------------|-------------------|
| NBA        | 126    | 30    | 2            | 2 (cross-verified) |
| LoL        | 148    | 102   | 8            | 1                 |
| Football   | 54     | 9     | 1            | 1                 |

---

## Rate Limits

There are no enforced rate limits at this time. Please be respectful and avoid excessive polling. If you need real-time updates, check back periodically rather than polling continuously.

---

## Local Development

### Prerequisites

- Python 3.12+
- PostgreSQL 16
- Redis 7 (optional, app degrades gracefully)

### Setup

```bash
# Clone and install
git clone <repo-url>
cd Sportshub
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env  # Edit with your database credentials

# Database
createdb sportshub
alembic upgrade head
python scripts/seed_competitions.py
python scripts/seed_teams.py

# Run
uvicorn sportshub.main:app --reload --port 8000
```

### Docker

```bash
# Development (includes PostgreSQL and Redis)
docker compose up -d

# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Project Structure

```
src/sportshub/
  api/v1/           REST API endpoints and Pydantic schemas
  ingestion/        Source adapters (ESPN, NBA.com, LoL Esports, etc.)
  resolution/       Entity resolution (team matching, timezone, dedup)
  validation/       Sport-specific constraint validation
  reconciliation/   Cross-source data reconciliation
  scheduling/       Background job scheduling with circuit breakers
  cache/            Redis caching layer
  db/               SQLAlchemy models, tables, repositories
  models/           Domain models (Event, Team, Player, Competition)
  dashboard/        Operations dashboard (HTML)
  monitoring/       Health and observability
data/               Seed data (teams, competitions, venue timezones)
alembic/            Database migrations
scripts/            Seed and utility scripts
```
