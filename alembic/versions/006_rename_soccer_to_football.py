"""Rename sport value 'soccer' to 'football' across all tables.

Revision ID: 006
Revises: 005
Create Date: 2026-03-28
"""
from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Actual constraint names from pg_constraint (ck_ prefix from SQLAlchemy naming)
CONSTRAINTS = [
    ("teams", "ck_teams_teams_valid_sport"),
    ("players", "ck_players_players_valid_sport"),
    ("competitions", "ck_competitions_competitions_valid_sport"),
    ("events", "ck_events_events_valid_sport"),
    ("source_records", "ck_source_records_source_records_valid_sport"),
    ("source_accuracy_records", "ck_source_accuracy_records_source_accuracy_valid_sport"),
]

SPORT_CHECK_NEW = "sport IN ('nba', 'lol', 'football')"
SPORT_CHECK_OLD = "sport IN ('nba', 'lol', 'soccer')"

# Tables that contain sport data to update
DATA_TABLES = ["teams", "players", "competitions", "events", "source_records", "source_accuracy_records"]


def upgrade() -> None:
    # 1. Drop ALL sport CHECK constraints first (before updating data)
    for table, constraint_name in CONSTRAINTS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint_name}")

    # 2. Update data: rename 'soccer' -> 'football' in all tables
    for table in DATA_TABLES:
        op.execute(f"UPDATE {table} SET sport = 'football' WHERE sport = 'soccer'")

    # 3. Re-add CHECK constraints with 'football'
    for table, constraint_name in CONSTRAINTS:
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
            f"CHECK ({SPORT_CHECK_NEW})"
        )


def downgrade() -> None:
    # 1. Drop constraints
    for table, constraint_name in CONSTRAINTS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint_name}")

    # 2. Update data back: 'football' -> 'soccer'
    for table in DATA_TABLES:
        op.execute(f"UPDATE {table} SET sport = 'soccer' WHERE sport = 'football'")

    # 3. Re-add constraints with 'soccer'
    for table, constraint_name in CONSTRAINTS:
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint_name} "
            f"CHECK ({SPORT_CHECK_OLD})"
        )
