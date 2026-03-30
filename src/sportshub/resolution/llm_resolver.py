"""LLM-powered fallback resolver for long-tail entity resolution using Claude Haiku."""

import collections
import json
import time
from datetime import datetime, timedelta
from uuid import UUID

import re

import anthropic
import structlog


def _extract_json(text: str) -> str:
    """Strip markdown code fences if present, returning raw JSON text."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text

from sportshub.db.repositories.llm_repo import LLMResolutionRepository
from sportshub.db.repositories.team_repo import TeamRepository
from sportshub.models.common import Sport
from sportshub.models.event import Event
from sportshub.models.llm_resolution import LLMResolution
from sportshub.models.source import SourceRecord
from sportshub.models.team import TeamAlias
from sportshub.resolution.normalizer import normalize_team_name

logger = structlog.get_logger()

_CONFIDENCE_ACCEPT = 0.70


class LLMEventResolver:
    """Uses Claude Haiku to resolve long-tail entity matching failures.

    Wraps three resolution tasks:
    - team_name: map an unrecognised raw team name to a canonical team UUID
    - competition: map an unrecognised raw competition string to a competition UUID
    - event_match: promote a tentative (0.50–0.70) rule-based match to auto-match

    Includes an in-memory circuit breaker: if the rolling acceptance rate or
    average latency exceed thresholds, the resolver enters a cooldown period
    and returns None/no-match until it recovers.
    """

    def __init__(
        self,
        api_key: str,
        session,  # AsyncSession — kept as Any to avoid circular import
        model: str = "claude-haiku-4-5-20251001",
        acceptance_threshold: float = 0.40,
        rolling_window: int = 20,
        cooldown_minutes: int = 30,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._session = session
        self._model = model
        self._acceptance_threshold = acceptance_threshold
        self._cooldown_minutes = cooldown_minutes
        self._outcomes: collections.deque = collections.deque(maxlen=rolling_window)
        self._cooldown_until: datetime | None = None
        self._llm_repo = LLMResolutionRepository(session)

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Return True if LLM calls should proceed (circuit closed)."""
        if self._cooldown_until is None:
            return True
        if datetime.utcnow() >= self._cooldown_until:
            logger.info("llm_circuit_closed", model=self._model)
            self._cooldown_until = None
            self._outcomes.clear()
            return True
        return False

    def _record_outcome(self, accepted: bool, latency_ms: int) -> None:
        """Append outcome to the rolling window and open circuit if thresholds are breached."""
        self._outcomes.append((accepted, latency_ms))
        n = len(self._outcomes)
        if n < self._outcomes.maxlen:  # type: ignore[operator]
            return  # not enough data yet
        accepted_count = sum(1 for a, _ in self._outcomes if a)
        rate = accepted_count / n
        avg_latency = sum(lat for _, lat in self._outcomes) / n
        if (rate < self._acceptance_threshold or avg_latency > 10_000) and not self._cooldown_until:
            self._cooldown_until = datetime.utcnow() + timedelta(minutes=self._cooldown_minutes)
            logger.warning(
                "llm_circuit_open",
                model=self._model,
                acceptance_rate=round(rate, 3),
                avg_latency_ms=int(avg_latency),
                cooldown_until=self._cooldown_until.isoformat(),
            )

    # ------------------------------------------------------------------
    # Resolution methods
    # ------------------------------------------------------------------

    async def resolve_team_name(
        self,
        raw_name: str,
        sport: Sport,
        source_record_id: UUID,
        source_id: str,
        candidates: list[dict],
    ) -> UUID | None:
        """Try to match a raw team name to a candidate team.

        On success, persists a new alias so future rule-based lookups hit the cache.
        """
        if not candidates:
            return None

        start = time.monotonic()
        candidate_lines = "\n".join(
            f"  {i}. name={c['name']!r}, abbreviation={c.get('abbreviation', '')!r}, id={c['id']}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            f"You are a sports data entity resolver. Match the raw team name to the best candidate.\n\n"
            f"Sport: {sport.value}\n"
            f"Raw name: {raw_name!r}\n\n"
            f"Candidates:\n{candidate_lines}\n\n"
            'Respond ONLY with JSON: {"matched_id": "<uuid or null>", "confidence": 0.0, "reasoning": "..."}'
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            parsed = json.loads(_extract_json(response.content[0].text))
            matched_id_str = parsed.get("matched_id")
            confidence = float(parsed.get("confidence", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
            tokens = response.usage.input_tokens + response.usage.output_tokens

            accepted = bool(matched_id_str and matched_id_str != "null" and confidence >= _CONFIDENCE_ACCEPT)
            resolved_id: UUID | None = UUID(matched_id_str) if accepted else None

            alias_created = False
            if accepted and resolved_id:
                normalized = normalize_team_name(raw_name)
                alias = TeamAlias(
                    team_id=resolved_id,
                    alias=raw_name,
                    alias_normalized=normalized,
                    source_id=source_id,
                )
                try:
                    team_repo = TeamRepository(self._session)
                    await team_repo.add_alias(alias)
                    alias_created = True
                except Exception:
                    logger.warning("llm_team_alias_create_failed", raw_name=raw_name)

            await self._llm_repo.create(
                LLMResolution(
                    source_record_id=source_record_id,
                    resolution_type="team_name",
                    input_text=raw_name,
                    candidates={"candidates": candidates},
                    llm_output=parsed,
                    resolved_id=resolved_id,
                    confidence=confidence,
                    accepted=accepted,
                    acceptance_reason=reasoning if accepted else None,
                    alias_created=alias_created,
                    latency_ms=latency_ms,
                    model=self._model,
                    tokens_used=tokens,
                )
            )
            self._record_outcome(accepted, latency_ms)

            if accepted:
                logger.info(
                    "llm_team_resolved",
                    raw_name=raw_name,
                    resolved_id=str(resolved_id),
                    confidence=confidence,
                    alias_created=alias_created,
                )
            return resolved_id

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("llm_team_resolution_error", raw_name=raw_name, error=str(exc))
            self._record_outcome(False, latency_ms)
            return None

    async def resolve_competition(
        self,
        raw_competition: str,
        sport: Sport,
        source_record_id: UUID,
        candidates: list[dict],
    ) -> UUID | None:
        """Try to match a raw competition string to a candidate competition."""
        if not candidates:
            return None

        start = time.monotonic()
        candidate_lines = "\n".join(
            f"  {i}. name={c['name']!r}, short_name={c.get('short_name', '')!r}, id={c['id']}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            f"You are a sports data entity resolver. Match the raw competition name to the best candidate.\n\n"
            f"Sport: {sport.value}\n"
            f"Raw competition: {raw_competition!r}\n\n"
            f"Candidates:\n{candidate_lines}\n\n"
            'Respond ONLY with JSON: {"matched_id": "<uuid or null>", "confidence": 0.0, "reasoning": "..."}'
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            parsed = json.loads(_extract_json(response.content[0].text))
            matched_id_str = parsed.get("matched_id")
            confidence = float(parsed.get("confidence", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
            tokens = response.usage.input_tokens + response.usage.output_tokens

            accepted = bool(matched_id_str and matched_id_str != "null" and confidence >= _CONFIDENCE_ACCEPT)
            resolved_id = UUID(matched_id_str) if accepted else None

            await self._llm_repo.create(
                LLMResolution(
                    source_record_id=source_record_id,
                    resolution_type="competition",
                    input_text=raw_competition,
                    candidates={"candidates": candidates},
                    llm_output=parsed,
                    resolved_id=resolved_id,
                    confidence=confidence,
                    accepted=accepted,
                    acceptance_reason=reasoning if accepted else None,
                    alias_created=False,
                    latency_ms=latency_ms,
                    model=self._model,
                    tokens_used=tokens,
                )
            )
            self._record_outcome(accepted, latency_ms)

            if accepted:
                logger.info(
                    "llm_competition_resolved",
                    raw_competition=raw_competition,
                    resolved_id=str(resolved_id),
                    confidence=confidence,
                )
            return resolved_id

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("llm_competition_resolution_error", raw_competition=raw_competition, error=str(exc))
            self._record_outcome(False, latency_ms)
            return None

    async def resolve_event_match(
        self,
        record: SourceRecord,
        candidates: list[Event],
    ) -> tuple[Event | None, float]:
        """Use Haiku to confirm or promote a tentative event match."""
        if not candidates:
            return None, 0.0

        start = time.monotonic()
        record_time = record.scheduled_at.isoformat() if record.scheduled_at else "unknown"
        candidate_lines = "\n".join(
            f"  {i}. home_team_id={c.home_team_id}, away_team_id={c.away_team_id}, "
            f"scheduled_at={c.scheduled_at.isoformat()}, venue={c.venue or 'unknown'!r}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            "You are a sports data entity resolver. Determine if the source record matches one of the candidate events.\n\n"
            f"Source record:\n"
            f"  home_team_raw: {record.raw_home_team!r}\n"
            f"  away_team_raw: {record.raw_away_team!r}\n"
            f"  scheduled_at (UTC): {record_time}\n"
            f"  venue: {record.venue or 'unknown'!r}\n\n"
            f"Candidates:\n{candidate_lines}\n\n"
            'Respond ONLY with JSON: {"matched_index": <integer or null>, "confidence": 0.0, "reasoning": "..."}'
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            parsed = json.loads(_extract_json(response.content[0].text))
            matched_index = parsed.get("matched_index")
            confidence = float(parsed.get("confidence", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
            tokens = response.usage.input_tokens + response.usage.output_tokens

            accepted = (
                matched_index is not None
                and isinstance(matched_index, int)
                and 0 <= matched_index < len(candidates)
                and confidence >= _CONFIDENCE_ACCEPT
            )
            resolved_id = candidates[matched_index].id if accepted and matched_index is not None else None

            await self._llm_repo.create(
                LLMResolution(
                    source_record_id=record.id,
                    resolution_type="event_match",
                    input_text=f"{record.raw_home_team} vs {record.raw_away_team} @ {record_time}",
                    candidates={
                        "candidates": [
                            {"index": i, "event_id": str(c.id)} for i, c in enumerate(candidates)
                        ]
                    },
                    llm_output=parsed,
                    resolved_id=resolved_id,
                    confidence=confidence,
                    accepted=accepted,
                    acceptance_reason=reasoning if accepted else None,
                    alias_created=False,
                    latency_ms=latency_ms,
                    model=self._model,
                    tokens_used=tokens,
                )
            )
            self._record_outcome(accepted, latency_ms)

            if accepted and matched_index is not None:
                logger.info(
                    "llm_event_match_confirmed",
                    record_id=str(record.id),
                    event_id=str(resolved_id),
                    confidence=confidence,
                )
                return candidates[matched_index], confidence

            return None, 0.0

        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("llm_event_match_error", record_id=str(record.id), error=str(exc))
            self._record_outcome(False, latency_ms)
            return None, 0.0
