# Развёртывание Buyerly

## Состав production

Docker Compose запускает `buyerly-web`, `buyerly-api`, `buyerly-telegram-bot`, `buyerly-worker`, `buyerly-db` и `buyerly-redis`. Публичный порт `8080` принадлежит только веб-сервису; API доступен через его reverse proxy. PostgreSQL хранится в томе `buyerly-postgres`, Redis AOF для общих rate limits — в `buyerly-redis`, журналы — в `/opt/buyerly/logs`.

Минимальные значения для production в `/opt/buyerly/.env`:

```dotenv
BOT_TOKEN=...
POSTGRES_PASSWORD=...
WEBAPP_URL=https://buyerly.app
TRUSTED_PROXY_CIDRS=172.16.0.0/12
SESSION_COOKIE_SECURE=true
RESEND_API_KEY=...
EMAIL_FROM="Buyerly <team@buyerly.app>"
OTP_PEPPER=...
```

Если `POSTGRES_PASSWORD` отсутствует, deploy-скрипт один раз создаёт случайное значение локально на сервере и ограничивает права файла `.env`.
`OTP_PEPPER` должен быть отдельным длинным случайным секретом; fallback на
`BOT_TOKEN` сохранён только для совместимости. `ADMIN_CHAT_ID` необязателен и
нужен только для Telegram-алертов.

Для рабочего подключения Facebook дополнительно обязательны:

```dotenv
META_GRAPH_VERSION=v26.0
META_APP_ID=...
META_APP_SECRET=...
META_LOGIN_CONFIG_ID=...
META_OAUTH_REDIRECT_URI=https://buyerly.app/api/meta/oauth/callback
META_TOKEN_ENCRYPTION_KEY=...
```

`META_TOKEN_ENCRYPTION_KEY` — URL-safe base64 Fernet key. При ротации новый ключ
указывается первым, старые decrypt-only ключи — после него через запятую.
Если ключ ещё отсутствует при первом production deploy, preflight создаёт его
криптографически стойким генератором, сохраняет только в server `.env` с правами
`600` и не выводит значение в CI-журнал. Неверный уже заданный ключ не
перезаписывается: deploy завершается до миграции, чтобы не потерять доступ к
существующим шифротекстам.
После выпуска конфигурации все сохранённые OAuth- и ручные System User токены
нужно перевести на первичный ключ внутри API-контейнера:

```bash
docker compose exec api python -m scripts.rotate_meta_tokens
```

Старые ключи удаляются из `META_TOKEN_ENCRYPTION_KEY` только после успешного
завершения команды. Операция транзакционна и не выводит токены в журнал.

Параметры `APP_VERSION`, `DATABASE_URL`, `REDIS_URL`, `API_HOST`, `API_PORT` и
`SERVE_STATIC` для Docker Compose задаются deploy/compose и не требуют ручного
production override. `CORS_ORIGINS` нужен только для явно разрешённых
cross-origin клиентов; `ENABLE_DEV_AUTH` в production всегда должен оставаться
`false`.

Допустимые операционные overrides: `ADMIN_CHAT_ID`,
`DEFAULT_POLL_INTERVAL_MINUTES`, `TELEGRAM_INIT_DATA_MAX_AGE_SECONDS`,
`WEB_SESSION_TTL_HOURS` и `WEB_SESSION_ROTATE_MINUTES`. Пара
`BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` используется только при
первом запуске пустой установки и после создания администратора должна быть
удалена. Полный перечень с безопасными значениями находится в `.env.example`.

Поддерживаемый почтовый transport — Resend REST API; SMTP-параметры runtime не
использует.

## Автодеплой

После push в `main` GitHub Actions запускает тесты и вызывает `scripts/deploy.sh` на VPS. Сценарий:

1. блокирует параллельные деплои;
2. создаёт проверенный бэкап текущей базы PostgreSQL;
3. получает точный commit из `main` и собирает версионные образы;
4. проверяет готовность PostgreSQL и Redis, затем запускает миграцию схемы;
5. запускает API, бота и worker, затем переключает публичный web;
6. выполняет блокирующий read-only smoke для API/auth/workspace/Meta/summary/worker/DB и проверяет параметры ротации журналов; при ошибке возвращает предыдущие образы;
7. удаляет только устаревшие Buyerly image tags, dangling images и build cache, сохраняя активные контейнеры и два последних полных релиза.

Перед сборкой deploy очищает только untracked и неигнорируемые файлы исходного
дерева. Поэтому удалённый ранее Python-модуль или Alembic revision не может
случайно попасть в новый образ; `.env`, логи, резервные копии и остальные
gitignored runtime-данные остаются на месте.

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

Docker stdout/stderr каждого сервиса использует `json-file` с пятью сжатыми
файлами не более 20 MB каждый (до 100 MB на контейнер). Проверка фактически
применённых параметров выполняется после каждого deploy:

```bash
bash scripts/verify_docker_log_rotation.sh
```

## Диск и безопасная очистка Docker

Deploy предупреждает при заполнении диска на 75% и останавливается при 90%.
Перед сборкой и после успешного переключения запускается безопасная очистка:

```bash
DRY_RUN=true bash scripts/cleanup_docker_artifacts.sh
bash scripts/cleanup_docker_artifacts.sh
CHECK_PATH=/opt/buyerly bash scripts/check_disk_usage.sh
```

Очистка сохраняет минимум два последних полных релиза `buyerly-app` и
`buyerly-web`, а также любой image, используемый существующим контейнером.
Удаляются только более старые version tags, dangling images старше семи дней и
build cache старше семи дней. Скрипт никогда не вызывает `docker system prune`,
`docker image prune -a` или `docker volume prune`, поэтому production volumes и
единственный rollback image не могут быть удалены.

При предупреждении сначала выполните dry-run, проверьте список кандидатов,
запустите обычную очистку и повторите проверку диска. Если после этого занято
90% или больше, остановите deploy и найдите источник роста через `docker system
df` и `du` без удаления volumes вручную.

## Post-deploy smoke и rollback gate

После переключения трафика `scripts/post_deploy_smoke.py` проверяет точный SHA
live/readiness, отказ защищённых endpoints без сессии, workspace-isolation,
Meta-конфигурацию без раскрытия значений, summary scope, worker heartbeat и
Alembic/schema contract. Все операции — GET/SELECT; Meta Marketing API и бюджеты
не изменяются.

Результат каждой попытки сохраняется атомарно с правами `600`:

```text
/opt/buyerly/logs/smoke/post-deploy-<full-sha>.json
```

Любая критическая ошибка завершает smoke ненулевым кодом и запускает rollback
предыдущих app/web images. Процедуры реакции собраны в
[`INCIDENT_RUNBOOKS.md`](INCIDENT_RUNBOOKS.md).

## Резервные копии

`scripts/backup_db.sh` автоматически выполняет горячий дамп PostgreSQL:

- `buyerly_postgres_YYYYMMDD_HHMMSS.sql.gz` через `pg_dump`.

По умолчанию сохраняются последние 30 архивов. Для восстановления PostgreSQL остановите пишущие сервисы, разверните нужный архив через `psql`, затем запустите `migrate`, `api`, `bot`, `worker` и `web`.

`migrate` изменяет production-схему только через `alembic upgrade head`. Одновременный запуск блокируется PostgreSQL advisory lock; после миграции контейнер сверяет текущий revision с Alembic head и проверяет наличие всех таблиц и колонок из моделей. Для исторической базы без `alembic_version` разрешён только одноразовый переход на явно зафиксированный baseline `0009_web_sessions`, причём перед stamp выполняется fail-closed проверка схемы. `create_all()` и ручные `ALTER TABLE` в production-runner не используются.

Пользовательские аватары и логотипы хранятся в именованном Docker volume
`buyerly-uploads`: API записывает файлы в `/app/webapp/uploads`, а web-контейнер
монтирует тот же volume read-only в `/usr/share/nginx/html/uploads`. При первом
переходе deploy сохраняет доступные файлы из старого API-контейнера до смены
трафика; последующие релизы повторно используют volume.

Production checkout `/opt/buyerly` приводится к владельцу системного deploy-пользователя через root/passwordless sudo, а `origin` обязан указывать на канонический `hiurano/buyerly` по SSH или HTTPS. Если ownership нельзя безопасно нормализовать либо remote отличается, выкладка останавливается до сборки. В production-образ входят только runtime-каталоги; тесты, документация, локальные диагностические скрипты, транскрипты и исследовательские снимки интерфейсов не копируются.

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
