"""Data access repositories."""

from sportshub.db.repositories.competition_repo import CompetitionRepository
from sportshub.db.repositories.event_repo import EventRepository
from sportshub.db.repositories.player_repo import PlayerRepository
from sportshub.db.repositories.source_repo import SourceRecordRepository
from sportshub.db.repositories.team_repo import TeamRepository

__all__ = [
    "TeamRepository",
    "PlayerRepository",
    "CompetitionRepository",
    "EventRepository",
    "SourceRecordRepository",
]
