from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import recurring
from app.auth import get_current_user
from app.database import get_db
from app.forecast import add_exclusion, remove_exclusion
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/recurring", response_class=HTMLResponse)
def recurring_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "recurring.html",
        {"items": recurring.detect(db), "min_months": recurring.MIN_MONTHS, "lookback": recurring.LOOKBACK_MONTHS},
    )


@router.post("/recurring/hide")
def hide_recurring(key: str = Form(...), next: str = Form(default="/recurring"), db: Session = Depends(get_db)):
    add_exclusion(db, recurring.RECURRING, key)
    # Only local paths: this comes from a form field.
    if not next.startswith("/") or next.startswith("//"):
        next = "/recurring"
    return RedirectResponse(url=next, status_code=303)


@router.post("/recurring/unhide")
def unhide_recurring(key: str = Form(...), db: Session = Depends(get_db)):
    remove_exclusion(db, recurring.RECURRING, key)
    return RedirectResponse(url="/recurring", status_code=303)
