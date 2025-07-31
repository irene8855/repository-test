FROM python:3.10-slim

# Установим переменные среды
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Обновим пакеты и установим только необходимые зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libatlas-base-dev \
    libffi-dev \
    libssl-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка зависимостей Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Удаляем dev-зависимости, чтобы минимизировать размер образа
RUN apt-get purge -y --auto-remove build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем проект
COPY . .

# Команда запуска
CMD ["python", "main.py"]
