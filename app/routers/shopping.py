from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import shopping
from app.auth import get_current_user
from app.database import get_db
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/shopping", response_class=HTMLResponse)
def shopping_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "shopping.html", {"lines": shopping.shopping_list(db)})


@router.post("/shopping/forecast")
def tick_forecast(
    product: str = Form(...),
    due_date: str = Form(...),
    done: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    try:
        due = date.fromisoformat(due_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad date")
    shopping.tick_forecast(db, product, due, done)
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/shopping/items")
def add_shopping_item(name: str = Form(...), db: Session = Depends(get_db)):
    shopping.add_item(db, name)
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/shopping/items/{item_id}")
def toggle_shopping_item(item_id: int, done: bool = Form(default=False), db: Session = Depends(get_db)):
    shopping.toggle_item(db, item_id, done)
    return RedirectResponse(url="/shopping", status_code=303)


@router.post("/shopping/clear")
def clear_shopping(db: Session = Depends(get_db)):
    shopping.clear_done(db)
    return RedirectResponse(url="/shopping", status_code=303)
