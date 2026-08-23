# Архитектурный отчёт: Модернизация базы данных, Multi-Tenant Ownership и Alembic (v1.2.0)

**Дата:** 23 августа 2026 г.  
**Статус:** Выполнено и внедрено в \`main\` (Автодеплой на боевой VPS успешен)  
**Область:** Слой базы данных, модели SQLAlchemy, система владения данными, миграции схемы, CI/CD.

---

## 🎯 1. Цели и предпосылки

В ходе развития проекта Buyerly накопились архитектурные рудименты:
1. **Фрагментарный SQLite**: В проекте присутствовали остатки \`aiosqlite\` и дублирование логики для SQLite и PostgreSQL.
2. **Устаревшая схема именования (\`TelegramUser\`)**: Корневой пользователь назывался \`TelegramUser\`, что путало доменную логику платформы, поддерживающей Email, веб-пароли и Workspace RBAC.
3. **Двойное владение (\`owner_id: str\` vs \`workspace_id: int\` / \`owner_user_id: int\`)**: В 13 моделях хранилась строковая колонка \`owner_id\`, куда изначально записывался Telegram ID. Это вызывало дублирование и конфликт прав доступа.
4. **Нетипизированные JSON и разнородные TIMESTAMPTZ**: В моделях использовался базовый тип \`JSON\` вместо высокопроизводительного \`JSONB\` с бинарным хранением, а часть дат были naive (без таймзоны).
5. **Отсутствие декларативного версионирования схемы (Alembic)**: Схема обновлялась эвристическими функциями \`inspect()\`.

---

## 🛠 2. Выполненные архитектурные преобразования

### 2.1 Полное удаление SQLite
- Пакет \`aiosqlite\` удален из \`requirements.txt\`.
- Удалены все локальные \`.db\` файлы, ветки условий \`if is_sqlite\` и фоллбэки.
- Движок базы данных зафиксирован: **PostgreSQL 16+** с асинхронным драйвером \`asyncpg\`.

### 2.2 Рефакторинг сущности пользователя: \`User\`
- Таблица переименована из \`telegram_users\` в \`users\`.
- Класс модели в [\`database/models.py\`](../database/models.py) переименован в \`User\`.
- Для сохранения прозрачной обратной совместимости оставлен алиас \`TelegramUser = User\`.

### 2.3 Переход на PostgreSQL \`JSONB\` и единый \`TIMESTAMPTZ\`
- Все 25 моделей базы данных приведены к строгим типам PostgreSQL:
  - Колонки конфигураций, метаданных и правил (\`active_rules\`, \`permissions\`, \`metadata_json\`, \`config\`, \`details_json\` и др.) переведены на \`JSONB\`.
  - Все временные метки стандартизированы через \`DateTime(timezone=True)\`.

### 2.4 Чистая Multi-Tenant Ownership модель
- Полностью удалена колонка \`owner_id\` из всех моделей:
  - \`Account\`
  - \`AuditEvent\`
  - \`RulePreset\`
  - \`RuleGroup\`
  - \`AccountGroup\`
  - \`SummarySnapshot\`
  - \`AnalyticsViewPreference\`
  - \`ExportTemplate\`
  - \`BudgetSpendHistory\`
  - \`TokenUsageHourly\`
  - \`SystemHealthRecord\`
  - \`MetricCache\`
- Вся фильтрация прав и владения унифицирована в [\`core/ownership.py\`](../core/ownership.py):
  \`\`\`python
  def owned_by(model, user: User):
      conditions = []
      if getattr(user, "active_workspace_id", None) is not None and hasattr(model, "workspace_id"):
          conditions.append(model.workspace_id == user.active_workspace_id)
      if user.id is not None and hasattr(model, "owner_user_id"):
          conditions.append(model.owner_user_id == user.id)
      return or_(*conditions) if conditions else true()
  \`\`\`

### 2.5 Интеграция Alembic
- Сконфигурирован \`alembic.ini\` и асинхронный раннер \`alembic/env.py\`.
- Создана эталонная базовая миграция \`alembic/versions/0001_initial_schema.py\` для 25 таблиц.
- Реализован безопасный откат (\`downgrade\`), корректно сбрасывающий внешние ключи (включая циклический \`fk_users_active_workspace\`).

### 2.6 Повышение надежности CI/CD и автодеплоя
- Исправлена инициализация \`postgres_state\` в \`scripts/backup_db.sh\`.
- Пайплайн \`.github/workflows/deploy.yml\` гарантирует предварительную синхронизацию кода на сервере до вызова скриптов деплоя.
- Автомигратор \`services/database.py\` автоматически накатывает недостающие колонки (\`admin_chat_id\`, \`updated_at\`) в существующие таблицы на бою.

---

## 📊 3. Результаты и верификация

| Показатель | До изменений | После изменений |
| :--- | :--- | :--- |
| **СУБД** | Гибрид (SQLite / Postgres) | Чистый PostgreSQL 16+ |
| **Хранилище JSON** | Текстовый \`JSON\` | Бинарный \`JSONB\` с индексацией |
| **Временные зоны** | Смешанные datetime | Строгий \`TIMESTAMPTZ\` (UTC) |
| **Модель владения** | Двойная (\`owner_id\` + \`workspace_id\`) | Чистая Multi-tenant (\`workspace_id\` + \`owner_user_id\`) |
| **Миграции** | Ручной \`inspect()\` | Декларативный Alembic + Auto-sync |
| **CI / Тесты** | 197 тестов | ✅ **197/197 тестов успешно (100%)** |
| **Боевой деплой** | Ручные фиксы | ✅ **Автоматический Zero-Downtime Deploy** |

---

*Buyerly Engineering Team — 2026*
