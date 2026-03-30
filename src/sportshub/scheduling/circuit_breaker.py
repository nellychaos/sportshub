"""Circuit breaker for source adapter fault tolerance."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

logger = structlog.get_logger()

FAILURE_THRESHOLD = 5
INITIAL_PAUSE = timedelta(hours=1)
EXTENDED_PAUSE = timedelta(hours=4)


@dataclass
class SourceCircuitState:
    """Per-source circuit breaker state."""

    consecutive_failures: int = 0
    open_until: datetime | None = None
    pause_duration: timedelta = field(default_factory=lambda: INITIAL_PAUSE)
    last_failure_time: datetime | None = None


class CircuitBreaker:
    """Circuit breaker: pauses adapters after repeated failures."""

    def __init__(self) -> None:
        self._states: dict[str, SourceCircuitState] = {}

    def _get_state(self, source_id: str) -> SourceCircuitState:
        if source_id not in self._states:
            self._states[source_id] = SourceCircuitState()
        return self._states[source_id]

    def record_success(self, source_id: str) -> None:
        """Reset circuit breaker on success."""
        state = self._get_state(source_id)
        state.consecutive_failures = 0
        state.open_until = None
        state.pause_duration = INITIAL_PAUSE

    def record_failure(self, source_id: str) -> None:
        """Record a failure. Opens circuit after FAILURE_THRESHOLD consecutive failures."""
        state = self._get_state(source_id)
        state.consecutive_failures += 1
        state.last_failure_time = datetime.utcnow()

        if state.consecutive_failures >= FAILURE_THRESHOLD:
            state.open_until = datetime.utcnow() + state.pause_duration
            logger.warning(
                "circuit_breaker_opened",
                source_id=source_id,
                failures=state.consecutive_failures,
                pause_until=state.open_until.isoformat(),
            )
            # Escalate pause for next time
            state.pause_duration = EXTENDED_PAUSE

    def is_open(self, source_id: str) -> bool:
        """Check if circuit is open (adapter should be skipped)."""
        state = self._get_state(source_id)
        if state.open_until is None:
            return False
        if datetime.utcnow() >= state.open_until:
            # Pause expired — allow one probe attempt
            state.open_until = None
            logger.info("circuit_breaker_probe", source_id=source_id)
            return False
        return True

    def get_status(self, source_id: str) -> dict:
        """Get circuit breaker status for health endpoint."""
        state = self._get_state(source_id)
        return {
            "source_id": source_id,
            "consecutive_failures": state.consecutive_failures,
            "is_open": self.is_open(source_id),
            "open_until": state.open_until.isoformat() if state.open_until else None,
        }

    def get_all_statuses(self) -> list[dict]:
        return [self.get_status(sid) for sid in self._states]
