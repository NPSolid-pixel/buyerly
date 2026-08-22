# Развёртывание Buyerly

## Состав production

Docker Compose запускает `buyerly-web`, `buyerly-api`, `buyerly-telegram-bot`, `buyerly-worker` и `buyerly-db`. Публичный порт `8080` принадлежит только веб-сервису; API доступен через его reverse proxy. PostgreSQL хранится в именованном томе `buyerly-postgres`, журналы — в `/opt/buyerly/logs`.

Обязательные значения в `/opt/buyerly/.env`:

```dotenv
BOT_TOKEN=...
ADMIN_CHAT_ID=...
POSTGRES_PASSWORD=...
WEBAPP_URL=https://buyerly.app
```

Если `POSTGRES_PASSWORD` отсутствует, deploy-скрипт один раз создаёт случайное значение локально на сервере и ограничивает права файла `.env`.

## Автодеплой

После push в `main` GitHub Actions запускает тесты и вызывает `scripts/deploy.sh` на VPS. Сценарий:

1. блокирует параллельные деплои;
2. создаёт проверенный бэкап текущей базы PostgreSQL;
3. получает точный commit из `main` и собирает версионные образы;
4. проверяет готовность PostgreSQL и запускает миграцию схемы;
5. запускает API, бота и worker, затем переключает публичный web;
6. проверяет `/health/ready`; при ошибке возвращает предыдущие образы.

Ручной запуск:

```bash
cd /opt/buyerly
bash scripts/deploy.sh
```

## Проверка и журналы

```bash
docker compose ps
curl -fsS http://127.0.0.1:8080/health/ready
docker compose logs --tail=100 api
docker compose logs --tail=100 worker
docker compose logs --tail=100 bot
```

Файлы журналов разделены по процессам: `api.log`, `bot.log`, `worker.log`, `database-migration.log`.

## Резервные копии

`scripts/backup_db.sh` автоматически выполняет горячий дамп PostgreSQL:

- `buyerly_postgres_YYYYMMDD_HHMMSS.sql.gz` через `pg_dump`.

По умолчанию сохраняются последние 30 архивов. Для восстановления PostgreSQL остановите пишущие сервисы, разверните нужный архив через `psql`, затем запустите `migrate`, `api`, `bot`, `worker` и `web`.
