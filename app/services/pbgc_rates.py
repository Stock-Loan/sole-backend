from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.models.pbgc_mid_term_rate import PbgcMidTermRate


MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _parse_percent(value: str) -> Decimal | None:
    text = value.replace("\xa0", " ").strip()
    if not text:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)%", text)
    if not match:
        return None
    return Decimal(match.group(1))


def _find_current_year_table(html: str, year: int) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    year_str = str(year)
    header = None
    for h2 in soup.find_all("h2"):
        text = h2.get_text(strip=True)
        if year_str in text:
            header = h2
            break
    if not header:
        raise ValueError(f"Unable to locate PBGC table for year {year}")
    table = header.find_next("table")
    if not table:
        raise ValueError(f"No table found after PBGC year header {year}")
    return table


def _extract_current_year_rates(html: str, year: int) -> list[dict[str, object]]:
    table = _find_current_year_table(html, year)
    tbody = table.find("tbody") or table
    rows = []
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        month_text = cells[0].get_text(" ", strip=True).lower()
        month = MONTH_MAP.get(month_text)
        if not month:
            continue
        year_text = cells[1].get_text(" ", strip=True)
        if year_text.isdigit() and int(year_text) != year:
            continue
        annual_rate = _parse_percent(cells[2].get_text(" ", strip=True))
        monthly_rate = _parse_percent(cells[3].get_text(" ", strip=True))
        rows.append(
            {
                "year": year,
                "month": month,
                "annual_rate_percent": annual_rate,
                "monthly_rate_percent": monthly_rate,
            }
        )
    if not rows:
        raise ValueError(f"No PBGC rate rows parsed for year {year}")
    return rows


def _is_year_complete(rows: list[dict[str, object]]) -> bool:
    if len(rows) < 12:
        return False
    for row in rows:
        if row.get("annual_rate_percent") is None:
            return False
    return True


def should_run_scrape(today: date | None = None) -> bool:
    current = today or datetime.now(timezone.utc).date()
    day = current.day
    month = current.month
    if month == 2:
        last_day = calendar.monthrange(current.year, month)[1]
        return day == last_day
    return day == settings.pbgc_rate_scrape_day


async def fetch_current_year_rates() -> list[dict[str, object]]:
    year = datetime.now(timezone.utc).year
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(settings.pbgc_mid_term_rates_url)
        response.raise_for_status()
    return _extract_current_year_rates(response.text, year)


async def upsert_current_year_rates(db: AsyncSession) -> tuple[int, datetime]:
    year = datetime.now(timezone.utc).year
    rows = await fetch_current_year_rates()
    fetched_at = datetime.now(timezone.utc)
    payload = [
        {
            **row,
            "source_url": settings.pbgc_mid_term_rates_url,
            "fetched_at": fetched_at,
        }
        for row in rows
    ]
    insert_stmt = insert(PbgcMidTermRate).values(payload)
    update_stmt = {
        "annual_rate_percent": case(
            (
                insert_stmt.excluded.annual_rate_percent.is_(None),
                PbgcMidTermRate.annual_rate_percent,
            ),
            else_=insert_stmt.excluded.annual_rate_percent,
        ),
        "monthly_rate_percent": case(
            (
                insert_stmt.excluded.monthly_rate_percent.is_(None),
                PbgcMidTermRate.monthly_rate_percent,
            ),
            else_=insert_stmt.excluded.monthly_rate_percent,
        ),
        "source_url": insert_stmt.excluded.source_url,
        "fetched_at": insert_stmt.excluded.fetched_at,
        "updated_at": func.now(),
    }
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[PbgcMidTermRate.year, PbgcMidTermRate.month],
        set_=update_stmt,
    )
    result = await db.execute(stmt)

    if _is_year_complete(rows):
        next_year = year + 1
        next_payload = [
            {
                "year": next_year,
                "month": month,
                "annual_rate_percent": None,
                "monthly_rate_percent": None,
                "source_url": settings.pbgc_mid_term_rates_url,
                "fetched_at": fetched_at,
            }
            for month in range(1, 13)
        ]
        next_stmt = (
            insert(PbgcMidTermRate)
            .values(next_payload)
            .on_conflict_do_nothing(index_elements=[PbgcMidTermRate.year, PbgcMidTermRate.month])
        )
        await db.execute(next_stmt)

    await db.flush()
    return result.rowcount or 0, fetched_at


async def get_latest_annual_rate(db: AsyncSession) -> Decimal | None:
    stmt = (
        select(PbgcMidTermRate.annual_rate_percent)
        .where(PbgcMidTermRate.annual_rate_percent.is_not(None))
        .order_by(PbgcMidTermRate.year.desc(), PbgcMidTermRate.month.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_rates(db: AsyncSession, year: int | None = None) -> list[PbgcMidTermRate]:
    stmt = select(PbgcMidTermRate)
    if year is not None:
        stmt = stmt.where(PbgcMidTermRate.year == year)
    stmt = stmt.order_by(PbgcMidTermRate.year.desc(), PbgcMidTermRate.month.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def has_any_rates(db: AsyncSession) -> bool:
    stmt = select(PbgcMidTermRate.id).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None
