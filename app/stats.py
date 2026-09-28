"""Month-level queries shared by the home feed and the dashboard."""

import calendar
import math
from datetime import date, timedelta

from sqlalchemy import Date, func, select
from sqlalchemy.orm import Session

from app.models import LineItem, Receipt

MONTHS_NOMINATIVE = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_DATIVE = [
    "январю", "февралю", "марту", "апрелю", "маю", "июню",
    "июлю", "августу", "сентябрю", "октябрю", "ноябрю", "декабрю",
]

# Receipts where the date couldn't be read (or was left blank) still count,
# attributed to the day they were uploaded.
effective_date = func.coalesce(
    Receipt.purchase_date, func.date(Receipt.uploaded_at), type_=Date
)


def parse_month(value: str | None) -> tuple[int, int]:
    """``YYYY-MM`` → (year, month); anything invalid falls back to this month."""
    today = date.today()
    if value:
        try:
            year, mon = (int(part) for part in value.split("-"))
            date(year, mon, 1)
            return year, mon
        except ValueError:
            pass
    return today.year, today.month


def shift_month(year: int, mon: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (mon - 1) + delta
    return index // 12, index % 12 + 1


def month_nav(year: int, mon: int) -> dict:
    """``YYYY-MM`` keys for the ‹ › arrows; no "next" past the current month."""
    today = date.today()
    prev_year, prev_mon = shift_month(year, mon, -1)
    next_year, next_mon = shift_month(year, mon, 1)
    return {
        "prev": f"{prev_year:04d}-{prev_mon:02d}",
        "next": (
            f"{next_year:04d}-{next_mon:02d}"
            if date(next_year, next_mon, 1) <= today
            else None
        ),
    }


def month_bounds(year: int, mon: int) -> tuple[date, date]:
    """Half-open range [first day, first day of next month)."""
    next_year, next_mon = shift_month(year, mon, 1)
    return date(year, mon, 1), date(next_year, next_mon, 1)


def category_totals(db: Session, start: date, end: date) -> list[tuple[str, float]]:
    return [
        (category, amount)
        for category, amount in db.execute(
            select(LineItem.category, func.sum(LineItem.amount))
            .join(Receipt, LineItem.receipt_id == Receipt.id)
            .where(effective_date >= start, effective_date < end)
            .group_by(LineItem.category)
            .order_by(func.sum(LineItem.amount).desc())
        ).all()
    ]


def month_summary(db: Session, year: int, mon: int) -> dict:
    """Total, per-category split and comparison with the previous month.

    For the current month the comparison is "to date": September 1–28 against
    August 1–28, otherwise a half-finished month always looks cheaper.
    """
    today = date.today()
    start, end = month_bounds(year, mon)
    by_category = category_totals(db, start, end)
    total = sum(amount for _, amount in by_category)

    is_current = (year, mon) == (today.year, today.month)
    prev_year, prev_mon = shift_month(year, mon, -1)
    prev_start, prev_end = month_bounds(prev_year, prev_mon)
    if is_current:
        cutoff = min(today.day, calendar.monthrange(prev_year, prev_mon)[1])
        prev_end = date(prev_year, prev_mon, cutoff) + timedelta(days=1)
    prev_by_category = dict(category_totals(db, prev_start, prev_end))
    prev_total = sum(prev_by_category.values())

    days_counted = today.day if is_current else calendar.monthrange(year, mon)[1]
    delta_pct = round((total / prev_total - 1) * 100) if prev_total else None

    return {
        "total": total,
        "by_category": by_category,
        "prev_by_category": prev_by_category,
        "per_day": total / days_counted,
        "is_current": is_current,
        "today": today,
        "delta_pct": delta_pct,
        "month_genitive": MONTHS_GENITIVE[mon - 1],
        "prev_month_dative": MONTHS_DATIVE[prev_mon - 1],
        "title": f"{MONTHS_NOMINATIVE[mon - 1]} {year}",
    }


def monthly_totals(db: Session, year: int, mon: int, count: int = 6) -> list[dict]:
    """Totals for ``count`` months ending with (year, mon), oldest first."""
    first_year, first_mon = shift_month(year, mon, -(count - 1))
    start, _ = month_bounds(first_year, first_mon)
    _, end = month_bounds(year, mon)
    month_key = func.strftime("%Y-%m", effective_date)
    rows = db.execute(
        select(month_key, LineItem.category, func.sum(LineItem.amount))
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(effective_date >= start, effective_date < end)
        .group_by(month_key, LineItem.category)
    ).all()
    by_month: dict[str, dict[str, float]] = {}
    for key, category, amount in rows:
        by_month.setdefault(key, {})[category] = amount

    months = []
    for i in range(count):
        y, m = shift_month(first_year, first_mon, i)
        key = f"{y:04d}-{m:02d}"
        cats = by_month.get(key, {})
        top = max(cats.items(), key=lambda c: c[1], default=None)
        months.append({
            "key": key,
            "short": MONTHS_SHORT[m - 1],
            "name": f"{MONTHS_NOMINATIVE[m - 1]} {y}",
            "total": sum(cats.values()),
            "top_category": top[0] if top else None,
            "top_amount": top[1] if top else 0,
        })
    return months


def nice_ticks(max_value: float, count: int = 4) -> list[float]:
    """Round axis ticks from 0 that cover ``max_value``: 0, 5 000, 10 000, 15 000."""
    if max_value <= 0:
        return [0]
    raw_step = max_value / count
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    ticks = [0.0]
    while ticks[-1] < max_value:
        ticks.append(ticks[-1] + step)
    return ticks


# Before this, a single purchase swings the pace too much to be worth showing.
FORECAST_FROM_DAY = 5


def month_forecast(db: Session, year: int, mon: int, total: float) -> dict | None:
    """End-of-month projection at the current pace, for the current month only.

    Compared with the average of the previous three months that have any
    spending, rather than just last month, which may have been unusual.
    """
    today = date.today()
    if (year, mon) != (today.year, today.month) or today.day < FORECAST_FROM_DAY or total <= 0:
        return None
    days_in_month = calendar.monthrange(year, mon)[1]
    prev_year, prev_mon = shift_month(year, mon, -1)
    history = [m["total"] for m in monthly_totals(db, prev_year, prev_mon, 3) if m["total"] > 0]
    return {
        "projected": total / today.day * days_in_month,
        "average": sum(history) / len(history) if history else None,
        "months_averaged": len(history),
        "last_day": days_in_month,
    }


def top_items(db: Session, start: date, end: date, limit: int = 5) -> list[dict]:
    """The month's most expensive line items."""
    rows = db.execute(
        select(
            LineItem.description,
            LineItem.amount,
            LineItem.category,
            Receipt.id,
            Receipt.store_name,
            Receipt.currency,
            effective_date,
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(effective_date >= start, effective_date < end, LineItem.amount > 0)
        .order_by(LineItem.amount.desc())
        .limit(limit)
    ).all()
    return [
        {
            "description": description,
            "amount": amount,
            "category": category,
            "receipt_id": receipt_id,
            "store_name": store_name,
            "currency": currency,
            "day": day,
        }
        for description, amount, category, receipt_id, store_name, currency, day in rows
    ]


def top_places(db: Session, start: date, end: date, limit: int = 5) -> list[dict]:
    """Stores by money spent. "LIDL" and "Lidl " count as one place."""
    place_key = func.lower(func.trim(Receipt.store_name))
    receipt_totals = (
        select(Receipt.id.label("receipt_id"), func.sum(LineItem.amount).label("amount"))
        .join(LineItem, LineItem.receipt_id == Receipt.id)
        .where(effective_date >= start, effective_date < end, Receipt.store_name.is_not(None))
        .group_by(Receipt.id)
        .subquery()
    )
    rows = db.execute(
        select(
            func.min(func.trim(Receipt.store_name)),
            func.count(),
            func.sum(receipt_totals.c.amount),
        )
        .join(receipt_totals, receipt_totals.c.receipt_id == Receipt.id)
        .group_by(place_key)
        .order_by(func.sum(receipt_totals.c.amount).desc())
        .limit(limit)
    ).all()
    return [{"name": name, "visits": visits, "amount": amount} for name, visits, amount in rows]
