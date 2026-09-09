"""Manual test send of the Telegram morning report to the configured chat.

Usage (from backend/):
    arch -x86_64 .venv/bin/python scripts/send_tg_report.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(".env"))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import get_async_session_maker  # noqa: E402
from app.models import User  # noqa: E402
from app.services.telegram_bot import TelegramBotService  # noqa: E402


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env")
        sys.exit(1)

    async with get_async_session_maker()() as db:
        result = await db.execute(select(User).order_by(User.created_at))
        user = result.scalars().first()
        if not user:
            print("No users in DB")
            sys.exit(1)

        print(f"Sending reports for user {user.email} (id={user.id})...")
        bot = TelegramBotService()
        ok_morning = await bot.send_morning_report(db, str(user.id))
        print(f"morning report: {'sent' if ok_morning else 'FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
