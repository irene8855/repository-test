FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Явно добавим копирование файла historical_trades.csv, если он есть
COPY historical_trades.csv ./historical_trades.csv

CMD ["python", "main.py"]
