"""Entity resolution pipeline — orchestrates normalize, match, merge/create."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from sportshub.db.repositories import (
    CompetitionRepository,
    EventRepository,
    SourceRecordRepository,
)
from sportshub.db.repositories.team_repo import TeamRepository
from sportshub.db.tables import events_table
from sportshub.models import Event, SourceRecord
from sportshub.models.common import Sport
from sportshub.resolution.matcher import (
    THRESHOLD_AUTO_MATCH,
    THRESHOLD_TENTATIVE,
    find_matching_event,
)
from sportshub.resolution.merger import create_event_from_source, merge_source_into_event
from sportshub.resolution.normalizer import TeamResolver
from sportshub.resolution.reliability import DynamicReliabilityScorer
from sportshub.resolution.timezone import VenueTimezoneResolver, normalize_to_utc
from sportshub.validation.engine import ConstraintEngine

logger = structlog.get_logger()


@dataclass
class ResolutionResult:
    """Summary of a resolution run."""

    matched: int = 0
    created: int = 0
    unresolved: int = 0
    errors: list[str] = field(default_factory=list)


class ResolutionPipeline:
    """Orchestrates the full entity resolution flow."""

    def __init__(
        self,
        session: AsyncSession,
        team_resolver: TeamResolver,
        venue_tz_resolver: VenueTimezoneResolver | None = None,
        reliability_scorer: DynamicReliabilityScorer | None = None,
        llm_resolver=None,  # LLMEventResolver | None
    ) -> None:
        self._session = session
        self._resolver = team_resolver
        self._venue_tz_resolver = venue_tz_resolver or VenueTimezoneResolver()
        self._reliability_scorer = reliability_scorer
        self._llm_resolver = llm_resolver
        self._event_repo = EventRepository(session)
        self._source_repo = SourceRecordRepository(session)
        self._comp_repo = CompetitionRepository(session)
        self._team_repo = TeamRepository(session)
        self._constraint_engine = ConstraintEngine()
        self._priority_maps: dict[str, dict[str, float]] = {}
        # Pre-loaded LLM candidate lists keyed by sport value string
        self._llm_team_candidates: dict[str, list[dict]] = {}
        self._llm_comp_candidates: dict[str, list[dict]] = {}

    async def process_source_records(
        self,
        records: list[SourceRecord],
    ) -> ResolutionResult:
        """Process a batch of unmatched source records through the resolution pipeline.

        For each record:
        1. Resolve home and away team names to UUIDs
        2. Resolve competition
        3. Load candidate canonical events (same sport, same teams, 48h window)
        4. Find best match via confidence scoring
        5. If match >= 0.70: merge into existing event
        6. If no match: create new canonical event
        7. Link source_record.event_id
        """
        result = ResolutionResult()

        # Pre-load dynamic priority maps for all sports in this batch
        sports_in_batch = {r.sport for r in records}
        if self._reliability_scorer:
            for sport_val in sports_in_batch:
                sport_key = sport_val.value if isinstance(sport_val, Sport) else str(sport_val)
                try:
                    self._priority_maps[sport_key] = (
                        await self._reliability_scorer.get_all_priorities(sport_key)
                    )
                    logger.debug(
                        "reliability_priorities_loaded",
                        sport=sport_key,
                        priorities=self._priority_maps[sport_key],
                    )
                except Exception as e:
                    logger.warning(
                        "reliability_load_failed",
                        sport=sport_key,
                        error=str(e),
                    )
                    # Will fall back to hardcoded in matcher/merger

        # Pre-load LLM candidate lists (teams + competitions) per sport
        if self._llm_resolver:
            for sport_val in sports_in_batch:
                sport_key = sport_val.value if isinstance(sport_val, Sport) else str(sport_val)
                sport_enum = sport_val if isinstance(sport_val, Sport) else Sport(sport_val)
                try:
                    teams, _ = await self._team_repo.get_all(sport=sport_enum, per_page=500)
                    self._llm_team_candidates[sport_key] = [
                        {"id": str(t.id), "name": t.name, "abbreviation": t.abbreviation}
                        for t in teams
                    ]
                    comps, _ = await self._comp_repo.get_all(sport=sport_enum, per_page=100)
                    self._llm_comp_candidates[sport_key] = [
                        {"id": str(c.id), "name": c.name, "short_name": c.short_name}
                        for c in comps
                    ]
                except Exception as e:
                    logger.warning("llm_candidates_load_failed", sport=sport_key, error=str(e))

        for record in records:
            try:
                await self._process_single_record(record, result)
            except Exception as e:
                error_msg = f"Error processing record {record.id}: {e}"
                logger.error("resolution_error", record_id=str(record.id), error=str(e))
                result.errors.append(error_msg)

        logger.info(
            "resolution_complete",
            matched=result.matched,
            created=result.created,
            unresolved=result.unresolved,
            errors=len(result.errors),
        )
        return result

    async def _process_single_record(
        self,
        record: SourceRecord,
        result: ResolutionResult,
    ) -> None:
        """Process a single source record through the pipeline."""
        # Step 1: Resolve teams
        home_team_id = self._resolver.resolve(
            record.raw_home_team, record.source_id, record.sport
        )
        away_team_id = self._resolver.resolve(
            record.raw_away_team, record.source_id, record.sport
        )

        # Fall back to LLM for unresolved teams
        if self._llm_resolver and self._llm_resolver.is_healthy():
            sport_key = record.sport.value if isinstance(record.sport, Sport) else str(record.sport)
            team_candidates = self._llm_team_candidates.get(sport_key, [])
            if not home_team_id and team_candidates:
                home_team_id = await self._llm_resolver.resolve_team_name(
                    record.raw_home_team, record.sport, record.id, record.source_id, team_candidates
                )
            if not away_team_id and team_candidates:
                away_team_id = await self._llm_resolver.resolve_team_name(
                    record.raw_away_team, record.sport, record.id, record.source_id, team_candidates
                )

        if not home_team_id or not away_team_id:
            result.unresolved += 1
            return

        # Step 1b: Normalize scheduled_at to UTC
        source_tz = record.source_timezone or "UTC"
        record.scheduled_at = normalize_to_utc(record.scheduled_at, source_tz)

        # Step 1c: Resolve venue timezone
        venue_timezone = self._venue_tz_resolver.resolve_venue_timezone(
            record.venue, record.sport
        )
        if venue_timezone:
            logger.debug(
                "venue_timezone_resolved",
                record_id=str(record.id),
                venue=record.venue,
                venue_timezone=venue_timezone,
            )

        # Step 2: Resolve competition
        competition_id = await self._resolve_competition(record)

        # Step 3: Load candidate events
        time_window = (
            record.scheduled_at - timedelta(hours=24),
            record.scheduled_at + timedelta(hours=24),
        )
        candidates = await self._event_repo.get_upcoming_by_sport_and_teams(
            sport=record.sport,
            team_ids={home_team_id, away_team_id},
            time_window=time_window,
        )

        # Step 4: Find best match
        sport_key = record.sport.value if isinstance(record.sport, Sport) else str(record.sport)
        priority_map = self._priority_maps.get(sport_key)

        matched_event, confidence = find_matching_event(
            record_home_team_id=home_team_id,
            record_away_team_id=away_team_id,
            record=record,
            existing_events=candidates,
            priority_map=priority_map,
        )

        # Promote tentative matches via LLM confirmation
        if (
            matched_event
            and THRESHOLD_TENTATIVE <= confidence < THRESHOLD_AUTO_MATCH
            and self._llm_resolver
            and self._llm_resolver.is_healthy()
        ):
            llm_match, llm_confidence = await self._llm_resolver.resolve_event_match(
                record, [matched_event]
            )
            if llm_match and llm_confidence >= THRESHOLD_AUTO_MATCH:
                confidence = llm_confidence

        if matched_event and confidence >= THRESHOLD_TENTATIVE:
            # Step 5: Merge into existing event
            existing_records = await self._source_repo.get_by_event(matched_event.id)
            updated_event = merge_source_into_event(
                matched_event, record, confidence, existing_records,
                priority_map=priority_map,
            )
            # Backfill venue_timezone if the existing event lacks one
            if venue_timezone and not updated_event.venue_timezone:
                updated_event.venue_timezone = venue_timezone
            await self._event_repo.update(updated_event)
            await self._source_repo.update_event_link(record.id, matched_event.id, confidence)

            # Run constraint validation on the merged event
            await self._run_validation(matched_event.id, updated_event)

            result.matched += 1

            if confidence < THRESHOLD_AUTO_MATCH:
                logger.warning(
                    "tentative_match",
                    record_id=str(record.id),
                    event_id=str(matched_event.id),
                    confidence=confidence,
                )
        else:
            # Step 6: Create new canonical event
            if not competition_id:
                logger.warning(
                    "no_competition_resolved",
                    record_id=str(record.id),
                    raw_competition=record.raw_competition,
                )
                result.unresolved += 1
                return

            new_event = create_event_from_source(
                record=record,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                competition_id=competition_id,
                confidence=1.0,  # First source always gets full confidence
            )
            # Attach resolved venue timezone to the canonical event
            if venue_timezone:
                new_event.venue_timezone = venue_timezone

            try:
                new_event = await self._event_repo.create(new_event)
                await self._source_repo.update_event_link(record.id, new_event.id, 1.0)

                # Run constraint validation on the newly created event
                await self._run_validation(new_event.id, new_event)

                result.created += 1
            except Exception as e:
                # Likely a uniqueness violation — event already exists
                logger.warning(
                    "event_creation_conflict",
                    record_id=str(record.id),
                    error=str(e),
                )
                result.errors.append(str(e))

    async def _run_validation(self, event_id: UUID, event) -> None:
        """Run constraint validation and reduce confidence if errors found."""
        try:
            violations = await self._constraint_engine.validate_event(
                event_id, self._session
            )
            error_violations = [v for v in violations if v.severity == "error"]
            if error_violations:
                new_score = max(0.0, event.confidence_score - 0.3)
                await self._session.execute(
                    sa.update(events_table)
                    .where(events_table.c.id == event_id)
                    .values(confidence_score=new_score)
                )
                logger.warning(
                    "validation_confidence_reduced",
                    event_id=str(event_id),
                    original_score=event.confidence_score,
                    new_score=new_score,
                    error_count=len(error_violations),
                )
        except Exception:
            logger.exception(
                "validation_failed_in_pipeline",
                event_id=str(event_id),
            )

    async def _resolve_competition(self, record: SourceRecord) -> UUID | None:
        """Try to resolve the raw competition name to a competition UUID."""
        # Simple lookup by short_name
        comp = await self._comp_repo.find_by_name(record.raw_competition, record.sport)
        if comp:
            return comp.id

        # Try partial match on common patterns
        raw = record.raw_competition.lower().replace("-", " ").replace("_", " ")
        for keyword, short_name in [
            ("regular season", "NBA-RS"),
            ("nba", "NBA-RS"),
            ("playoff", "NBA-PO"),
            ("lck", "LCK"),
            ("lpl", "LPL"),
            ("lec", "LEC"),
            ("lcs", "LCS"),
            ("msi", "MSI"),
            ("worlds", "WORLDS"),
            ("fifa world cup", "WC2026"),
            ("world cup 2026", "WC2026"),
            ("wc 2026", "WC2026"),
            ("fifa", "WC2026"),
        ]:
            if keyword in raw:
                comp = await self._comp_repo.find_by_name(short_name, record.sport)
                if comp:
                    return comp.id

        # Fall back to LLM for unrecognised competition strings
        if self._llm_resolver and self._llm_resolver.is_healthy():
            sport_key = record.sport.value if isinstance(record.sport, Sport) else str(record.sport)
            comp_candidates = self._llm_comp_candidates.get(sport_key, [])
            if comp_candidates:
                resolved = await self._llm_resolver.resolve_competition(
                    record.raw_competition, record.sport, record.id, comp_candidates
                )
                if resolved:
                    return resolved

        logger.warning(
            "unresolved_competition",
            raw_competition=record.raw_competition,
            source_id=record.source_id,
        )
        return None
