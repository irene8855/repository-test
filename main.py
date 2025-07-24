import requests
import pandas as pd
from datetime import datetime, timedelta

def get_frax_candles():
    symbol = "FRAXUSDT"  # ВНИМАНИЕ: без дефиса!
    granularity = 60     # 60 секунд = 1 минута

    # Статический диапазон: 22 июля 2025, 15:06 — 15:11
    end_time = datetime(2025, 7, 22, 15, 11)
    start_time = end_time - timedelta(minutes=5)

    # В миллисекундах
    start = int(start_time.timestamp() * 1000)
    end = int(end_time.timestamp() * 1000)

    url = (
        f"https://api.bitget.com/api/spot/v1/market/candles"
        f"?symbol={symbol}&granularity={granularity}&startTime={start}&endTime={end}"
    )

    print(f"[INFO] Запрос к Bitget: {url}")
    response = requests.get(url)
    
    try:
        response.raise_for_status()
        data = response.json()

        if "data" not in data or not data["data"]:
            print("[WARNING] Пустой ответ от Bitget или данных нет")
            print("Raw response:", response.text)
            return pd.DataFrame()

        df = pd.DataFrame(data["data"], columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.sort_values("timestamp")
        df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
        print("[INFO] Получено свечей:", len(df))

        return df
    
    except Exception as e:
        print("[ERROR] Ошибка при запросе данных:", str(e))
        print("Raw response:", response.text)
        return pd.DataFrame()


# --- Основной вызов ---
if __name__ == "__main__":
    df = get_frax_candles()
    print(df if not df.empty else "[RESULT] Данных нет")
