from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import prices
from app.auth import get_current_user
from app.database import get_db
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/prices", response_class=HTMLResponse)
def prices_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "prices.html", {"r": prices.report(db)})
