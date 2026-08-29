from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import LineItem, Receipt
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


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

    detail_rows = db.execute(
        select(
            LineItem.category,
            LineItem.description,
            LineItem.amount,
            LineItem.quantity,
            Receipt.id,
            Receipt.store_name,
            Receipt.purchase_date,
        )
        .join(Receipt, LineItem.receipt_id == Receipt.id)
        .where(Receipt.purchase_date >= start, Receipt.purchase_date < end)
        .order_by(Receipt.purchase_date.desc())
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

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "month": month_str,
            "rows": rows,
            "total": total,
            "items_by_category": items_by_category,
        },
    )
