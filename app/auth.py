import bcrypt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings

SESSION_KEY = "user"


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_credentials(username: str, password: str) -> bool:
    settings = get_settings()
    if username != settings.auth_username:
        return False
    if not settings.auth_password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"), settings.auth_password_hash.encode("utf-8")
        )
    except ValueError:
        return False


def login(request: Request, username: str) -> None:
    request.session[SESSION_KEY] = username


def logout(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_KEY)


def get_current_user(request: Request) -> str:
    """FastAPI dependency: redirects to /login (via 303 + Location header) if not logged in."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
