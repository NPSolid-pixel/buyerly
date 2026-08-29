# Buyerly BL-107 — Meta onboarding and connection lifecycle

Date: 2026-08-29
ClickUp: `BL-107` (`86eyr6079`)
Branch: `feat/bl-107-meta-connections`
Status: implemented; awaiting GitHub Actions

## Goal

Завершить пользовательский путь Meta от первого подключения или приглашения владельца профиля до импорта кабинетов и дальнейшего управления доступом. Все состояния должны быть честными, доступными, адаптивными и не раскрывать токены.

## Work packages

### 1. Connection lifecycle contract

- Разделить безопасное отключение Buyerly и явный отзыв разрешений в Meta.
- Возвращать из API подтверждённый результат отключения, количество деактивированных кабинетов и результат внешнего revoke.
- Сохранить workspace/RBAC isolation, шифрование токена, audit trail и отключение правил.

### 2. Connections table and health

- Отдавать для каждого Facebook-профиля точные counts кабинетов и Business Manager, а также агрегированный расход только его кабинетов.
- Унифицировать статусы active/expiring/expired/missing_scopes/needs_reconnect и показывать дату последней проверки.
- Добавить понятные действия для проверки, импорта, переподключения, отключения и явного revoke.

### 3. Invite owner flow

- Переименовать сценарий в приглашение владельца Facebook-профиля и объяснить одноразовость ссылки.
- Закрыть loading/empty/error/expired/used/revoked/success состояния и дать безопасные retry-действия.
- Добавить busy/feedback states для создания, копирования, обновления и отзыва приглашения.

### 4. Responsive and accessibility

- Исключить document-level horizontal overflow на mobile/tablet/desktop.
- Обеспечить читаемые mobile cards, wrapping длинных ID/ошибок, видимые focus states и текстовые labels у icon-only controls.
- Использовать `aria-live`, `aria-busy`, `aria-label`, disabled state и фокус после операций.

### 5. Verification

- Обновить API и frontend contract tests для connect/import/reconnect/disconnect/revoke/invite/health contracts.
- Выполнить разрешённые статические проверки и browser QA без production mutations.
- Не запускать локальные pytest/unittest; тесты выполнить только через GitHub Actions.
- Открыть отдельный PR с ClickUp ID, дождаться зелёного CI и остановиться до merge.

## Definition of done

- Каждый этап подключения имеет loading, empty, error, expired/attention и success состояние.
- Таблица и mobile cards показывают данные только соответствующего подключения.
- Обычное отключение не вызывает отзыв разрешений Meta; revoke запускается только отдельным явно подтверждённым действием.
- В UI и ответах API нет plaintext токенов, scopes не используются как секретные значения, ошибки санитизированы.
- Контрактные тесты покрывают новый lifecycle, CI зелёный, PR не слит.

## Safety constraints

- Не выполнять реальные disconnect/revoke/import/reconnect действия в production.
- Не закрывать ClickUp до подтверждённого production deployment.
- Не мёрджить PR без явного подтверждения пользователя.

## Verification record

- Production Connections baseline inspected read-only; no connect, validate, import, reconnect, disconnect, invite revoke or Meta permission revoke action was triggered.
- Existing production layout has zero document-level horizontal overflow at `390`, `768`, `1024` and `1440` px; the updated CSS preserves the same breakpoint contract and contains long IDs/actions.
- Local public invite success and network-error/retry states were loaded with the production assets at `390`, `768` and `1024` px; `scrollWidth === clientWidth` at every checked size.
- Public `/connect/meta/*` routing was verified to bypass Buyerly auth; `meta_status=connected` resolves to success, including a trailing-slash URL.
- Python modules compile and `git diff --check`, duplicate-ID and modal-count static checks pass.
- Local pytest/unittest were intentionally not run. GitHub Actions remains the test authority.
