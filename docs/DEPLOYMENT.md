# Развёртывание Buyerly

## Состав production

Docker Compose запускает `buyerly-web`, `buyerly-api`, `buyerly-telegram-bot`, `buyerly-worker` и `buyerly-db`. Публичный порт `8080` принадлежит только веб-сервису; API доступен через его reverse proxy. PostgreSQL хранится в именованном томе `buyerly-postgres`, журналы — в `/opt/buyerly/logs`.

Обязательные значения в `/opt/buyerly/.env`:

```dotenv
BOT_TOKEN=...
ADMIN_CHAT_ID=...
POSTGRES_PASSWORD=...
WEBAPP_URL=https://smattrades.com
```

Если `POSTGRES_PASSWORD` отсутствует, deploy-скрипт один раз создаёт случайное значение локально на сервере и ограничивает права файла `.env`.

## Автодеплой

После push в `main` GitHub Actions запускает тесты и вызывает `scripts/deploy.sh` на VPS. Сценарий:

1. блокирует параллельные деплои;
2. создаёт проверенный бэкап текущей базы;
3. получает точный commit из `main` и собирает версионные образы;
4. проверяет PostgreSQL и запускает одноразовую миграцию;
5. запускает API, бота и worker, затем переключает публичный web;
6. проверяет `/health/ready`; при ошибке возвращает предыдущие образы.

При первом переходе прежний SQLite-монолит останавливается непосредственно перед копированием данных. До этого момента он продолжает обслуживать пользователей. Исходный файл и его архив не удаляются.

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

`scripts/backup_db.sh` автоматически выбирает источник:

- работающий PostgreSQL → `buyerly_postgres_YYYYMMDD_HHMMSS.sql.gz` через `pg_dump`;
- прежний SQLite до миграции → `mediabuyer_YYYYMMDD_HHMMSS.db.gz` через консистентный backup API.

По умолчанию сохраняются последние 30 архивов каждого типа. Для восстановления PostgreSQL остановите пишущие сервисы, разверните нужный архив через `psql`, затем запустите `migrate`, `api`, `bot`, `worker` и `web`.

## Первый переход с SQLite

Сервис `migrate` создаёт актуальную схему, читает `/opt/buyerly/data/mediabuyer.db` только для чтения, копирует все известные таблицы в одной транзакции и обновляет PostgreSQL sequence. Если целевая база уже содержит данные, повторное копирование пропускается. Это делает повторный deploy безопасным.
