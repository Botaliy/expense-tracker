"""Monthly limits per category, and how the month is tracking against them.

"On pace" compares spending with the share of the month gone by: 40% of the
limit spent on the 10th of a 30-day month is ahead of pace (10/30 = 33%).
The projection is the same straight line as the dashboard's month forecast.
"""

import calendar
import math
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categories import CATEGORIES
from app.models import Budget
from app.stats import FORECAST_FROM_DAY, category_totals, month_bounds, shift_month

# Months averaged for the suggested limit.
HISTORY_MONTHS = 3


@dataclass
class BudgetLine:
    category: str
    limit: float | None
    spent: float
    projected: float | None
    average: float  # over the previous HISTORY_MONTHS months

    @property
    def share(self) -> float:
        return self.spent / self.limit if self.limit else 0.0

    @property
    def status(self) -> str:
        """``over`` (limit passed), ``risk`` (will pass at this pace), ``ok``, or ``none``."""
        if not self.limit:
            return "none"
        if self.spent > self.limit:
            return "over"
        if self.projected is not None and self.projected > self.limit:
            return "risk"
        return "ok"

    @property
    def left(self) -> float:
        return (self.limit or 0) - self.spent

    @property
    def suggested(self) -> float:
        """The recent average rounded up to a tidy number: a starting point, not advice."""
        if self.average <= 0:
            return 0
        step = 10 if self.average < 200 else 50
        return math.ceil(self.average / step) * step


def limits(db: Session) -> dict[str, float]:
    return dict(db.execute(select(Budget.category, Budget.monthly_limit)).all())


def set_limit(db: Session, category: str, limit: float | None) -> None:
    budget = db.scalar(select(Budget).where(Budget.category == category))
    if limit is None or limit <= 0:
        if budget is not None:
            db.delete(budget)
    elif budget is None:
        db.add(Budget(category=category, monthly_limit=limit))
    else:
        budget.monthly_limit = limit
    db.commit()


def report(db: Session, today: date | None = None) -> dict:
    """This month by category, budgeted ones first (worst first)."""
    today = today or date.today()
    start, end = month_bounds(today.year, today.month)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    spent = dict(category_totals(db, start, end))

    first_year, first_mon = shift_month(today.year, today.month, -HISTORY_MONTHS)
    history_start, _ = month_bounds(first_year, first_mon)
    history = dict(category_totals(db, history_start, start))

    budgets = limits(db)
    lines = []
    for category in CATEGORIES:
        amount = spent.get(category, 0.0)
        projected = (
            amount / today.day * days_in_month if today.day >= FORECAST_FROM_DAY else None
        )
        lines.append(BudgetLine(
            category=category,
            limit=budgets.get(category),
            spent=amount,
            projected=projected,
            average=history.get(category, 0.0) / HISTORY_MONTHS,
        ))

    rank = {"over": 0, "risk": 1, "ok": 2}
    budgeted = sorted((b for b in lines if b.limit), key=lambda b: (rank[b.status], -b.share))
    unbudgeted = sorted((b for b in lines if not b.limit and (b.spent or b.average)), key=lambda b: -(b.spent or b.average))
    total_limit = sum(b.limit for b in budgeted)
    total_spent = sum(b.spent for b in budgeted)
    return {
        "budgeted": budgeted,
        "unbudgeted": unbudgeted,
        "month_share": today.day / days_in_month,
        "day": today.day,
        "days_in_month": days_in_month,
        "total_limit": total_limit,
        "total_spent": total_spent,
        "alerts": [b for b in budgeted if b.status in ("over", "risk")],
    }
