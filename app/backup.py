"""Daily database backup, sent to a Telegram chat.

Runs as its own compose service on the app's image (so it adds nothing to the
image): ``python -m app.backup`` loops, ``python -m app.backup --once`` sends
one right away. Only the standard library is used.

The snapshot goes through SQLite's online backup API, so it's consistent even
while the app is writing. Receipt photos aren't included: they're large, and
everything the app shows lives in the database.

Setup: create a bot with @BotFather, send it any message, then take your chat
id from https://api.telegram.org/bot<TOKEN>/getUpdates.
"""

import argparse
import gzip
import json
import logging
import sqlite3
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import BASE_DIR, get_settings

logger = logging.getLogger("backup")

# Telegram bots may send files up to 50 MB.
MAX_SIZE = 50 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def snapshot(db_file: Path) -> bytes:
    """A consistent copy of the database, gzipped."""
    if not db_file.exists():
        raise BackupError(f"Database not found: {db_file}")
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "copy.db"
        source = sqlite3.connect(db_file)
        dest = sqlite3.connect(copy_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        return gzip.compress(copy_path.read_bytes(), compresslevel=9)


def _multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        "Content-Type: application/gzip\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send_to_telegram(token: str, chat_id: str, filename: str, content: bytes, caption: str) -> None:
    if len(content) > MAX_SIZE:
        raise BackupError(f"Backup is {len(content) // 1024 // 1024} MB, over Telegram's 50 MB limit")
    body, content_type = _multipart({"chat_id": chat_id, "caption": caption}, filename, content)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except Exception as exc:  # network errors, HTTP 4xx/5xx
        raise BackupError(f"Telegram request failed: {exc}") from exc
    if not result.get("ok"):
        raise BackupError(f"Telegram refused the file: {result.get('description')}")


def run_once() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise BackupError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    db_file = (BASE_DIR / settings.db_path).resolve()
    data = snapshot(db_file)
    now = datetime.now(ZoneInfo(settings.timezone))
    filename = f"expenses-{now:%Y-%m-%d}.db.gz"
    caption = f"Бэкап трат за {now:%d.%m.%Y}, {len(data) / 1024:.0f} КБ"
    send_to_telegram(settings.telegram_bot_token, settings.telegram_chat_id, filename, data, caption)
    logger.info("Sent %s (%d bytes)", filename, len(data))


def seconds_until(hour: int, now: datetime) -> float:
    """Until the next ``hour``:00 after ``now`` (same timezone)."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def loop() -> None:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    while True:
        wait = seconds_until(settings.backup_hour, datetime.now(tz))
        logger.info("Next backup in %.1f h", wait / 3600)
        time.sleep(wait)
        # A failed day is logged and retried once an hour later, not lost.
        for attempt in range(3):
            try:
                run_once()
                break
            except BackupError:
                logger.exception("Backup failed (attempt %d)", attempt + 1)
                time.sleep(3600)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Back up the expenses database to Telegram")
    parser.add_argument("--once", action="store_true", help="send one backup now and exit")
    args = parser.parse_args()
    if args.once:
        run_once()
    else:
        loop()


if __name__ == "__main__":
    main()
