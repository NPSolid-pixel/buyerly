# Changelog

Все ключевые изменения в проекте **Buyerly** документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/),
проект придерживается [Семантического версионирования (SemVer)](https://semver.org/lang/ru/).

---

## [Unreleased]

### Security
- **Санация уведомлений `showToast` и устранение Stored/Reflected XSS**:
  - Переписана функция `showToast()` в `webapp/js/app.js` с использованием изолированных DOM-элементов и безопасного свойства `textContent` вместо уязвимой конкатенации в `innerHTML`.
  - Добавлена нормализация входных параметров `message`: безопасная обработка строк, экземпляров `Error` и объектов ответов API без риска вывода `[object Object]`.
  - Реализована защита от переполнения экрана и утечек памяти (anti-flooding: лимит до 5 активных тостов в DOM).
  - В `loadAccountsAndGroups()` добавлено экранирование текста ошибок через `escapeHtml(err.message)` в блоке `empty-state`.
  - Добавлен контрактный тест `test_toast_and_error_sanitization_contract` в `tests/test_frontend_contract.py`.

### Fixed
- **Устранение утечки стейта между воркспейсами (Multi-Tenancy Bleed)**:
  - Добавлена функция `resetWorkspaceState()` в `webapp/js/app.js` для полной очистки временных выборок чекбоксов (`selectedAccounts`, `selectedRuleIds`, `linkRuleSelectedAccountIds`, `metaOAuth.selectedAccountIds`), кэша аналитики и сводки (`summaryCache`, `summary`), остановленных адсетов, аудит-логов и блокировок запросов.
  - Введено версионирование стейта через `state.workspaceEpoch`: критические асинхронные загрузчики (`loadAccounts`, `loadSummary`, `loadLogsTab`, `loadFacebookAccounts`, `loadPresets`, `loadRuleGroups`) защищены от состояний гонки (Race Conditions) и отбрасывают ответы от запросов предыдущего воркспейса.
  - Автоматизирована перезагрузка текущей активной вкладки (`summary`, `logs`, `settings`) при переключении воркспейса.
  - Добавлена обработка смены воркспейса при навигации через историю браузера (`popstate`).
  - Добавлен контрактный тест `test_workspace_multi_tenancy_bleed_isolation_contract` в `tests/test_frontend_contract.py`.
- **Устранение runtime-краша при пакетном удалении правил**:
  - Заменены ошибочные вызовы необъявленной функции `showNotification()` на `showToast()` в `webapp/js/app.js`.
- **Безопасное экранирование HTML в TelegramNotifier**:
  - Все динамические поля (`eval_result.reason`, `adset_name`, `account_name`, `user_msg`, `status_label`) теперь строго экранируются через `html.escape()`.
  - Устранена ошибка парсинга HTML в Telegram при срабатывании правил со знаками `<`, `>`, `&` (например `CTR (< 1.5%) < 1.0%`).
  - Исправлен порядок обрезки `user_msg`: срез максимальной длины `[:350]` выполняется до экранирования HTML, исключая повреждение сущностей (`&amp;`, `&lt;`).
  - Добавлен набор тестов `tests/test_notifier_escaping.py`.
- **Семантическое сопоставление кастомных конверсий Meta API**:
  - В метод `_conversion_counts` добавлена семантическая классификация кастомных конверсий по ключевым словам для независимого сопоставления лидов (`lead`, `form`, `submit`, `contact`, `schedule`), регистраций (`reg`, `signup`, `account`) и покупок (`purchase`, `buy`, `order`, `sale`, `checkout`).
  - Обеспечен детерминированный выбор безымянных кастомных ID событий (`offsite_conversion.custom.<id>`) без коллизий между разными воронками.
  - Добавлены тесты одновременного присутствия нескольких кастомных конверсий в `tests/test_meta_client.py`.
- **Устранение Multi-Process Cache Drift (Синхронизация статусов между Worker и Web UI)**:
  - Создана таблица `adset_inventory_cache` и сервис `AdsetInventoryService` в PostgreSQL как единый источник правды для инвентаря адсетов между процессами воркера и API.
  - Добавлен адаптер `PostgreSQLInventoryCache` для `MetaClient` с поддержкой внешнего провайдера кэша и чистой архитектуры.
  - Реализована защита от состояния гонки «Stale Overwrite» при конкурентных сетевых ответах Meta API.
  - Добавлена инвалидация кэша инвентаря и сводки (`invalidate_summary_cache`) при любых мутациях статусов и бюджетов.
  - Обеспечена финансовая безопасность (Financial Safety): успех переключения статуса в Meta не маскируется локальными ошибками сохранения БД.
  - Добавлена миграция Alembic `0002_adset_inventory_cache.py`.
- **Парсинг Omni и Custom Conversions в Meta Insights**:
  - Добавлена поддержка событий `omni_lead`, `omni:lead`, `onsite_conversion.lead_grouped`, `leadgen.other`, `leadgen`, `leadgen_grouped` в метод извлечения конверсий `_conversion_counts`.
  - Добавлен безопасный fallback для пользовательских конверсий (`offsite_conversion.custom.*`, `custom:*`, `omni_custom`) для предотвращения ложных срабатываний стоп-правил (False STOP).
  - Обеспечена каноническая дедупликация событий во избежание задвоения лидов.

---

## [1.2.0] - 2026-08-23

### 🗄 Модернизация базы данных, чистый Multi-Tenant и декларативный Alembic (Database Modernization Release)

#### Added
- **Декларативные миграции Alembic**:
  - Интегрирован Alembic (`alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`) с полной поддержкой асинхронного движка SQLAlchemy (`asyncpg`) и пула потоков для синхронных команд.
  - Создана эталонная базовая миграция `0001_initial_schema.py` для всех 25 таблиц платформы.
- **PostgreSQL JSONB**: Все JSON-колонки во всех 25 моделях переведены на нативный тип PostgreSQL `JSONB` с бинарной десериализацией и поддержкой индексации.
- **Унифицированный TIMESTAMPTZ**: Все временные метки (`created_at`, `updated_at`, `expires_at` и др.) приведены к единому стандарту `DateTime(timezone=True)`.
- **Документация**: Добавлен подробный архитектурный отчет [`docs/database_modernization_and_migrations.md`](docs/database_modernization_and_migrations.md).

#### Changed
- **Рефакторинг сущности пользователя (`TelegramUser` → `User`)**: Главная сущность авторизации и аккаунта пользователя переименована в универсальный `User` (таблица `users`) с сохранением обратной совместимости через алиас.
- **Multi-Tenant Ownership Architecture**:
  - Полностью выпилена рудиментарная колонка `owner_id: str` (двойное владение) из всех 13 моделей.
  - Логика владения переведена на стандарт **Multi-tenant Workspaces**: изоляция по `workspace_id: int` с явной привязкой создателя `owner_user_id: int`.
  - Обновлены и усилены селекторы `owned_by`, `entity_is_owned_by`, `assign_owner` в `core/ownership.py`.

#### Removed
- **Тотальное удаление SQLite**: Полностью вычищен пакет `aiosqlite`, рудиментарные ветки поддержки SQLite, локальные файлы `.db` и фоллбэки. База данных PostgreSQL является единственным стандартом платформы.

#### Fixed
- **CI/CD Auto-Deploy Pipeline Resilience**:
  - Исправлен запуск скрипта резервного копирования `scripts/backup_db.sh` под строгим режимом `set -euo pipefail`.
  - Улучшен пайплайн деплоя `.github/workflows/deploy.yml`: добавлена обязательная предварительная синхронизация кодовой базы на сервере до запуска `scripts/deploy.sh`.
  - В автомиграцию `migrate_automation_settings_contract` добавлены колонки `updated_at` и `admin_chat_id`.
- **Десериализация JSONB в Summary API**: Исправлен парсинг `AnalyticsViewPreference.config` для поддержки нативных словарей Python.

---

## [1.1.0] - 2026-08-23

### 🔒 Безопасность и отказоустойчивость (Hardening & Security Release)

#### Added
- **Rate Limiting Engine**: Потокобезопасный `RateLimiter` на базе скользящего окна (Sliding Window) с автоматической очисткой устаревших записей памяти и поддержкой заголовков прокси (`X-Forwarded-For`, `X-Real-IP`).
- **Rate Limiting на критических эндпоинтах**:
  - `/api/auth/login` (10 req/min) — защита от подбора паролей.
  - `/api/auth/request-temporary-password` (5 req/min) — защита от флуда одноразовыми паролями.
  - `/api/invites/{token}` и `/api/invites/{token}/accept` (30 и 10 req/min) — защита от перебора инвайтов.
  - `/api/onboarding/check-slug` (30 req/min) — защита от перебора названий воркспейсов.
  - `/api/meta/oauth/start` (10 req/min) — защита от исчерпания сессий OAuth.
  - `/api/accounts/parse-raw` (20 req/min) — защита парсера.
- **OTP Brute-force Protection**: Блокировка одноразовых кодов после 5 неверных попыток (`failed_attempts >= 5`) и 60-секундный кулдаун на повторную отправку.
- **Payload Size Middleware**: Ограничение максимального размера тела запроса (10 МБ для загрузки медиафайлов, 1 МБ для всех остальных API-запросов) с возвратом HTTP 413 `Payload Too Large`.
- **ReDoS Protection**: Ограничение входного текста в `parse_fb_raw_accounts` до 64 КБ / 2000 строк, обрезка названий до 120 символов и лимит вывода до 500 записей.
- **Security Headers**: Автоматическое добавление заголовков `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`.
- **Docs**: Добавлен подробный итоговый отчет по безопасности [`docs/security_audit_report.md`](docs/security_audit_report.md).
- **Automated Tests**: Набор тестов расширен до 197 сценариев, включая проверки скользящего окна, блокировки OTP, валидации инвайтов и изоляции воркспейсов.

#### Fixed
- **Funnel Guard Bypass**: Исправлено поведение правил остановки: защита воронок `funnel_guarded` теперь требует явного минимального порога конверсий (`min_conversions_for_cpa > 0`), исключая обход стопа при нулевых конверсиях.
- **Cross-Workspace Hijack**: Запрещен межворкспейсный захват рекламных кабинетов через `Batch Add` и ручной импорт.
- **Rule Snapshot Isolation**: Предотвращена нежелательная каскадная мутация работающих правил в рекламных кабинетах при изменении пресетов.
- **Session Token Entropy**: Генерация токенов авторизации переведена на криптостойкий генератор `secrets.token_urlsafe(32)` (256 бит энтропии).
- **Targeted Invites**: Приглашения с указанием Email теперь могут быть приняты только пользователем с подтвержденным соответствующим email.
- **SVG Stored XSS**: Запрещена загрузка файлов формата SVG для аватаров и логотипов воркспейсов.
- **CORS Misconfiguration**: Исправлена небезопасная комбинация `allow_credentials=True` при открытом `origins=*`.

---

## [1.0.0] - 2026-08-18

### Initial Release
- Запуск веб-платформы Buyerly и Telegram Mini App.
- Интеграция с Meta Marketing API (OAuth, авто-правила, инсайты, управление бюджетами).
- Поддержка мульти-пользовательского режима и рабочих пространств (Workspaces).
- Telegram-бот с персональными уведомлениями баеров.
- Поддержка часовых поясов и отслеживание смены суток для рекламных кабинетов.
