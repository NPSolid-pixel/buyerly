# План реализации 86eyr5z4q: Workspace scope для Meta connections

## Цель

Обеспечить строгую изоляцию подключений Meta (Facebook OAuth connections, профили, токены доступа и кэш активов) по рабочим пространствам (`Workspace`):
- Независимые FB-профили и токены в разных командах/проектах даже в рамках одной учетной записи.
- Устранение уязвимостей Scope Drift & Race Condition при OAuth-авторизации.
- Защита фонового воркера и API от межворкспейсных утечек токенов.

## Контракт

1. **База данных и модели**:
   - `MetaConnection.workspace_id`: `ForeignKey("workspaces.id", ondelete="CASCADE")`, `nullable=False`.
   - `MetaOAuthState.workspace_id`: `ForeignKey("workspaces.id", ondelete="CASCADE")`, `nullable=False`.
   - Ограничение уникальности `meta_connections`: `(workspace_id, provider_user_id)`.
   - Миграция `0016_meta_connections_workspace_scope` идемпотентно выполняет backfill, дедупликацию и установку констрейнтов.

2. **Backend API**:
   - `/api/meta/oauth/start`: фиксирует `workspace_id` текущей сессии в `MetaOAuthState`, блокирует роль `viewer`.
   - `/api/meta/oauth/callback`: извлекает `workspace_id` из сохраненного `MetaOAuthState`, проверяет членство пользователя в этом воркспейсе, создает/обновляет подключение строго в рамках `(workspace_id, provider_user_id)`.
   - Обрабатывает возможную гонку параллельных запросов через graceful retry.
   - `_workspace_connection`: `owner` и `admin` управляют подключениями воркспейса; `buyer` управляет своими; `viewer` заблокирован от мутаций.

3. **Core и Worker**:
   - `resolve_account_access_token`: проверяет `account.workspace_id == connection.workspace_id`.
   - Фоновый воркер: валидирует совпадение воркспейса перед обращением к Graph API.

## Проверка готовности

- Тесты изоляции нескольких воркспейсов с одинаковым FB-профилем.
- Тесты сохранения исходного воркспейса при смене активного воркспейса во время OAuth.
- Тесты блокировки роли `viewer` и несовпадения воркспейса токена.
- Полный облачный Quality Gate (GitHub Actions) пройден до слияния в `main`.
