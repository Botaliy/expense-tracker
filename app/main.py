from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.config import get_settings
from app import database
from app.database import init_db
from app.receipts import fail_interrupted_receipts
from app.routers import auth, dashboard, receipts, search

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

app.include_router(auth.router)
app.include_router(receipts.router)
app.include_router(dashboard.router)
app.include_router(search.router)
