from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.rules import all_rules, delete_rule
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/more", response_class=HTMLResponse)
def more_page(request: Request):
    return templates.TemplateResponse(request, "more.html", {})


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "rules.html", {"rules": all_rules(db)})


@router.post("/rules/{rule_id}/delete")
def remove_rule(rule_id: int, db: Session = Depends(get_db)):
    delete_rule(db, rule_id)
    return RedirectResponse(url="/rules", status_code=303)
