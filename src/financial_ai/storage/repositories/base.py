from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_ai.utils.exceptions import DatabaseQueryError, RecordNotFoundError

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    model_class: type[Any]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # create
    async def add(self, instance: ModelT) -> ModelT:
        try:
            self._session.add(instance)
            await self._session.flush()
            await self._session.refresh(instance)
            logger.debug(
                "Added %s id=%s",
                self.model_class.__name__,
                getattr(instance, "id", "?"),
            )
            return instance
        except Exception as exc:
            raise DatabaseQueryError(f"failed to add {self.model_class}: {exc}") from exc

    async def add_many(self, instances: list[ModelT]) -> list[ModelT]:
        if not instances:
            return []
        try:
            self._session.add_all(instances)
            await self._session.flush()
            logger.debug("Bulk-added %d %s records", len(instances), self.model_class.__name__)
            return instances
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to bulk-add {self.model_class.__name__}: {exc}"
            ) from exc

    # Read
    async def get_by_id(self, record_id: UUID) -> ModelT:
        try:
            instance = await self._session.get(self.model_class, record_id)
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to fetch {self.model_class.__name__} id={record_id}: {exc}"
            ) from exc
        if instance is None:
            raise RecordNotFoundError(self.model_class.__name__, str(record_id))
        return instance

    async def get_by_id_or_none(self, record_id: UUID) -> ModelT | None:
        try:
            return await self._session.get(self.model_class, record_id)
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to fetch {self.model_class.__name__} id={record_id}: {exc}"
            ) from exc

    async def list_all(self, *, limit: int = 100, offset: int = 100) -> list[ModelT]:
        try:
            stmt = select(self.model_class).limit(limit).offset(offset)
            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseQueryError(f"Failed to list {self.model_class.__name__}: {exc}") from exc

    async def count(self) -> int:
        try:
            result = await self._session.execute(select(func.count()).select_from(self.model_class))
            return result.scalar_one()
        except Exception as exc:
            raise DatabaseQueryError(f"Failed to count {self.model_class.__name__}: {exc}") from exc

    # update
    async def update(self, instance: ModelT, **fields: Any) -> ModelT:
        try:
            for key, value in fields.items():
                if not hasattr(instance, key):
                    raise DatabaseQueryError(
                        f"{self.model_class.__name__} has no attribute '{key}'"
                    )
                setattr(instance, key, value)
            await self._session.flush()
            await self._session.refresh(instance)
            return instance
        except DatabaseQueryError:
            raise
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to update {self.model_class.__name__}: {exc}"
            ) from exc

    # delete
    async def delete(self, instance: ModelT) -> None:
        try:
            await self._session.delete(instance)
            await self._session.flush()
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to delete {self.model_class.__name__}: {exc}"
            ) from exc

    async def soft_delete(self, instance: ModelT) -> ModelT:
        return await self.update(instance, as_active=False)
