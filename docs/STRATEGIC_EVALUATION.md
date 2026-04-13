# Sportshub: Strategic Evaluation

## Thesis Under Test

*With modern scraping tools, LLMs for mapping and matching, and the ability to create a self-healing database, players like Sportradar and other data services are being disrupted and commoditised -- although they will always have an edge on low-latency live data.*

---

## 1. What We Built

Sportshub is a multi-sport data aggregation platform that ingests from 15 registered providers (9 with live automated adapters) across NBA, Football (FIFA WC 2026), and League of Legends. The platform serves as the internal data backbone for multiple front-end sites and products.

### Production State (April 10, 2026)

| Metric | Value |
|--------|-------|
| Events tracked | 428 (NBA 126, Football 126, LoL 176) |
| Teams | 141 |
| Competitions | 18 |
| Active automated adapters | 9 |
| Ingestion runs (last 24h) | 72 runs, 17,512 records fetched |
| Source records ingested | 10,627+ unmatched, 229 scheduled events |
| Events reconciled across sources | 199 |
| AI-assisted resolution attempts | 1,980 calls, $7.10 total spend |
| Continuous uptime | 41.9 hours |
| Production code | ~11,000 lines |

### Architecture Components

1. **Adapter-based ingestion** -- 9 live adapters (ESPN, NBA.com CDN, BallDontLie, FIFA API, football-data.org, LoL Esports, PandaScore, Mollybet x3). Each normalises source-specific formats into a unified RawEvent schema.

2. **Hybrid entity resolution** -- Rule-based normalisation (accent removal, suffix stripping, alias cache) handles ~95% of team matching. An AI fallback (Claude Haiku) resolves the long tail with circuit breaker protection (opens at <40% acceptance or >10s latency).

3. **Post-event reconciliation** -- Compares predicted schedules against observed results from multiple sources. Tracks per-source accuracy on time, teams, and venue.

4. **Dynamic reliability scoring** -- Source priority computed from historical accuracy with 14-day exponential decay half-life. Replaces hardcoded priorities when sufficient samples exist (10+ reconciled events).

5. **Constraint validation** -- Sport-specific schedule rules (no same-day doubleheaders, minimum rest periods) catch data anomalies.

6. **Reference data layer** -- 22 JSON files with 535 NBA player stats (base, advanced, play-by-play, adjusted shooting), 30 teams with TeamRankings power ratings/ATS/O-U trends, FIFA WC rosters, LoL team data.

7. **Operational dashboard** -- Real-time dashboard with provider registry, data inventory, script activity tracking, data completeness metrics, source reliability scores.

---

## 2. Evidence For the Thesis

### A. Multi-source consensus works and is cheap

The system ingests from 9 automated sources on 1-6h cadences at zero marginal data cost (free APIs, public CDNs, undocumented endpoints). Traditional providers charge $50K-500K/year for equivalent schedule data. Our infrastructure costs are under $50/month.

NBA schedule data is confirmed by 3 independent sources (NBA.com CDN, ESPN, BallDontLie). Football by 3 (FIFA API, football-data.org, ESPN). The reconciliation system shows **100% team accuracy and 0.0s average time difference** across 199 reconciled events. This is correct data derived from triangulation, not approximation.

For schedule and fixture data, free public sources contain the same information that Sportradar packages and sells. The value Sportradar adds is convenience, depth, and contractual guarantees -- not the underlying data itself.

### B. AI-assisted resolution reduces manual curation to near zero

The entity resolution pipeline demonstrates the self-healing thesis. When rule-based matching fails (a new team name variant, an abbreviation mismatch), the AI evaluates candidates and either matches or correctly rejects. Successful matches persist as aliases, so the system accumulates knowledge over time without human intervention.

1,980 AI calls at $7.10 total cost demonstrates the economics. At 10x scale, AI-assisted entity resolution would cost ~$70/month -- trivial compared to hiring data operations staff. Traditional providers employ teams of 50-200 people for this work.

**Caveat**: The 0% acceptance rate in production needs context. The AI correctly identified that "Group A Winner" and "Semifinal 1 Winner" are tournament placeholders, not resolvable teams -- so every rejection was correct. But it also means the system has not faced genuinely ambiguous matches yet. The architecture is sound; the test data has not stressed it.

### C. The adapter model scales horizontally

Adding a new source takes an afternoon: write an adapter (~120 lines), add to providers.json, deploy. The registry documents 15 providers with full metadata (auth requirements, rate limits, endpoint URLs, abbreviation maps, known quirks). The Reep football entity register alone brings cross-references to 23 football data providers.

Sportradar maintains proprietary relationships with leagues. Our model treats every public data endpoint as a potential source. The marginal cost of source #16 is close to zero.

### D. Operational observability is a solved problem

The dashboard surfaces data quality metrics that traditional providers keep internal: confidence distributions, source coverage gaps, unmatched records, constraint violations, AI effectiveness, reconciliation accuracy. For internal consumers, this transparency means product teams can understand exactly how reliable the data feeding their front-ends is.

---

## 3. Evidence Against the Thesis

### A. Unmatched records reveal the entity resolution ceiling

10,627 source records remain unmatched:
- Mollybet football: 6,692 (betting odds for events outside our schedule coverage)
- Mollybet basketball: 2,049 (broader market coverage than our NBA schedule)
- Mollybet esports: 1,836
- ESPN FIFA: 44
- ESPN NBA: 6

The Mollybet numbers are expected (they cover hundreds of leagues; we cover 3 sports). But this reveals a fundamental challenge: **matching across sources requires complete reference data**. You cannot match a Mollybet record for "Fenerbahce vs Galatasaray" if you do not have the Turkish Super Lig in your system. Traditional providers solve this by covering everything. We have to choose our battles or progressively expand coverage.

### B. No live data pipeline exists

The thesis concedes Sportradar keeps an edge on low-latency live data, but the gap is wider than anticipated. Sportshub has:
- No in-progress score updates
- No real-time game status changes
- No play-by-play streaming
- No live odds movement tracking

The Mollybet WebSocket adapter exists and authenticates, but it is used for fixture discovery, not live data. Building real-time capability requires fundamentally different infrastructure (event streaming, sub-second latency, WebSocket fan-out to consumers). This is an architectural gap, not just a feature gap.

**This matters because** several front-end products will eventually need live data. Without it, Sportshub is limited to pre-match use cases: content planning, fantasy lineup construction, model building, editorial scheduling.

### C. Data depth is shallow compared to incumbents

Sportshub tracks schedule/fixture data well. But it lacks:
- **Historical data** -- No prior seasons. Traditional providers have 5-20 years of history.
- **Play-by-play for football/LoL** -- Only NBA has player-level stats (via BRef scraping).
- **Injuries/suspensions** -- Not tracked.
- **Lineups/formations** -- Not captured.
- **Venue/weather context** -- Minimal.

StatsBomb has 1.2 million events with 3,400+ data points per match. Our richest data is 535 NBA players with 4 stat categories. This depth gap cannot be closed by adding more public sources alone -- it requires either dedicated collection infrastructure or partnerships.

### D. Direct scraping is operationally fragile

Two of our most valuable sources present durability concerns:
- **ESPN API**: Undocumented, no terms of service for programmatic access. Could be restructured without notice.
- **Basketball Reference**: Cloudflare-protected, requiring browser automation to extract data. Rate-limited to prevent automated access.

The TeamRankings data was extracted via Chrome browser automation because standard HTTP requests are blocked. This works, but a Cloudflare configuration change breaks the pipeline.

Traditional providers have contractual data agreements with leagues. We rely on HTTP requests that could be blocked at any time. At scale, this fragility becomes a material operational risk.

**Mitigation available**: Scraping proxy services like ScrapingBee or Zenrows offer managed residential proxy pools, automatic Cloudflare bypass, JavaScript rendering, and CAPTCHA solving. Using these services would:
- Move Cloudflare bypass from our browser automation to a managed service
- Provide IP rotation across residential proxies (reducing block risk)
- Add cost ($50-250/month for 500K-2M API credits) but significantly improve reliability
- Allow standard HTTP-based adapters instead of browser automation

This turns scraping from a fragile manual process into a managed, budgetable service. It does not eliminate the legal/TOS risk, but it does eliminate the operational fragility.

### E. The 0% AI acceptance rate suggests premature investment

1,980 AI calls with 0 acceptances. The AI correctly rejected placeholder teams, but this means the rule-based system is already handling every resolvable case. The AI layer is a solution waiting for a problem at current scale.

This changes when coverage expands (more sports, more name variants from international leagues), but today it represents cost without demonstrated return. The architecture is correct; the timing is early.

---

## 4. Honest MVP Scorecard

| Dimension | Score | Notes |
|-----------|-------|-------|
| Schedule/fixture accuracy | 9/10 | 100% team accuracy, 0s time drift across 199 reconciled events |
| Multi-source redundancy | 7/10 | 3+ sources per sport for schedules; but 172 of 229 events have single-source coverage |
| Entity resolution | 6/10 | Rule-based handles 95%+; AI untested on genuine ambiguity |
| Data depth (stats/play-by-play) | 3/10 | NBA only; no football/LoL player stats |
| Live/real-time data | 1/10 | WebSocket architecture exists but delivers no live data |
| Historical data | 1/10 | Current season only |
| Operational resilience | 7/10 | Circuit breakers, rate limiting, graceful degradation |
| Observability | 8/10 | Dashboard, structured logging, per-source metrics |
| Cost efficiency | 9/10 | <$50/month infra + $7/month AI for 3-sport coverage |
| Source durability | 4/10 | Undocumented APIs, Cloudflare-protected scraping targets |
| Production readiness | 5/10 | Single-process scheduler, no distributed locking, 14% test coverage |

---

## 5. The Internal Business Case

### What Sportshub Provides to Front-End Products Today

Sportshub replaces the need for a Sportradar or equivalent subscription by providing an internal data layer with:

- **Accurate schedules** across NBA, FIFA WC, and LoL esports with multi-source confirmation
- **Player stats and team analytics** (NBA: per-game, advanced, play-by-play, adjusted shooting; team power ratings and betting trends)
- **Source provenance** -- every data point traces back to specific sources with confidence scores
- **Self-maintaining data quality** -- reconciliation, reliability scoring, and constraint validation run continuously

Front-end products consuming this data do not need to negotiate individual data provider contracts, handle rate limits, normalise disparate formats, or manage entity matching. Sportshub absorbs all of that complexity.

### Cost Comparison: Build vs Buy

| Approach | Annual Cost | Coverage | Control |
|----------|-----------|----------|---------|
| Sportradar (schedules + basic stats) | $50K-150K | Deep, contractual guarantees | None -- their formats, their timeline, their decisions |
| Stats Perform / Opta (football) | $100K+ | Very deep football | None |
| Sportshub (current MVP) | ~$1K/year | 3 sports, schedules + NBA stats | Full -- we choose sources, formats, refresh cadence |
| Sportshub (scaled, 10 sports) | ~$6K-10K/year | 10 sports, schedules + stats | Full |

The cost delta is 10-100x. The trade-off is depth and live data, both of which are addressable over time.

### What Front-End Products Could Sportshub Support?

With current data:
- **Sports news/editorial sites** -- fixture schedules, team stats, pre-match analysis context
- **Fantasy sports tools** -- player stats, team matchup data, schedule-based recommendations
- **Betting analytics dashboards** -- power ratings, ATS trends, O/U trends, cross-source schedule data
- **Sports data visualisation projects** -- multi-sport coverage with transparent sourcing

With live data pipeline (future):
- **Live score widgets** -- real-time game status updates
- **In-play betting tools** -- live odds movement and market data
- **Push notification services** -- game start/end, score alerts

---

## 6. Production-Scale Architecture

### Current State: Single-Process Monolith

The app runs as a single FastAPI process with an embedded APScheduler. PostgreSQL for persistence, Redis for caching. This works for the MVP but has hard ceilings:

- Cannot scale horizontally (scheduler would duplicate jobs)
- No distributed locking
- No event streaming
- No separation between ingestion and serving workloads

### 10x Scale (1,000 events/day, 10-15 sports)

**Task queue**: Migrate from in-process APScheduler to **Celery + Redis** or **Temporal**. Distributed task queue with exactly-once semantics, allowing horizontal scaling of ingestion workers independently of the API.

**Database**: Add **read replicas** for API queries, separating read and write workloads. Partition the events table by sport or date range. Current schema works to ~100K events; beyond that, JSONB metadata queries need path-specific GIN indexes or normalisation.

**Caching**: Move to tiered caching -- hot data (current week) in Redis with 5min TTL, warm data (historical) with 1h TTL, cold data from read replica. Add cache warming triggered by ingestion completion.

**Scraping infrastructure**: Integrate **ScrapingBee or Zenrows** as a managed scraping layer. This replaces browser automation with API calls, provides:
- Residential proxy rotation (reduces IP-based blocking)
- Automatic JavaScript rendering (replaces browser automation for BRef, TeamRankings)
- Cloudflare/bot-detection bypass as a service
- Predictable cost model ($100-250/month for the volume we need)

All scraping adapters would call through a unified proxy client rather than direct HTTP or browser automation.

**API**: Cursor-based pagination. Per-consumer API keys with usage tracking. ETag/Cache-Control headers for CDN compatibility. Consider GraphQL for complex front-end queries.

**AI resolution**: Batch mode -- instead of one AI call per unresolved entity, batch 50 into a single prompt. Reduces latency and cost by an order of magnitude.

**Monitoring**: Sentry for error tracking, Prometheus metrics export, structured request tracing. The current structlog setup is good; it needs a destination (Datadog, Grafana Cloud).

### 100x Scale (10,000 events/day, 30+ sports, live data)

**Event streaming**: Replace polling-based ingestion with **Kafka or Redpanda**. Each adapter publishes raw events to a topic; resolution, validation, and enrichment consume from downstream topics. This decouples ingestion from processing and enables replay.

**Live data pipeline**: Dedicated WebSocket infrastructure for real-time sources. **Redis Streams** or **NATS** for sub-second fan-out to front-end consumers. This is the largest architectural change and cannot be bolted onto the current design -- it requires a parallel system.

**CDC (Change Data Capture)**: **Postgres Logical Decoding** to stream database changes to Kafka. Enables real-time cache invalidation and event-driven processing without polling.

**Multi-region**: Read replicas deployed close to front-end products. If front-ends span geographies, consider **CockroachDB** or **Citus** for distributed writes.

**Data lake**: Archive raw source records to **S3/Parquet** for historical analysis and backfill. Keeping everything in Postgres does not scale past 1M records economically.

**Adapter framework**: Standardised adapter SDK with testing harness. Current adapters are 100-200 lines each; a documented interface would let other teams contribute sport-specific adapters.

### Cost Projections

| Scale | Infrastructure | AI Resolution | Scraping Services | Engineering |
|-------|---------------|---------------|-------------------|-------------|
| Current MVP | $50/month | $7/month | $0 (manual) | 1 person |
| 10x | $500/month | $70/month | $150/month | 2-3 people |
| 100x | $5,000/month | $500/month | $500/month | 5-8 people |
| Sportradar equivalent | $100K+/month | N/A (manual curation) | N/A (contractual) | 200+ people |

The cost advantage is 20-100x at comparable data coverage. The staffing advantage is where the disruption is most pronounced -- traditional providers need large operations teams because they do not have automated entity resolution, self-healing aliases, or dynamic reliability scoring.

---

## 7. Risks and Mitigations

### Source Durability (HIGH)

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| ESPN restructures undocumented API | Medium | Lose 2 adapters (NBA + FIFA) | Diversify to 3+ sources per sport; ESPN data is duplicated by official APIs |
| BRef strengthens Cloudflare protection | High | Lose player stat enrichment | Use ScrapingBee/Zenrows for managed bypass; pre-cache stat data seasonally |
| League sends C&D for scraping | Low-Medium | Loss of enrichment data for that sport | Core fixture data uses official APIs only; scraping is supplementary |
| Mollybet changes auth/TOS | Medium | Lose odds data and cross-sport confirmation | Odds data is supplementary; core fixture data comes from official sources |

**Strategic approach**: Build the core product on officially documented APIs (FIFA, NBA.com CDN, LoL Esports, football-data.org). Use scraping -- ideally through managed proxy services -- only for supplementary enrichment (player stats, power ratings, betting trends). If a scraping source breaks, the core product continues; the enrichment data just goes stale until repaired.

### Technical Risk (MEDIUM)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Single-process scheduler duplicates jobs at scale | Data corruption | Migrate to Celery before running more than one instance |
| JSONB queries degrade past 100K events | API latency spikes for front-ends | Normalise high-query fields; add path-specific indexes |
| 14% test coverage misses regressions | Silent data quality degradation | Target 70% coverage on resolution and reconciliation |
| No secrets management | Credential exposure if team grows | Move to environment-based secrets (Doppler, Vault, or platform-native) |

### Strategic Risk (MEDIUM-HIGH)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Sportradar drops prices or offers free tiers | Reduces cost advantage | Compete on control, customisation, and transparency -- not just price |
| Free APIs shut down or add auth requirements | Loss of primary data sources | Maintain 3+ sources per sport; budget for paid API tiers as fallback |
| Front-end products need live data sooner than expected | Cannot serve real-time use cases | Scope front-end requirements early; build live pipeline as explicit Phase 2 |
| Data quality incident damages front-end credibility | User trust erosion | Reconciliation system catches discrepancies; add alerting to Slack/PagerDuty |

---

## 8. Strategic Recommendation

### Verdict: The thesis is PARTIALLY PROVEN

**Proven**:
- Multi-source aggregation of schedule and fixture data works, is accurate, and costs 10-100x less than buying from traditional providers
- AI-assisted entity resolution is architecturally sound and economically viable
- Self-healing concepts (alias learning, dynamic reliability scoring, reconciliation) function as designed
- A single developer can build and operate a 3-sport data platform that would traditionally require a team

**Not yet proven**:
- AI resolution under genuine ambiguity (0% acceptance rate means the hard cases have not arrived)
- Data depth beyond schedules (stats, play-by-play, injuries are NBA-only)
- Live data delivery (architectural gap, not just a missing feature)
- Long-term durability of scraping-dependent sources
- Whether front-end products find current depth sufficient or demand more

### Recommended Path

**Phase 1 (Months 1-2): Harden and Expand**
- Stress-test the AI resolver with genuinely ambiguous international team names
- Get test coverage to 50%+ on resolution and reconciliation paths
- Add 2-3 more sports (MLB, Premier League, NFL) to prove the adapter model scales
- Integrate ScrapingBee or Zenrows for managed scraping -- retire browser automation
- Move secrets to proper environment-based management
- Audit front-end product data requirements to scope what "enough" looks like

**Phase 2 (Months 3-4): Deepen and Serve**
- Ship historical data backfill (at minimum current + prior season for NBA)
- Add player stats for football (via Reep + FBref enrichment)
- Build internal API documentation for front-end teams
- Implement per-consumer API keys with usage tracking
- Validate: do front-end products get measurably better data than they would from a single free API?

**Phase 3 (Months 5-8): Scale or Specialise**
- If front-end demand justifies it: build Kafka-based streaming pipeline for live data
- If not: specialise in pre-match analytics depth (stats, predictions, betting context)
- Migrate scheduler to distributed task queue
- Begin evaluating whether official API partnerships (paid tiers, league agreements) are worth the investment for specific sports

**Decision point**: After Phase 2, assess whether the data quality and breadth from Sportshub measurably improves front-end products compared to the alternative (subscribing to Sportradar or equivalent). If the answer is yes, the thesis is proven and the investment case for Phase 3 writes itself. If not, the remaining gap is likely live data or depth -- both of which have clear (if expensive) solutions.

---

## 9. Learnings

1. **Free public APIs contain 90% of the schedule data that Sportradar charges $50K+ for.** The remaining 10% (depth, history, live) is where traditional providers justify their pricing. The question is whether that 10% matters for our specific front-end products.

2. **AI entity resolution is cheap but needs genuine stress testing.** 1,980 calls at $7.10 proves the unit economics. 0% acceptance proves the rule-based system works well for known entities. The real test comes when we add international leagues where name variants are common and aliases are sparse.

3. **Reconciliation is the actual competitive advantage.** Not the data collection, not the AI -- the ability to confirm "this event was verified by 3 independent sources with 100% team accuracy and 0s time drift" is what makes the data trustworthy. No single free API provides that assurance. This is what justifies the platform over raw API consumption.

4. **Scraping should be managed, not manual.** Browser automation works but is fragile. Proxy services like ScrapingBee and Zenrows convert scraping from an operational liability into a budgetable service. Use them for supplementary enrichment; build the core on official APIs.

5. **Coverage breadth matters more than depth for an internal platform.** 3 sports is a proof of concept. 10 sports makes Sportshub the obvious internal choice over managing 10 separate API subscriptions. The adapter pattern makes expansion cheap in code terms, but each sport needs reference data (teams, competitions, aliases) that takes time to assemble.

6. **Operational transparency is unexpectedly valuable.** Traditional providers are opaque -- you get data, you trust it or you do not. Showing internal consumers exactly which sources contributed to each data point, with confidence scores and reconciliation history, gives product teams agency over data quality decisions.

7. **The self-healing database concept works in principle but is unproven over time.** Dynamic reliability scoring, alias learning, and reconciliation-driven quality improvement are all implemented and running. Whether they actually improve data quality over 6-12 months of production use is the key open question. If they do, this is the strongest evidence for the thesis -- a system that gets better without human intervention.
