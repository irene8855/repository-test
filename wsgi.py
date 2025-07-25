from main import app, main_loop
import threading

def start_background_loop():
    threading.Thread(target=main_loop, daemon=True).start()

# Запускаем фоновый поток при старте Gunicorn
start_background_loop()

# Gunicorn будет использовать эту переменную как WSGI приложение
application = app
