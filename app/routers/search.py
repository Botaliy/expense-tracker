from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.ai_client import SearchExpansionError, expand_search_terms
from app.auth import get_current_user
from app.database import get_db
from app.search import SEARCH_MONTHS, find_matches, search_period, summarize
from app.stats import nice_ticks
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query(default=""),
    exact: bool = Query(default=False, description="Skip translating the query"),
    db: Session = Depends(get_db),
):
    query = q.strip()
    context = {"query": query, "exact": exact, "months_count": SEARCH_MONTHS}
    if not query:
        return templates.TemplateResponse(request, "search.html", context)

    expansion_failed = False
    if exact:
        terms = [query.casefold()]
    else:
        try:
            terms = expand_search_terms(query)
        except SearchExpansionError:
            terms = [query.casefold()]
            expansion_failed = True

    start, end, months = search_period(date.today())
    result = summarize(find_matches(db, terms, start, end), months)
    ticks = nice_ticks(max(m["total"] for m in result["chart"]))
    context.update(
        terms=terms,
        expansion_failed=expansion_failed,
        result=result,
        ticks=ticks,
        axis_max=ticks[-1] or 1,
    )
    return templates.TemplateResponse(request, "search.html", context)
