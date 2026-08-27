FROM python:3.12-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей и установка
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only production runtime ownership. Documentation, tests, captures and
# workstation utilities never enter the API/bot/worker image.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY api ./api
COPY bot ./bot
COPY core ./core
COPY database ./database
COPY meta_api ./meta_api
COPY rules ./rules
COPY scheduler ./scheduler
COPY services ./services
COPY scripts/rotate_meta_tokens.py ./scripts/rotate_meta_tokens.py

# The command is selected per service in docker-compose.yml.
CMD ["python", "-m", "services.api"]
