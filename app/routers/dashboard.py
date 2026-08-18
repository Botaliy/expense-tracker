from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import LineItem, Receipt

router = APIRouter(dependencies=[Depends(get_current_user)])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    month: str | None = Query(default=None, description="YYYY-MM"),
    db: Session = Depends(get_db),
):
    today = date.today()
    if month:
        year, mon = (int(part) for part in month.split("-"))
    else:
        year, mon = today.year, today.month
    month_str = f"{year:04d}-{mon:02d}"

    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)

    rows = db.execute(
        select(LineItem.category, func.sum(LineItem.amount))
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(Receipt.purchase_date >= start, Receipt.purchase_date < end)
        .group_by(LineItem.category)
        .order_by(func.sum(LineItem.amount).desc())
    ).all()

    total = sum(amount for _, amount in rows)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "month": month_str,
            "rows": rows,
            "total": total,
        },
    )
