"""Event endpoints: list and detail."""

import math
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.api.dependencies import get_db, get_pagination, get_redis, verify_api_key
from sportshub.api.v1.schemas import (
    CompetitionSummary,
    EventDetailResponse,
    EventEnrichment,
    EventListItem,
    EventListResponse,
    EventOddsResponse,
    InjuryReport,
    MarketOdds,
    OddsSelection,
    PlayerBio,
    PlayerPropOdds,
    PlayerSeasonStats,
    RosterPlayer,
    SourceInfo,
    SourceOdds,
    TeamEnrichment,
    TeamStats,
    TeamSummary,
)
from sportshub.cache.client import RedisClient
from sportshub.cache.keys import TTL_EVENTS_DETAIL, TTL_EVENTS_LIST, events_detail_key, events_list_key
from sportshub.db.repositories import CompetitionRepository, EventRepository, SourceRecordRepository, TeamRepository
from sportshub.models.common import PaginationMeta, PaginationParams, Sport

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(verify_api_key)])


def _parse_enrichment(
    metadata: dict,
    home_abbr: str | None = None,
    away_abbr: str | None = None,
) -> EventEnrichment | None:
    """Extract typed enrichment data from the event metadata JSONB.

    Handles two storage formats:
    - Abbreviation-keyed: teams.{BKN, CHA} with stats at root level
    - Role-keyed: teams.{home, away} with nested stats dict
    """
    teams = metadata.get("teams")
    if not teams:
        return None

    def _parse_team(team_data: dict | None) -> TeamEnrichment | None:
        if not team_data:
            return None

        # Parse team stats — may be nested under 'stats' or at root level
        stats_data = team_data.get("stats")
        if stats_data:
            stats = TeamStats(
                wins=stats_data.get("wins"),
                losses=stats_data.get("losses"),
                win_pct=stats_data.get("win_pct"),
                points_per_game=stats_data.get("points_per_game") or stats_data.get("ppg"),
                rebounds_per_game=stats_data.get("rebounds_per_game") or stats_data.get("rpg"),
                assists_per_game=stats_data.get("assists_per_game") or stats_data.get("apg"),
                fg_pct=stats_data.get("fg_pct"),
                fg3_pct=stats_data.get("fg3_pct"),
                ft_pct=stats_data.get("ft_pct"),
            )
        elif "ppg" in team_data or "win_pct" in team_data:
            # Stats stored at team root level (ESPN enrichment format)
            record = team_data.get("record", "")
            wins = losses = None
            if record and "-" in record:
                parts = record.split("-")
                try:
                    wins, losses = int(parts[0]), int(parts[1])
                except (ValueError, IndexError):
                    pass
            stats = TeamStats(
                wins=wins,
                losses=losses,
                win_pct=team_data.get("win_pct"),
                points_per_game=team_data.get("ppg"),
                rebounds_per_game=team_data.get("rpg"),
                assists_per_game=team_data.get("apg"),
                fg_pct=team_data.get("fg_pct"),
                fg3_pct=team_data.get("fg3_pct"),
                ft_pct=team_data.get("ft_pct"),
            )
        else:
            stats = None

        # Parse roster
        roster = []
        for p in team_data.get("roster", []):
            # Player-level season stats (lowercase keys from NBA.com/ESPN)
            stats_d = p.get("season_stats") or p.get("stats") or {}

            # Bio may be nested or at player root level
            bio_data = p.get("bio") or {}
            # If bio fields are at player root (ESPN format), build bio from there
            if not bio_data and ("height" in p or "age" in p):
                bio_data = p

            roster.append(RosterPlayer(
                player_id=p.get("player_id"),
                name=p.get("name", ""),
                jersey_number=p.get("jersey_number") or p.get("jersey"),
                position=p.get("position"),
                bio=PlayerBio(
                    height=bio_data.get("height"),
                    weight=bio_data.get("weight"),
                    age=bio_data.get("age"),
                    birthdate=bio_data.get("birthdate"),
                    college=bio_data.get("college"),
                    headshot_url=bio_data.get("headshot_url") or bio_data.get("headshot"),
                ) if bio_data else None,
                season_stats=PlayerSeasonStats(
                    games_played=stats_d.get("games_played") or stats_d.get("gp") or stats_d.get("GP"),
                    minutes_per_game=stats_d.get("minutes_per_game") or stats_d.get("min") or stats_d.get("MIN"),
                    points_per_game=stats_d.get("points_per_game") or stats_d.get("pts") or stats_d.get("PTS"),
                    rebounds_per_game=stats_d.get("rebounds_per_game") or stats_d.get("reb") or stats_d.get("REB"),
                    assists_per_game=stats_d.get("assists_per_game") or stats_d.get("ast") or stats_d.get("AST"),
                    steals_per_game=stats_d.get("steals_per_game") or stats_d.get("stl") or stats_d.get("STL"),
                    blocks_per_game=stats_d.get("blocks_per_game") or stats_d.get("blk") or stats_d.get("BLK"),
                    turnovers_per_game=stats_d.get("turnovers_per_game") or stats_d.get("tov") or stats_d.get("TOV"),
                    fg_pct=stats_d.get("fg_pct") or stats_d.get("FG_PCT"),
                    fg3_pct=stats_d.get("fg3_pct") or stats_d.get("FG3_PCT"),
                    ft_pct=stats_d.get("ft_pct") or stats_d.get("FT_PCT"),
                    plus_minus=stats_d.get("plus_minus") or stats_d.get("PLUS_MINUS"),
                ) if stats_d else None,
            ))

        # Parse injuries — may be at team level or top-level metadata
        injuries = []
        for inj in team_data.get("injuries", []):
            injuries.append(InjuryReport(
                player_name=inj.get("player_name", ""),
                position=inj.get("position"),
                status=inj.get("status", "Unknown"),
                injury=inj.get("injury"),
                details=inj.get("details"),
            ))

        return TeamEnrichment(
            record=team_data.get("record"),
            stats=stats,
            roster=roster,
            injuries=injuries,
        )

    # Resolve team data — try role keys first, then abbreviation keys
    home_data = teams.get("home")
    away_data = teams.get("away")

    if not home_data and not away_data:
        # Teams keyed by abbreviation — use home/away abbreviations to map
        team_keys = list(teams.keys())
        if home_abbr and home_abbr in teams:
            home_data = teams[home_abbr]
        elif len(team_keys) >= 1:
            home_data = teams[team_keys[0]]

        if away_abbr and away_abbr in teams:
            away_data = teams[away_abbr]
        elif len(team_keys) >= 2:
            away_data = teams[team_keys[1]]

    enrichment_sources = metadata.get("enrichment_sources", [])
    enriched_at_str = metadata.get("enriched_at")
    enriched_at = None
    if enriched_at_str:
        try:
            enriched_at = datetime.fromisoformat(enriched_at_str)
        except (ValueError, TypeError):
            pass

    return EventEnrichment(
        home_team=_parse_team(home_data),
        away_team=_parse_team(away_data),
        enriched_at=enriched_at,
        enrichment_sources=enrichment_sources,
    )


async def _build_team_summary(team_repo: TeamRepository, team_id: UUID) -> TeamSummary:
    team = await team_repo.get_by_id(team_id)
    if not team:
        return TeamSummary(id=team_id, name="Unknown", short_name="UNK", abbreviation="UNK", sport="")
    return TeamSummary(
        id=team.id, name=team.name, short_name=team.short_name,
        abbreviation=team.abbreviation, sport=team.sport.value,
    )


async def _build_competition_summary(comp_repo: CompetitionRepository, comp_id: UUID) -> CompetitionSummary:
    comp = await comp_repo.get_by_id(comp_id)
    if not comp:
        return CompetitionSummary(id=comp_id, name="Unknown", short_name="UNK", sport="")
    return CompetitionSummary(
        id=comp.id, name=comp.name, short_name=comp.short_name,
        sport=comp.sport.value, season=comp.season,
    )


@router.get("", response_model=EventListResponse)
async def list_events(
    sport: str | None = Query(None, description="Filter by sport: nba, lol, football"),
    team_id: UUID | None = Query(None),
    competition_id: UUID | None = Query(None),
    status_filter: str = Query("scheduled", alias="status"),
    from_dt: datetime | None = Query(None, alias="from"),
    to_dt: datetime | None = Query(None, alias="to"),
    sort: str = Query("scheduled_at"),
    pagination: PaginationParams = Depends(get_pagination),
    session: AsyncSession = Depends(get_db),
    cache: RedisClient = Depends(get_redis),
) -> EventListResponse:
    # Defaults
    if from_dt is None:
        from_dt = datetime.utcnow()
    if to_dt is None:
        to_dt = datetime.utcnow() + timedelta(days=30)

    sport_enum = Sport(sport) if sport else None

    event_repo = EventRepository(session)
    team_repo = TeamRepository(session)
    comp_repo = CompetitionRepository(session)

    events, total = await event_repo.get_list(
        sport=sport_enum,
        team_id=team_id,
        competition_id=competition_id,
        status=status_filter,
        from_dt=from_dt,
        to_dt=to_dt,
        sort=sort,
        page=pagination.page,
        per_page=pagination.per_page,
    )

    items = []
    for event in events:
        home = await _build_team_summary(team_repo, event.home_team_id)
        away = await _build_team_summary(team_repo, event.away_team_id)
        comp = await _build_competition_summary(comp_repo, event.competition_id)
        items.append(EventListItem(
            id=event.id, sport=event.sport.value,
            home_team=home, away_team=away, competition=comp,
            scheduled_at=event.scheduled_at, status=event.status.value,
            match_format=event.match_format.value, venue=event.venue,
            confidence_score=event.confidence_score, source_count=event.source_count,
            created_at=event.created_at, updated_at=event.updated_at,
        ))

    return EventListResponse(
        data=items,
        pagination=PaginationMeta(
            page=pagination.page,
            per_page=pagination.per_page,
            total=total,
            total_pages=math.ceil(total / pagination.per_page) if total > 0 else 0,
        ),
    )


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: UUID,
    session: AsyncSession = Depends(get_db),
    cache: RedisClient = Depends(get_redis),
) -> EventDetailResponse:
    event_repo = EventRepository(session)
    team_repo = TeamRepository(session)
    comp_repo = CompetitionRepository(session)
    source_repo = SourceRecordRepository(session)

    event = await event_repo.get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Event not found"}})

    home = await _build_team_summary(team_repo, event.home_team_id)
    away = await _build_team_summary(team_repo, event.away_team_id)
    comp = await _build_competition_summary(comp_repo, event.competition_id)
    source_records = await source_repo.get_by_event(event_id)

    sources = [
        SourceInfo(
            source_id=sr.source_id, source_event_id=sr.source_event_id,
            raw_home_team=sr.raw_home_team, raw_away_team=sr.raw_away_team,
            scheduled_at=sr.scheduled_at, venue=sr.venue,
            match_confidence=sr.match_confidence, created_at=sr.created_at,
        )
        for sr in source_records
    ]

    # Parse typed enrichment from raw metadata (rosters, stats, injuries)
    enrichment = _parse_enrichment(
        event.metadata,
        home_abbr=home.abbreviation,
        away_abbr=away.abbreviation,
    ) if event.metadata else None

    return EventDetailResponse(
        id=event.id, sport=event.sport.value,
        home_team=home, away_team=away, competition=comp,
        scheduled_at=event.scheduled_at, status=event.status.value,
        match_format=event.match_format.value, venue=event.venue,
        confidence_score=event.confidence_score, source_count=event.source_count,
        sources=sources, enrichment=enrichment, metadata=event.metadata,
        created_at=event.created_at, updated_at=event.updated_at,
    )


# ─── Betting / Odds Endpoints ───────────────────────────────────────────────

_BETTING_SOURCES = {"cloudbet_basketball", "cloudbet_soccer", "cloudbet_league_of_legends",
                    "mollybet_fb", "mollybet_basket", "mollybet_esports"}


def _extract_source_odds(raw_data: dict, source_id: str, source_event_id: str, created_at: datetime) -> SourceOdds:
    """Extract structured odds from a source_record's raw_data JSONB."""
    odds = raw_data.get("odds", {})
    markets: list[MarketOdds] = []
    props: list[PlayerPropOdds] = []

    # Moneyline / Match Winner
    ml = odds.get("moneyline", {})
    if ml:
        sels = [OddsSelection(outcome=k, price=v) for k, v in ml.items() if isinstance(v, (int, float))]
        if sels:
            markets.append(MarketOdds(market_type="moneyline", selections=sels))

    # Spread / Handicap
    spread = odds.get("spread", {})
    if spread:
        sels = []
        for side, data in spread.items():
            if isinstance(data, dict) and "price" in data:
                sels.append(OddsSelection(outcome=side, price=data["price"], line=data.get("line")))
        if sels:
            markets.append(MarketOdds(market_type="spread", selections=sels))

    # Totals
    total = odds.get("total", {})
    if total:
        sels = []
        for side, data in total.items():
            if isinstance(data, dict) and "price" in data:
                sels.append(OddsSelection(outcome=side, price=data["price"], line=data.get("line")))
        if sels:
            markets.append(MarketOdds(market_type="total", selections=sels))

    # BTTS
    btts = odds.get("btts", {})
    if btts:
        sels = [OddsSelection(outcome=k, price=v) for k, v in btts.items() if isinstance(v, (int, float))]
        if sels:
            markets.append(MarketOdds(market_type="btts", selections=sels))

    # Player Props
    for prop in odds.get("player_props", []):
        props.append(PlayerPropOdds(
            market=prop.get("market", ""),
            player=prop.get("player", ""),
            outcome=prop.get("outcome", ""),
            price=prop.get("price", 0),
            min_stake=prop.get("min_stake"),
            max_stake=prop.get("max_stake"),
        ))

    return SourceOdds(
        source_id=source_id,
        source_event_id=source_event_id,
        markets=markets,
        player_props=props,
        fetched_at=created_at,
    )


@router.get("/{event_id}/odds", response_model=EventOddsResponse)
async def get_event_odds(
    event_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> EventOddsResponse:
    """Get combined odds from all betting sources for a canonical event.

    Returns structured odds data from Cloudbet and Mollybet, allowing
    frontends to display odds from multiple bookmakers for the same event.
    """
    event_repo = EventRepository(session)
    team_repo = TeamRepository(session)
    source_repo = SourceRecordRepository(session)

    event = await event_repo.get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Event not found"}})

    home = await _build_team_summary(team_repo, event.home_team_id)
    away = await _build_team_summary(team_repo, event.away_team_id)

    # Get all source records linked to this event
    all_records = await source_repo.get_by_event(event_id)
    betting_records = [r for r in all_records if r.source_id in _BETTING_SOURCES]

    sources = []
    for sr in betting_records:
        raw = sr.raw_data if hasattr(sr, "raw_data") else {}
        if not raw:
            continue
        source_odds = _extract_source_odds(raw, sr.source_id, sr.source_event_id, sr.created_at)
        if source_odds.markets or source_odds.player_props:
            sources.append(source_odds)

    return EventOddsResponse(
        event_id=event.id,
        sport=event.sport.value,
        home_team=home,
        away_team=away,
        scheduled_at=event.scheduled_at,
        sources=sources,
        source_count=len(sources),
    )


@router.get("/by-source/{source_id}/{source_event_id}", response_model=EventDetailResponse)
async def get_event_by_source(
    source_id: str,
    source_event_id: str,
    session: AsyncSession = Depends(get_db),
    cache: RedisClient = Depends(get_redis),
) -> EventDetailResponse:
    """Look up a canonical event by source-specific event ID.

    Allows Cloudbet and Mollybet frontends to find the canonical event
    using their own event identifiers, then access the combined data.
    """
    source_repo = SourceRecordRepository(session)
    event_repo = EventRepository(session)
    team_repo = TeamRepository(session)
    comp_repo = CompetitionRepository(session)

    # Find the source record
    sr = await source_repo.get_by_source_event(source_id, source_event_id)
    if not sr or not sr.event_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": f"No matched event for {source_id}/{source_event_id}"}},
        )

    # Load the canonical event (reuse the detail logic)
    event = await event_repo.get_by_id(sr.event_id)
    if not event:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Event not found"}})

    home = await _build_team_summary(team_repo, event.home_team_id)
    away = await _build_team_summary(team_repo, event.away_team_id)
    comp = await _build_competition_summary(comp_repo, event.competition_id)
    source_records = await source_repo.get_by_event(sr.event_id)

    sources = [
        SourceInfo(
            source_id=s.source_id, source_event_id=s.source_event_id,
            raw_home_team=s.raw_home_team, raw_away_team=s.raw_away_team,
            scheduled_at=s.scheduled_at, venue=s.venue,
            match_confidence=s.match_confidence, created_at=s.created_at,
        )
        for s in source_records
    ]

    enrichment = _parse_enrichment(
        event.metadata,
        home_abbr=home.abbreviation,
        away_abbr=away.abbreviation,
    ) if event.metadata else None

    return EventDetailResponse(
        id=event.id, sport=event.sport.value,
        home_team=home, away_team=away, competition=comp,
        scheduled_at=event.scheduled_at, status=event.status.value,
        match_format=event.match_format.value, venue=event.venue,
        confidence_score=event.confidence_score, source_count=event.source_count,
        sources=sources, enrichment=enrichment, metadata=event.metadata,
        created_at=event.created_at, updated_at=event.updated_at,
    )
