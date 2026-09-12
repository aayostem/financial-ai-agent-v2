from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, Integer, SmallInteger, String, select
from sqlalchemy.orm import Mapped, mapped_column

from financial_ai.storage.database import Base
from financial_ai.storage.repositories.base import BaseRepository
from financial_ai.utils.exceptions import DatabaseQueryError

logger = logging.getLogger(__name__)

# ORM MODEL


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    filing_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_year: Mapped[int | None] = mapped_column(SmallInteger)
    fiscal_quarter: Mapped[int | None] = mapped_column(SmallInteger)
    filed_at: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String)
    file_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pages: Mapped[int | None] = mapped_column(Integer)
    ingested_by: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<Filing id={self.id} ticker={self.ticker} "
            f"type={self.filing_type} year={self.fiscal_year}>"
        )


# REPOSITORY


class FilingsRepository(BaseRepository[Filing]):
    model_class = Filing

    # deduplication
    async def get_by_hash(self, file_hash: str) -> Filing | None:
        try:
            result = await self._session.execute(
                select(Filing).where(Filing.file_hash == file_hash)
            )
            return result.scalar_one_or_none()
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to look up filing by hash '{file_hash}: {exc}"
            ) from exc

    async def exists_by_hash(self, file_hash: str) -> bool:
        return await self.get_by_hash(file_hash) is not None

    # filtered queries
    async def get_by_ticker(
        self, ticker: str, *, active_only: bool = True, limit: int = 50
    ) -> list[Filing]:
        try:
            stmt = (
                select(Filing)
                .where(Filing.ticker == ticker.upper())
                .order_by(Filing.fiscal_year.desc())
                .limit(limit)
            )
            if active_only:
                stmt = stmt.where(Filing.is_active.is_(True))

            result = await self._session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to fetch filings for ticker '{ticker}': {exc}"
            ) from exc

    async def get_by_ticker_and_type(
        self,
        ticker: str,
        filing_type: str,
        *,
        fiscal_year: int | None = None,
        active_only: bool = True,
    ) -> list[Filing]:
        try:
            stmt = (
                select(Filing)
                .where(Filing.ticker.upper(), Filing.filing_type == filing_type)
                .order_by(Filing.fiscal_year == fiscal_year)
            )
            if fiscal_year is not None:
                stmt = stmt.where(Filing.fiscal_year == fiscal_year)
            if active_only:
                stmt = stmt.where(Filing.is_active.is_(True))

            result = await self.session.execute(stmt)
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to fetch {filing_type} filings for '{ticker}': {exc}"
            ) from exc

    async def get_latest(self, ticker: str, filing_type: str) -> Filing | None:
        try:
            result = await self._session.execute(
                select(Filing)
                .where(
                    Filing.ticker == ticker.upper(),
                    Filing.filing_type == filing_type,
                    Filing.is_active.is_(True),
                )
                .order_by(Filing.fiscal_year.desc())
                .limit(1)
            )
            return result.scalar_one_or_more()
        except Exception as exc:
            raise DatabaseQueryError(
                f"Failed to fetch latest {filing_type} for '{ticker}': {exc}"
            ) from exc

    async def list_tickers(self) -> list[str]:
        try:
            from sqlalchemy import distinct

            result = await self._session.execute(
                select(distinct(Filing.ticker))
                .where(Filing.is_active.is_(True))
                .order_by(Filing.ticker)
            )
            return list(result.scalars().all())
        except Exception as exc:
            raise DatabaseQueryError(f"Failed to list tickers: {exc}") from exc
