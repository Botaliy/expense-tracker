from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.config import BASE_DIR, get_settings
from app import database
from app.database import init_db
from app.receipts import fail_interrupted_receipts
from app.routers import (
    auth,
    bank,
    budgets,
    dashboard,
    forecast,
    more,
    prices,
    receipts,
    search,
    usage,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with database.SessionLocal() as db:
        fail_interrupted_receipts(db)
    yield


app = FastAPI(title="Expense Tracker", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

app.mount("/uploads", StaticFiles(directory=str(settings.upload_path)), name="uploads")
STATIC_DIR = BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# At the root, not under /static: a service worker only controls pages below its own path.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

app.include_router(auth.router)
app.include_router(receipts.router)
app.include_router(dashboard.router)
app.include_router(search.router)
app.include_router(usage.router)
app.include_router(forecast.router)
app.include_router(more.router)
app.include_router(bank.router)
app.include_router(budgets.router)
app.include_router(prices.router)
