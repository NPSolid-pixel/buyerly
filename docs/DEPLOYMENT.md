# 🚀 Руководство по развертыванию и DevOps (Deployment Guide)

В данном документе подробно описаны архитектура окружения, настройка Production VPS, автоматизация CI/CD через GitHub Actions, управление базой данных и процедуры обслуживания проекта **Buyerly**.

---

## 📌 Оглавление
1. [Характеристики Production-сервера](#1-характеристики-production-сервера)
2. [Структура директорий на сервере](#2-структура-директорий-на-сервере)
3. [Настройка CI/CD через GitHub Actions](#3-настройка-cicd-через-github-actions)
4. [Скрипты деплоя и резервного копирования](#4-скрипты-деплоя-и-резервного-копирования)
5. [Управление контейнером Docker](#5-управление-контейнером-docker)
6. [Резервное копирование и восстановление БД](#6-резервное-копирование-и-восстановление-бд)
7. [Диагностика и решение проблем (Troubleshooting)](#7-диагностика-и-решение-проблем-troubleshooting)

---

## 1. Характеристики Production-сервера

* **IP-адрес:** `147.45.78.208`
* **Пользователь:** `root`
* **ОС:** Ubuntu 26.04 LTS
* **Среда выполнения:** Docker Engine 27+ & Docker Compose v2+
* **Расположение проекта:** `/opt/buyerly`
* **Режим работы:** Контейнер `buyerly-bot` (`restart: always`), 24/7 автономный мониторинг.

---

## 2. Структура директорий на сервере

```
/opt/buyerly/
├── data/
│   └── mediabuyer.db         # Персистентный файл SQLite базы данных (Volume)
├── logs/
│   └── buyerly.log           # Сквозной ротируемый лог работы (Volume)
├── backups/
│   ├── mediabuyer_*.db.gz    # Сжатые резервные копии базы (хранятся последние 30 шт)
├── scripts/
│   ├── deploy.sh             # Скрипт автоматического обновления
│   └── backup_db.sh          # Скрипт горячего бэкапа SQLite
├── .env                      # Конфигурация и секреты (не коммитится в Git)
├── docker-compose.yml        # Конфигурация контейнера
└── Dockerfile                # Инструкция сборки образа
```

---

## 3. Настройка CI/CD через GitHub Actions

Автодеплой настроен в файле [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml).

Запуски на VPS сериализованы системной блокировкой. Если ручной запуск и
GitHub Actions одновременно получили один и тот же commit SHA, первый процесс
завершает обновление, а второй проверяет уже запущенный контейнер и выходит без
повторного пересоздания сервиса.

### Схема работы:
1. Разработчик пушит изменения в ветку `main`.
2. GitHub Actions поднимает виртуальную машину `ubuntu-latest`, устанавливает зависимости и выполняет тесты:
   ```bash
   python -m unittest discover tests -v
   ```
3. Если тесты прошли успешно:
   * Открывается защищенная SSH-сессия к VPS.
   * На сервере запускается скрипт `/opt/buyerly/scripts/deploy.sh`.
   * Выполняется бэкап базы данных, `git pull`, `docker compose up -d --build` и верификация запуска.

### Необходимые GitHub Secrets (Settings $\rightarrow$ Secrets and variables $\rightarrow$ Actions):

| Имя секрета | Обязателен | Значение / Описание | Пример |
|---|---|---|---|
| `VPS_HOST` | **Да** | IP-адрес или домен сервера | `147.45.78.208` |
| `VPS_USERNAME` | Нет (по умолч. `root`) | Пользователь для SSH | `root` |
| `VPS_SSH_KEY` | **Да** | Приватный SSH-ключ (`id_ed25519` / `id_rsa`) | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `VPS_SSH_PASSPHRASE` | Нет | Пароль от SSH-ключа (если ключ зашифрован) | |
| `VPS_PORT` | Нет (по умолч. `22`) | SSH порт | `22` |

#### Как сгенерировать выделенный Deploy Key для GitHub Actions:
На сервере (или локальной машине):
```bash
ssh-keygen -t ed25519 -C "github-actions-deploy@buyerly" -f ~/.ssh/github_deploy
```
* Публичный ключ `~/.ssh/github_deploy.pub` добавьте в `~/.ssh/authorized_keys` на VPS:
  ```bash
  cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys
  ```
* Содержимое приватного ключа `~/.ssh/github_deploy` вставьте в секрет `VPS_SSH_KEY` в репозитории GitHub.

---

## 4. Скрипты деплоя и резервного копирования

### Автодеплой: `scripts/deploy.sh`
Скрипт выполняет 4 последовательных шага с контролем ошибок:
1. Запускает бэкап базы данных через `scripts/backup_db.sh`.
2. Синхронизирует репозиторий: `git fetch origin main && git reset --hard origin/main`.
3. Пересобирает и перезапускает Docker-контейнер: `docker compose up -d --build`.
4. Проверяет статус контейнера `buyerly-bot` и при сбое выводит последние 50 строк логов.

**Ручной вызов на сервере:**
```bash
bash /opt/buyerly/scripts/deploy.sh
```

---

## 5. Управление контейнером Docker

### Полезные команды для администратора:

```bash
# Просмотр статуса контейнера
docker compose ps

# Просмотр логов в реальном времени (follow)
docker compose logs -f --tail=100

# Перезапуск бота
docker compose restart buyerly

# Остановка бота
docker compose down

# Пересборка с нуля (без кэша)
docker compose build --no-cache && docker compose up -d
```

---

## 6. Резервное копирование и восстановление БД

### Автоматический бэкап (`scripts/backup_db.sh`)
* Создает снимок базы данных через безопасную команду `sqlite3 .backup` (или `cp`).
* Сжимает файл архиватором `gzip` (`mediabuyer_YYYYMMDD_HHMMSS.db.gz`).
* Автоматически удаляет старые копии, сохраняя **последние 30 архивов**.

### Периодический бэкап по расписанию (Cron):
Рекомендуется настроить запуск бэкапа каждые 6 часов:
```bash
crontab -e
```
Добавьте строку:
```cron
0 */6 * * * /bin/bash /opt/buyerly/scripts/backup_db.sh > /dev/null 2>&1
```

### Восстановление базы из резервной копии:
1. Остановите контейнер бота:
   ```bash
   docker compose down
   ```
2. Разархивируйте нужный снимок:
   ```bash
   gunzip -c /opt/buyerly/backups/mediabuyer_20260816_120000.db.gz > /opt/buyerly/data/mediabuyer.db
   ```
3. Запустите контейнер:
   ```bash
   docker compose up -d
   ```

---

## 7. Диагностика и решение проблем (Troubleshooting)

### 1. Ошибка 409 Conflict: Terminated by other getUpdates request
* **Причина:** Бот запущен одновременно в двух местах (например, локально и на сервере).
* **Решение:** Убедитесь, что локальный процесс Python остановлен (`pkill -f "python main.py"`). На продакшене должен работать только один экземпляр в Docker.

### 2. Ошибка Meta API OAuth 190 (Token Expired)
* **Причина:** Истек срок действия Access Token рекламного кабинета.
* **Решение:** Сгенерируйте бессрочный токен по [инструкции](TOKEN_GUIDE.md) и обновите его в боте через меню `➕ Добавить кабинеты` или команду `/add`.

### 3. Ошибка прав доступа к базе SQLite `attempt to write a readonly database`
* **Решение:** Проверьте владельца директории `data/`:
  ```bash
  chown -R 1000:1000 /opt/buyerly/data /opt/buyerly/logs
  chmod -R 775 /opt/buyerly/data /opt/buyerly/logs
  ```
