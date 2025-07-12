
from telegram import Bot
from datetime import datetime
import os

token = os.getenv("TG_TOKEN")
chat_id = os.getenv("CHAT_ID")

if token and chat_id:
    text = "✅ Crypto bot запущен в " + datetime.utcnow().strftime("%H:%M:%S") + " (UTC)"
    Bot(token).send_message(chat_id=chat_id, text=text)
    print("Сообщение отправлено в Telegram.")
else:
    print("❌ TG_TOKEN или CHAT_ID не установлены в переменных окружения.")
