# План реализации 86eyr60pg: Миграция старых ручных токенов на OAuth

## Цель

Обеспечить бесшовный и безопасный перевод рекламных кабинетов, ранее добавленных по ручным токенам System User, на авторизацию через Facebook Login (OAuth) без потери правил автоматики, истории и настроек.

## Контракт

1. **API Discovery (`GET /api/meta/connections/{id}/assets`)**:
   - Возвращает гранулярные статусы обнаруженных активов:
     - `import_status`: `"not_imported"`, `"this_connection"`, `"manual_token"`, `"other_connection"`.
     - `can_migrate`: `True` для кабинетов со статусом `"manual_token"`.
     - `rules_count`: количество назначенных на кабинет правил автоматики.
     - `rules_enabled`: статус активности автоматики.
     - `custom_name`: внутреннее название в Buyerly.
     - `imported`: `True` только для `"this_connection"`, чтобы ручные кабинеты оставались доступными для выбора в мастере миграции.

2. **API Import (`POST /api/meta/connections/{id}/import`)**:
   - При переносе существующего кабинета:
     - Привязывает `meta_connection_id = connection.id`.
     - Очищает `access_token` и `access_token_encrypted`.
     - Строго сохраняет `active_rules`, `rules_enabled`, `custom_name`, `note` и привязку к группам.
     - Фиксирует событие аудита `ACCOUNT_MIGRATED_TO_OAUTH` в `audit_events`.
     - Возвращает `migrated: True`, `rules_count` и `rules_enabled` в ответе API.

3. **Frontend (`webapp/`)**:
   - В модальном окне обнаружения активов (`modalMetaAssets`) помечает кабинеты на ручном токене бейджем «Миграция на OAuth» и показывает количество сохраняемых правил.
   - Кнопка «Выбрать все» выбирает новые кабинеты и кабинеты для миграции.
   - В модальном окне деталей кабинета (`modalAccountDetails`) отображает рекомендацию перехода на OAuth с кнопкой быстрого вызова мастера подключения.

## Проверка готовности

- Тесты в `tests/test_meta_oauth_api.py`:
  - `test_discover_assets_distinguishes_manual_token_accounts_and_rules_count`
  - `test_import_migrates_manual_account_to_oauth_preserving_rules_and_state`
  - `test_migrate_enforces_workspace_isolation_and_rbac`
- Контрактные тесты в `tests/test_frontend_contract.py`.
- 100% успешный проход удалённого Quality Gate (GitHub Actions).
