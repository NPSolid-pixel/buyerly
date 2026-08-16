FROM python:3.12-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей и установка
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3).read()"

# Запуск сервиса
CMD ["python", "main.py"]
