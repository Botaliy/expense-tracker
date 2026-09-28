from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import LineItem, Receipt
from app.stats import (
    effective_date,
    month_bounds,
    month_summary,
    month_nav,
    monthly_totals,
    nice_ticks,
    parse_month,
)
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    month: str | None = Query(default=None, description="YYYY-MM"),
    db: Session = Depends(get_db),
):
    year, mon = parse_month(month)
    start, end = month_bounds(year, mon)
    summary = month_summary(db, year, mon)

    detail_rows = db.execute(
        select(
            LineItem.category,
            LineItem.description,
            LineItem.amount,
            LineItem.quantity,
            Receipt.id,
            Receipt.store_name,
            effective_date,
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(effective_date >= start, effective_date < end)
        .order_by(effective_date.desc())
    ).all()

    items_by_category: dict[str, list[dict]] = {}
    for category, description, amount, quantity, receipt_id, store_name, purchase_date in detail_rows:
        items_by_category.setdefault(category, []).append(
            {
                "description": description,
                "amount": amount,
                "quantity": quantity,
                "receipt_id": receipt_id,
                "store_name": store_name,
                "purchase_date": purchase_date,
            }
        )

    months = monthly_totals(db, year, mon)
    ticks = nice_ticks(max(m["total"] for m in months))
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "month": f"{year:04d}-{mon:02d}",
            "summary": summary,
            "months": months,
            "ticks": ticks,
            "axis_max": ticks[-1] or 1,
            "items_by_category": items_by_category,
            "nav": month_nav(year, mon),
        },
    )
