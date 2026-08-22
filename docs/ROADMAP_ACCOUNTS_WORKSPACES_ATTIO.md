# 🗺 Roadmap: Система аккаунтов, воркспейсов и онбординга (Attio Style)

> **Цель:** Полная модернизация системы пользователей, авторизации, воркспейсов, приглашений в команду (Team Invites) и прав доступа в строгом соответствии с UX/UI эталоном **Attio CRM** ([`app.attio-structure-login-workspaces`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces)).

---

## 📐 Целевая архитектура и концепция

```
[ 1. Sign In / Auth ] ────► [ 2. Temporary Code ] ───► [ 3. Personal Details ]
 (Email / TG / Pass)         (Код подтверждения)         (Аватар, Имя, Фамилия)
                                                                  │
                                                                  ▼
[ 6. Workspace Home ] ◄──── [ 5. Invite Team ] ◄────── [ 4. Workspace Details ]
 (Сайдбар, Attio Switcher,   (Инвайты, роли: Admin,     (Лого, Название, Авто-слаг,
  кабинеты, списки)           Member, Viewer + ссылка)    Страна/Валюта + Live Preview)
```

### Ключевые принципы:
1. **Workspace-First (Воркспейс как главный контейнер):**
   * Все ресурсы (Рекламные кабинеты Meta, подключения Meta OAuth, группы кабинетов, пресеты правил, аудит) строго принадлежат **`Workspace`** (`workspace_id`).
   * Пользователи получают доступ к ресурсам через членство в воркспейсе (`WorkspaceMember`).
2. **Гранулярный RBAC (Роли внутри воркспейса):**
   * `Owner` (Владелец) — полное управление, включая удаление и передачу воркспейса.
   * `Admin` (Администратор) — управление участниками, настройками, интеграциями и правилами.
   * `Buyer / Member` (Байер) — добавление/управление кабинетами, запуск правил, просмотр сводок.
   * `Viewer` (Наблюдатель) — только чтение аналитики и дашбордов без права изменения настроек.
3. **Омниканальная авторизация (Dual-Channel Auth):**
   * Веб: вход по почте/паролю, временным кодам, Google/OAuth.
   * Telegram: бесшовный вход через Mini App (`initData`) и уведомления бота.

---

## 📋 Пошаговый план работ (Атомарные задачи)

---

### 🔹 ЭТАП 1: Бэкенд и База данных — Инвайты, участники и строгий скоупинг

#### Задача 1.1: Модель `WorkspaceInvite` и схема БД ✅
- [x] Создать модель `WorkspaceInvite` в [`database/models.py`](file:///home/hiurano/Projects/ai-mediabuyer/database/models.py):
  - `id`, `workspace_id` (FK), `email` (опционально), `role` (`admin`, `buyer`, `viewer`),
  - `token` (уникальный безопасный URL-токен), `inviter_user_id` (FK на `User`),
  - `max_uses` (лимит использований, по дефолту 1 или безлимит для ссылок),
  - `used_count`, `expires_at`, `created_at`.
- [x] Добавить миграцию / безопасную инициализацию таблицы в базе данных.

#### Задача 1.2: API управления участниками воркспейса (Members API) ✅
- [x] Реализовать эндпоинты в API:
  - `GET /api/v1/workspaces/{id}/members` — список участников с их ролями, аватарами, датой вступления.
  - `PATCH /api/v1/workspaces/{id}/members/{user_id}` — изменение роли участника (доступно только `owner` / `admin`).
  - `DELETE /api/v1/workspaces/{id}/members/{user_id}` — удаление участника из воркспейса.
  - `POST /api/v1/workspaces/{id}/leave` — выход пользователя из воркспейса по собственному желанию.
  - `POST /api/v1/workspaces/{id}/transfer-ownership` — передача владения воркспейсом.

#### Задача 1.3: API приглашений и ссылок (Invites API) ✅
- [x] Реализовать эндпоинты:
  - `POST /api/v1/workspaces/{id}/invites` — создание персонализированного инвайта (по email/юзернейму) или публичной инвайт-ссылки с ролью.
  - `GET /api/v1/workspaces/{id}/invites` — список активных приглашений воркспейса.
  - `DELETE /api/v1/workspaces/{id}/invites/{invite_id}` — отзыв приглашения.
  - `GET /api/v1/invites/{token}` — публичная проверка токена (возвращает имя воркспейса, логотип, автора приглашения и назначенную роль).
  - `POST /api/v1/invites/{token}/accept` — принятие инвайта авторизованным пользователем и автоматическое добавление в `WorkspaceMember`.

#### Задача 1.4: Строгая изоляция ресурсов (Workspace Resource Scoping) ✅
- [x] Проверить и перевести запросы кабинетов ([`get_user_accounts`](file:///home/hiurano/Projects/ai-mediabuyer/api/routes.py#L513)), пресетов, групп и логов аудита на строгий скоупинг `Account.workspace_id == current_workspace.id`.
- [x] Реализовать проверку прав на уровне эндпоинтов (запрет роли `viewer` на мутации кабинетов и правил).

---

### 🔹 ЭТАП 2: Бэкенд — Профиль пользователя и шаги онбординга

#### Задача 2.1: API персональных данных онбординга ✅
- [x] Расширить профиль `User` (алиас `User`) полями `first_name`, `last_name`, `email`, `avatar_url`, `onboarding_step`, `onboarding_completed`.
- [x] Реализовать эндпоинты:
  - `GET /api/v1/onboarding/status` — статус онбординга, текущий шаг и профиль.
  - `POST /api/v1/onboarding/personal-details` — сохранение имени, фамилии, email и автоперевод на шаг воркспейса.
  - `POST /api/v1/onboarding/avatar` — загрузка аватара пользователя в локальное хранилище медиа (`webapp/uploads/avatars/`).
  - `DELETE /api/v1/onboarding/avatar` — сброс аватара.

#### Задача 2.2: API создания воркспейса с логотипом и проверкой слага ✅
- [x] Расширить модель `Workspace` полем `logo_url`.
- [x] Реализовать эндпоинты:
  - `GET /api/v1/onboarding/check-slug` — проверка доступности слага воркспейса в реальном времени с валидацией зарезервированных имен.
  - `POST /api/v1/onboarding/workspace` — создание воркспейса, привязка пользователя овнером в `WorkspaceMember`, установка `active_workspace_id`.
  - `POST /api/v1/onboarding/workspace/logo` — загрузка кастомного логотипа компании (`webapp/uploads/workspaces/`).

#### Задача 2.3: API массовых инвайтов на онбординге ✅
- [x] Реализовать эндпоинты:
  - `POST /api/v1/onboarding/invites` — приём списка email + ролей (`[{ email: "...", role: "buyer" }]`), генерация инвайтов и фиксация завершения онбординга.
  - `POST /api/v1/onboarding/skip` — быстрый пропуск шага ("Skip for now") и фиксация `onboarding_completed = True`.

---

### 🔹 ЭТАП 3: Фронтенд — Полный Onboarding Flow в стиле Attio

#### Задача 3.1: Дизайн-система и базовые лейауты онбординга ✅
- [x] Создать CSS-стили для страниц онбординга по эталону Attio:
  - Центрированный минималистичный контейнер.
  - Точная карточка и поля ввода по спецификации Attio.
  - Аккуратная типографика (шрифт Inter, правильные отступы, micro-interactions).
  - Стили для полей ввода с иконками, аватара, тогл-переключателей и кнопок.

#### Задача 3.2: Экран 1 — `Sign In` ([`photo_1`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_1_2026-08-22_18-02-42.jpg)) ✅
- [x] Минималистичный экран входа:
  - Логотип Buyerly по центру.
  - Заголовок `Sign in`.
  - Поле `Enter your work email address` + кнопка `Continue`.
  - Вход по паролю / быстрому временному коду.
  - Футер со ссылками на политики и поддержку.

#### Задача 3.3: Экран 2 — `Temporary Password / Verification` ([`photo_2`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_2_2026-08-22_18-02-42.jpg)) ✅
- [x] Экран подтверждения:
  - Заголовок `Check your inbox!`.
  - Поле с введенным email и кнопкой изменения адреса.
  - Поле ввода одноразового кода или временного пароля.
  - Кнопка `Continue` с валидацией через API.

#### Задача 3.4: Экран 3 — `Personal Details` ([`photo_3`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_3_2026-08-22_18-02-42.jpg)) ✅
- [x] Экран персональных данных:
  - Заголовок `Let's get to know you`.
  - Аватарка (круглый блок 64×64px с кнопками `Upload image` и `Remove`, загрузка и удаление через API).
  - Инпуты `First name` и `Last name`.
  - Поле `Email` (read-only).
  - Тоггл подписки на обновления.
  - Кнопка `Continue` и сохранение через `POST /api/onboarding/personal-details`.

#### Задача 3.5: Экран 4 — `Workspace Details` с Live Preview ([`photo_4`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_4_2026-08-22_18-02-42.jpg), [`photo_5`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_5_2026-08-22_18-02-42.jpg))
- [ ] Двухколоночный экран создания воркспейса:
  - Левая колонка:
    - Кнопка возврата `←`.
    - Заголовок `Create your workspace`.
    - Логотип воркспейса (скругленный квадрат с динамическим инициалом `B` или загрузкой картинки).
    - Поле `Company name` (название компании/команды).
    - Поле `Workspace handle` (автогенерация слага `buyerly.app/<slug>`).
    - Селектор страны/валюты/таймзоны.
    - Кнопка `Continue`.
  - **Правая колонка (Реактивный Live Preview):**
    - В реальном времени отображает шапку сайдбара и рабочего пространства с введённым названием и логотипом.

#### Задача 3.6: Экран 5 — `Invite Team` ([`photo_6`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_6_2026-08-22_18-02-42.jpg))
- [ ] Двухколоночный экран приглашения коллег:
  - Левая колонка:
    - Заголовок `Collaborate with your team`.
    - Динамический список полей: Поле Email/TG + Выпадающий список роли (`Member ▾`, `Admin ▾`, `Viewer ▾`).
    - Кнопка `+ Add more` (добавление новых строк).
    - Кнопка `Copy invite link` (копирование общей ссылки с тостом об успехе).
    - Кнопка `Send invites` (основная) и `Skip for now` (пропустить).
  - Правая колонка: превью командной работы.

---

### 🔹 ЭТАП 4: Фронтенд — Workspace Switcher и Настройки участников ✅

#### Задача 4.1: Выпадающий селектор воркспейсов в Сайдбаре ([`photo_7`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/photo_7_2026-08-22_18-02-42.jpg), [`bar-workspaces.html`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/invite-4/bar-workspaces.html)) ✅
- [x] Переработать шапку сайдбара и выпадающее меню:
  - Компактный бейдж с логотипом/инициалом + Название воркспейса + шеврон `▾` + кнопка скрытия сайдбара.
  - Выпадающее меню:
    1. Список доступных воркспейсов пользователя с бейджами и синей галочкой `✓` у активного.
    2. Разделитель.
    3. Пункт `+ New workspace` (открывает мастер создания).
    4. Пункт `Account settings` (настройки личного профиля).
    5. Пункт `Workspace settings` (открывает вкладку настроек воркспейса).
    6. Пункт `Invite team members` (открывает быстрый модальный диалог инвайта).
    7. Разделитель.
    8. Пункт `Sign out` (безопасный выход из системы).

#### Задача 4.2: Диалоговое окно быстрого инвайта (`Invite team members Popup`) ([`invite-members-popup.html`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/invite-4/invite-members-popup.html)) ✅
- [x] Создать диалог быстрого приглашения коллег:
  - Заголовок `Invite team members` с вкладкой и крестиком закрытия `✕`.
  - Поле `Send Invite to ...` (placeholder: `example@email.com`).
  - Селектор роли `Invite as`: `Member (can edit)`, `Admin (full access)`, `Viewer (read only)`.
  - Блок `Copy invite link` (копирование общей ссылки с тостом).
  - Кнопка `Send Invites` с поддержкой хоткея `Ctrl + Enter`.

#### Задача 4.3: Вкладка "Members & Roles" в настройках воркспейса ✅
- [x] Создать полноценный интерфейс управления командой:
  - Вкладка `Members & Roles` в модальном окне настроек воркспейса.
  - Таблица участников: Аватар, Имя, Email, Селектор роли (`Owner`, `Admin`, `Member`, `Viewer`), Дата добавления, кнопка Удалить `✕`.
  - Поиск и фильтрация по участникам.
  - Секция активных инвайтов с кнопкой отзыва.
  - Изменение роли через `PATCH /api/workspaces/{id}/members/{user_id}` и удаление через `DELETE /api/workspaces/{id}/members/{user_id}`.

#### Задача 4.4: Страница принятия приглашения (`/invite/<token>`) ([`app.attio.com-auth-join-buyerly-app.html`](file:///home/hiurano/Projects/ai-mediabuyer/app.attio-structure-login-workspaces/app.attio.com-auth-join-buyerly-app.html)) ✅
- [x] Отдельный экран для пользователя, перешедшего по инвайт-ссылке:
  - Карточка с приглашением: логотип Buyerly, заголовок `Almost there!`, карточка воркспейса с бейджем, названием и назначенной ролью.
  - Кнопка `Accept & Join` (вызывает `POST /api/invites/{token}/accept` и автоматически переключает пользователя в этот воркспейс).
  - Кнопка `Not now` (отклоняет / возвращается назад).

---

### 🔹 ЭТАП 5: Рефакторинг и модульность (Технический долг) ✅

#### Задача 5.1: Модуляризация монолита `api/routes.py` ✅
- [x] Разбить файл 3300+ строк на пакет `api/routers/`:
  - `api/routers/auth.py` — авторизация, профиль, пароли, TMA.
  - `api/routers/workspaces.py` — создание, переключение, настройки воркспейсов.
  - `api/routers/members.py` — участники, роли, инвайты.
  - `api/routers/onboarding.py` — персональные данные, воркспейс, инвайты онбординга.
  - `api/routers/accounts.py` — рекламные кабинеты Meta, группы, статусы.
  - `api/routers/rules.py` — правила, пресеты, группы правил.
  - `api/routers/summary.py` — сводка аналитики, кэш, настройки колонок.
  - `api/routers/settings.py` — интервалы мониторинга и глобальная автоматика.
  - `api/routers/audit.py` — журнал аудита и Undo.
  - `api/routers/adsets.py` — остановленные адсеты и реактивация.
- [x] Вынести Pydantic-схемы в `api/schemas/`.
- [x] Вынести общие зависимости и вспомогательные функции в `api/deps.py`.
- [x] Обеспечить слой обратной совместимости в `api/routes.py`.

---

## 🎯 Порядок выполнения по чатам (Сессиям)

Каждый блок выполняется **в отдельном изолированном чате и отдельной ветке** от чистого `main` согласно [`AGENTS.md`](file:///home/hiurano/Projects/ai-mediabuyer/AGENTS.md):

| Сессия | Название задачи | Что будет сделано |
|---|---|---|
| **Сессия 1** | **Бэкенд: Инвайты и Участники (Задачи 1.1 – 1.4)** | Модель `WorkspaceInvite`, эндпоинты `/members`, `/invites`, `/invites/accept`, скоупинг. |
| **Сессия 2** | **Бэкенд: Профиль и Онбординг (Задачи 2.1 – 2.3)** | Эндпоинты сохранения персональных данных, воркспейса с логотипом и массовых инвайтов. |
| **Сессия 3** | **Фронтенд: Дизайн-система и Базовый Onboarding (Задачи 3.1 – 3.4)** | Стили Attio, экраны `Sign In`, `Temporary Password`, `Personal Details` с 2-колоночным макетом. |
| **Сессия 4** | **Фронтенд: Workspace Details с Live Preview и Invite Team (Задачи 3.5 – 3.6)** | Интерактивный экран создания воркспейса с живым превью справа и экран инвайтов команды. |
| **Сессия 5** | **Фронтенд: Workspace Switcher и Members Settings (Задачи 4.1 – 4.3)** | Сайдбар-свитчер Attio, управление участниками в настройках, страница `/invite/<token>`. |
| **Сессия 6** | **Рефакторинг `api/routes.py` (Задача 5.1)** | Разделение монолитного роутера на модули. |
