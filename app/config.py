from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8"
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-5-5"
    # "Ask about spending" (app.ask) writes its own SQL over several rounds:
    # a step up from receipt reading, at ~2-6 cents a question. Costs show on /usage.
    ask_model: str = "claude-sonnet-5"
    ask_effort: str = "medium"  # low | medium | high

    auth_username: str = "admin"
    auth_password_hash: str = ""
    secret_key: str = "change-me"

    # For showing stored UTC times, e.g. "Europe/Berlin". Dates of purchases
    # aren't affected: those come from the receipt itself.
    timezone: str = "UTC"

    db_path: str = "data/expenses.db"
    upload_dir: str = "uploads"

    @property
    def db_url(self) -> str:
        db_file = (BASE_DIR / self.db_path).resolve()
        db_file.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_file}"

    @property
    def upload_path(self) -> Path:
        path = (BASE_DIR / self.upload_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
