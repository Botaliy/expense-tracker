# Expense Tracker

Личное веб-приложение для учёта трат: загружаешь фото чека, Claude (vision) распознаёт
позиции и раскладывает их по фиксированному списку категорий, дальше можно смотреть
траты по месяцам на дашборде.

## Стек

- FastAPI + SQLAlchemy + SQLite
- Jinja2 + HTMX (без отдельного фронтенда)
- Session-based логин с одним захардкоженным пользователем
- Anthropic API (`claude-haiku-4-5`, vision + tool use) для распознавания чеков

## Установка

```bash
uv sync
cp .env.example .env
```

Заполни `.env`:
- `ANTHROPIC_API_KEY` — ключ из https://console.anthropic.com/settings/keys
- `SECRET_KEY` — любая случайная строка (`python -c "import secrets;print(secrets.token_hex(32))"`)
- `AUTH_USERNAME` — твой логин
- `AUTH_PASSWORD_HASH` — сгенерируй командой:

```bash
uv run python scripts/hash_password.py "твой-пароль"
```

## Запуск

```bash
uv run uvicorn app.main:app --reload
```

Открой http://127.0.0.1:8000, залогинься, загрузи фото чека.

## Тесты

```bash
uv run pytest
```

## Деплой на VPS (Oracle Cloud Free Tier)

```bash
docker build -t expense-tracker .
docker run -d --name expense-tracker \
  --env-file .env \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/uploads:/app/uploads \
  expense-tracker
```

Дальше — Nginx/Caddy как reverse proxy с TLS перед контейнером (по желанию).
