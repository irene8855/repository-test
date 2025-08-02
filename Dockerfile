# Используем официальный образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY . .

# Обновляем pip и устанавливаем зависимости
RUN pip install --upgrade pip && pip install -r requirements.txt

# Команда по умолчанию
CMD ["python3", "main.py"]
