from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.stats import MONTHS_NOMINATIVE, month_bounds, nice_ticks, shift_month
from app.templating import templates
from app.usage import PURPOSE_SHORT, usage_report

router = APIRouter(dependencies=[Depends(get_current_user)])

USAGE_MONTHS = 6


@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    start, end = month_bounds(today.year, today.month)
    months = [shift_month(today.year, today.month, -i) for i in reversed(range(USAGE_MONTHS))]
    report = usage_report(
        db, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()), months
    )
    ticks = nice_ticks(max(m["total"] for m in report["chart"]))
    return templates.TemplateResponse(
        request,
        "usage.html",
        {
            "r": report,
            "month_name": MONTHS_NOMINATIVE[today.month - 1],
            "purpose_labels": PURPOSE_SHORT,
            "months": report["chart"],
            "ticks": ticks,
            "axis_max": ticks[-1] or 1,
        },
    )
