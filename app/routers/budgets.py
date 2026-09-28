from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import budgets
from app.auth import get_current_user
from app.categories import CATEGORIES
from app.database import get_db
from app.routers.receipts import _parse_amount
from app.stats import MONTHS_NOMINATIVE
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/budgets", response_class=HTMLResponse)
def budgets_page(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    return templates.TemplateResponse(
        request,
        "budgets.html",
        {"r": budgets.report(db, today), "month_name": MONTHS_NOMINATIVE[today.month - 1]},
    )


@router.post("/budgets")
def save_budget(category: str = Form(...), limit: str = Form(default=""), db: Session = Depends(get_db)):
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")
    budgets.set_limit(db, category, _parse_amount(limit) if limit.strip() else None)
    return RedirectResponse(url="/budgets", status_code=303)
