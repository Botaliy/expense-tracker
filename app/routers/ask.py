from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app import ask
from app.auth import get_current_user
from app.templating import templates

router = APIRouter(dependencies=[Depends(get_current_user)])

EXAMPLES = [
    "Сколько я потратил на кафе и рестораны этим летом?",
    "На что ушло больше всего денег в прошлом месяце?",
    "Сколько в среднем стоит мой поход в супермаркет?",
    "Как менялись мои траты на кошку за последние полгода?",
    "В каком магазине я чаще всего покупаю кофе и почём?",
]


@router.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request):
    return templates.TemplateResponse(request, "ask.html", {"examples": EXAMPLES})


# Plain ``def``: the model call blocks, FastAPI runs it in a threadpool.
@router.post("/ask", response_class=HTMLResponse)
def ask_question(request: Request, question: str = Form(...)):
    answer, error = None, None
    if question.strip():
        try:
            answer = ask.ask(question)
        except ask.AskError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request,
        "ask.html",
        {"examples": EXAMPLES, "question": question, "answer": answer, "error": error},
    )
