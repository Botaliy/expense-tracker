from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.search import (
    SEARCH_MONTHS,
    find_matches,
    known_names,
    search_period,
    suggest,
    summarize,
)
from app.stats import nice_ticks
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])

OFTEN_BOUGHT = 12


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query(default=""),
    db: Session = Depends(get_db),
):
    query = q.strip()
    start, end, months = search_period(date.today())
    context = {"query": query, "months_count": SEARCH_MONTHS}

    if not query:
        items = [n for n in known_names(db, start, end) if n["kind"] == "product"]
        context["often"] = items[:OFTEN_BOUGHT]
        return templates.TemplateResponse(request, "search.html", context)

    result = summarize(find_matches(db, query, start, end), months)
    ticks = nice_ticks(max(m["total"] for m in result["chart"]))
    context.update(result=result, ticks=ticks, axis_max=ticks[-1] or 1)
    return templates.TemplateResponse(request, "search.html", context)


@router.get("/search/suggest", response_class=HTMLResponse)
def search_suggestions(
    request: Request,
    q: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Names from the user's own receipts, for the dropdown under the search box."""
    start, end, _ = search_period(date.today())
    return templates.TemplateResponse(
        request,
        "_search_suggestions.html",
        {"suggestions": suggest(known_names(db, start, end), q)},
    )
