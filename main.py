# === самый-минимальный тестовый скрипт ===
import os, time
from datetime import datetime
from telegram import Bot

print("=== Bot container started ===")        # строка появится в логах

TOKEN   = os.getenv("TG_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise RuntimeError("TG_TOKEN или CHAT_ID не заданы в Secrets Fly.io")

Bot(TOKEN).send_message(
    chat_id=CHAT_ID,
    text="✅ Crypto-bot online " + datetime.utcnow().strftime("%H:%M:%S")
)

# держим процесс живым, чтобы Fly не считал его упавшим
while True:
    time.sleep(3600)          # спит час, потом снова; можно 300 с
