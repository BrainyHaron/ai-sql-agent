# Используем легкий и безопасный базовый образ Python
FROM python:3.10-slim

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Устанавливаем системные библиотеки, необходимые для компиляции драйвера psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь исходный код проекта в контейнер
COPY . .

# Команда запуска веб-приложения Streamlit на порту 8501 для доступа извне
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]