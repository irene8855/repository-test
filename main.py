import asyncio
import os
from datetime import datetime
from telegram import Bot
import pytz

# ─── Переменные среды ──────────────────────────────────────────────────────────
TOKEN   = os.getenv("TG_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "-1000000000000"))

if not TOKEN or CHAT_ID == 0:
    raise RuntimeError("TG_TOKEN или CHAT_ID не заданы в Secrets Fly.io")

LONDON = pytz.timezone("Europe/London")
bot = Bot(TOKEN)

# ─── Функции ───────────────────────────────────────────────────────────────────
async def send(text: str):
    await bot.send_message(chat_id=CHAT_ID, text=text)

async def startup():
    now = datetime.now(LONDON).strftime("%H:%M:%S")
    await send(f"✅ Crypto-bot online {now}")

async def heartbeat():
    while True:
        await asyncio.sleep(3600)          # раз в час
        now = datetime.now(LONDON).strftime("%H:%M:%S")
        await send(f"💓 Alive {now}")

# ─── Запуск ────────────────────────────────────────────────────────────────────
async def main():
    await startup()
    await heartbeat()          # никогда не выйдет

if __name__ == "__main__":
    asyncio.run(main())
