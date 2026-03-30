"""Event matching algorithm — find existing canonical events for incoming source records."""

from datetime import datetime

import structlog

from sportshub.models import Event, SourceRecord

logger = structlog.get_logger()

# Source priority for conflict resolution (higher = more authoritative)
SOURCE_PRIORITY: dict[str, int] = {
    "nbacom_cdn": 10,
    "espn_nba": 8,
    "balldontlie_nba": 5,
    "lolesports": 10,
    "pandascore_lol": 8,
    "liquipedia_lol": 5,
    "fifa_api": 10,
    "footballdata_wc": 8,
    "espn_fifa": 7,
    # Mollybet — multi-bookie verified; high priority for kickoff accuracy
    "mollybet_fb": 9,
    "mollybet_basket": 9,
    "mollybet_esports": 8,
}

# Confidence thresholds
THRESHOLD_AUTO_MATCH = 0.70
THRESHOLD_TENTATIVE = 0.50


def calculate_confidence(
    record: SourceRecord,
    event: Event,
) -> float:
    """Calculate confidence score for matching a source record to a canonical event.

    Scoring:
    - Both teams match (set equality):     +0.50  (required)
    - Home/away assignment matches:         +0.10
    - Time proximity:
        - Exact (0 diff):                  +0.40
        - Within 5 minutes:                +0.35
        - Within 15 minutes:               +0.25
        - Within 1 hour:                   +0.10
        - Within 24 hours:                 +0.05
        - Beyond 24 hours:                 skip (do not match)
    """
    # This function assumes teams have already been resolved and matched
    # The caller should verify team_set equality before calling this
    score = 0.50  # Base: teams match

    # Home/away assignment bonus
    # Note: for this we'd need resolved team IDs, but we track at source_record level
    # The pipeline passes resolved IDs, so this is checked there
    score += 0.10  # Assume correct for now (refined in pipeline)

    # Time proximity
    time_diff = abs((record.scheduled_at - event.scheduled_at).total_seconds())

    if time_diff == 0:
        score += 0.40
    elif time_diff <= 300:  # 5 minutes
        score += 0.35
    elif time_diff <= 900:  # 15 minutes
        score += 0.25
    elif time_diff <= 3600:  # 1 hour
        score += 0.10
    elif time_diff <= 86400:  # 24 hours
        score += 0.05
    else:
        # Beyond 24 hours — do not match
        return 0.0

    return min(score, 1.0)


def find_matching_event(
    record_home_team_id,
    record_away_team_id,
    record: SourceRecord,
    existing_events: list[Event],
    priority_map: dict[str, float] | None = None,
) -> tuple[Event | None, float]:
    """Find the best matching canonical event for a source record.

    Args:
        record_home_team_id: Resolved UUID for the home team
        record_away_team_id: Resolved UUID for the away team
        record: The source record to match
        existing_events: Candidate canonical events

    Returns:
        (matched_event, confidence_score) or (None, 0.0)
    """
    best_match: Event | None = None
    best_score: float = 0.0

    record_team_set = {record_home_team_id, record_away_team_id}

    for event in existing_events:
        event_team_set = {event.home_team_id, event.away_team_id}

        # Team set must match (required)
        if record_team_set != event_team_set:
            continue

        score = 0.50  # Teams match

        # Home/away assignment bonus
        if (
            record_home_team_id == event.home_team_id
            and record_away_team_id == event.away_team_id
        ):
            score += 0.10

        # Time proximity
        time_diff = abs((record.scheduled_at - event.scheduled_at).total_seconds())

        if time_diff == 0:
            score += 0.40
        elif time_diff <= 300:
            score += 0.35
        elif time_diff <= 900:
            score += 0.25
        elif time_diff <= 3600:
            score += 0.10
        elif time_diff <= 86400:
            score += 0.05
        else:
            continue  # Too far apart

        score = min(score, 1.0)

        if score > best_score:
            best_score = score
            best_match = event

    return best_match, best_score
