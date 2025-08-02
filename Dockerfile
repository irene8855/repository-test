FROM python:3.11-slim

# Установим рабочую директорию
WORKDIR /app

# Скопируем requirements и установим зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Скопируем все остальные файлы
COPY . .

# Запускаем main.py как основной процесс
CMD ["python", "main.py"]
