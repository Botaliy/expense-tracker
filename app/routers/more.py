from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth import get_current_user
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/more", response_class=HTMLResponse)
def more_page(request: Request):
    return templates.TemplateResponse(request, "more.html", {})

