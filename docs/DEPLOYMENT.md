# Развёртывание Buyerly

## Состав production

Docker Compose запускает `buyerly-web`, `buyerly-api`, `buyerly-telegram-bot`, `buyerly-worker`, `buyerly-db` и `buyerly-redis`. Публичный порт `8080` принадлежит только веб-сервису; API доступен через его reverse proxy. PostgreSQL хранится в томе `buyerly-postgres`, Redis AOF для общих rate limits — в `buyerly-redis`, журналы — в `/opt/buyerly/logs`.

Обязательные значения в `/opt/buyerly/.env`:

```dotenv
BOT_TOKEN=...
ADMIN_CHAT_ID=...
POSTGRES_PASSWORD=...
WEBAPP_URL=https://buyerly.app
TRUSTED_PROXY_CIDRS=172.16.0.0/12
```

Если `POSTGRES_PASSWORD` отсутствует, deploy-скрипт один раз создаёт случайное значение локально на сервере и ограничивает права файла `.env`.

## Автодеплой

После push в `main` GitHub Actions запускает тесты и вызывает `scripts/deploy.sh` на VPS. Сценарий:

1. блокирует параллельные деплои;
2. создаёт проверенный бэкап текущей базы PostgreSQL;
3. получает точный commit из `main` и собирает версионные образы;
4. проверяет готовность PostgreSQL и Redis, затем запускает миграцию схемы;
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
docker compose logs --tail=100 redis
```

Файлы журналов разделены по процессам: `api.log`, `bot.log`, `worker.log`, `database-migration.log`.

## Резервные копии

`scripts/backup_db.sh` автоматически выполняет горячий дамп PostgreSQL:

- `buyerly_postgres_YYYYMMDD_HHMMSS.sql.gz` через `pg_dump`.

По умолчанию сохраняются последние 30 архивов. Для восстановления PostgreSQL остановите пишущие сервисы, разверните нужный архив через `psql`, затем запустите `migrate`, `api`, `bot`, `worker` и `web`.

## Безопасность хоста и доступ по SSH

1. **Запрет парольной аутентификации**:
   - Вход на VPS разрешен **исключительно по асимметричным SSH-ключам** (`Ed25519`).
   - Парольный вход и интерактивные методы отключены в конфигурации OpenSSH (`/etc/ssh/sshd_config.d/99-hardening.conf`):
     ```sshd_config
     PasswordAuthentication no
     KbdInteractiveAuthentication no
     PermitRootLogin prohibit-password
     PubkeyAuthentication yes
     ```
2. **Управление ключами**:
   - Список доверенных публичных ключей хранится в `~/.ssh/authorized_keys` на VPS.
   - Для деплоя через GitHub Actions используется секрет репозитория `VPS_SSH_KEY`.
3. **Сетевая изоляция**:
   - Порт PostgreSQL (`5432`) закрыт внутри Docker-сети и не публикуется наружу хоста.
   - Порт Redis (`6379`) также доступен только внутри Docker-сети.
   - Наружу выставлен только порт обратного прокси веб-сервиса (`8080`).
