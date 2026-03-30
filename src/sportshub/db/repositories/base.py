"""Base repository with shared helpers for all repositories."""

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Base class providing common database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _execute(self, stmt: sa.Executable) -> sa.CursorResult[Any]:
        return await self._session.execute(stmt)

    async def _fetch_one(self, stmt: sa.Select[Any]) -> sa.Row[Any] | None:
        result = await self._session.execute(stmt)
        return result.first()

    async def _fetch_all(self, stmt: sa.Select[Any]) -> list[sa.Row[Any]]:
        result = await self._session.execute(stmt)
        return list(result.all())

    async def _count(self, stmt: sa.Select[Any]) -> int:
        count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())
        result = await self._session.execute(count_stmt)
        return result.scalar_one()

    @staticmethod
    def _row_to_dict(row: sa.Row[Any]) -> dict[str, Any]:
        """Convert a SQLAlchemy Row to a dict."""
        return dict(row._mapping)
