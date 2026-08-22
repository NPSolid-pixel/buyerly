# Changelog

Все ключевые изменения в проекте **Buyerly** документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/),
проект придерживается [Семантического версионирования (SemVer)](https://semver.org/lang/ru/).

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
