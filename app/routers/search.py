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
    names = known_names(db, start, end)
    context = {"query": query, "months_count": SEARCH_MONTHS, "names": names}

    if not query:
        context["often"] = [n for n in names if n["kind"] == "product"][:OFTEN_BOUGHT]
        return templates.TemplateResponse(request, "search.html", context)

    result = summarize(find_matches(db, query, start, end), months)
    ticks = nice_ticks(max(m["total"] for m in result["chart"]))
    context.update(result=result, ticks=ticks, axis_max=ticks[-1] or 1)
    return templates.TemplateResponse(request, "search.html", context)

