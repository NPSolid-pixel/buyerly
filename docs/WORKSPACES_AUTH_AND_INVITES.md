# Воркспейсы, Авторизация, Инвайты и Сервис Resend

В этом документе детально описана архитектура пользователей, мульти-воркспейсов, системы приглашений (Invites), онбординга в стиле Attio CRM и интеграции почтового сервиса Resend.

---

## 1. Обзор архитектуры

Платформа Buyerly построена по принципу **Workspace-First**:
- Каждый пользователь (`User` / `User`) входит в систему через один из каналов аутентификации (Email OTP, Email + пароль или Telegram Mini App).
- Все рабочие сущности (Meta-кабинеты, пресеты автоправил, группы кабинетов, журнал аудита и сводки) изолированы внутри конкретного рабочего пространства (`Workspace`).
- Доступ пользователя к ресурсам воркспейса определяется его членством (`WorkspaceMember`) и назначенной ролью (`owner`, `admin`, `buyer`, `viewer`).

```mermaid
flowchart TD
    subgraph Auth["Каналы авторизации"]
        OTP["Email + 6-значный OTP (Resend)"]
        PWD["Email / Логин + Пароль"]
        TMA["Telegram Mini App (initData)"]
    end

    subgraph User["Пользователь и Профиль"]
        TU["User (User)"]
        Prof["Имя, Фамилия, Email, Аватар"]
        Onb["Онбординг (personal_details -> workspace -> invites)"]
    end

    subgraph Workspaces["Мульти-воркспейсы"]
        WS["Workspace (name, slug, logo, badge)"]
        WM["WorkspaceMember (role: owner/admin/buyer/viewer)"]
        WI["WorkspaceInvite (token, email, role, limits)"]
    end

    subgraph Resources["Ресурсы воркспейса (Строгая изоляция)"]
        ACC["Рекламные кабинеты Meta"]
        RUL["Автоправила и Пресеты"]
        GRP["Группы кабинетов"]
        AUD["Журнал аудита и Undo"]
    end

    Auth --> TU
    TU --> Prof
    TU --> Onb
    TU --> WM
    WM --> WS
    WI -. Принятие инвайта .-> WM
    WS --> ACC
    WS --> RUL
    WS --> GRP
    WS --> AUD
```

---

## 2. Модели данных и ER-диаграмма

### 2.1. Диаграмма сущностей (Database Schema)

```mermaid
erDiagram
    TELEGRAM_USERS ||--o{ WORKSPACE_MEMBERS : "состоит в"
    TELEGRAM_USERS ||--o{ WORKSPACE_INVITES : "создал инвайт"
    TELEGRAM_USERS ||--o{ WORKSPACES : "владеет (owner_user_id)"
    TELEGRAM_USERS ||--o| WORKSPACES : "активный воркспейс (active_workspace_id)"
    WORKSPACES ||--o{ WORKSPACE_MEMBERS : "содержит участников"
    WORKSPACES ||--o{ WORKSPACE_INVITES : "активные приглашения"
    WORKSPACES ||--o{ ACCOUNTS : "содержит кабинеты"
    WORKSPACES ||--o{ RULE_PRESETS : "содержит правила"
    WORKSPACES ||--o{ ACCOUNT_GROUPS : "содержит группы"
    WORKSPACES ||--o{ AUDIT_EVENTS : "содержит аудит"

    TELEGRAM_USERS {
        int id PK
        string telegram_id UK "nullable"
        string username UK
        string full_name
        string first_name
        string last_name
        string email "nullable, indexed"
        string avatar_url
        string onboarding_step "personal_details, workspace, invites, completed"
        boolean onboarding_completed
        string password_hash
        string auth_token UK "nullable"
        string role "admin / buyer"
        boolean is_approved
        int active_workspace_id FK "nullable"
        datetime created_at
    }

    WORKSPACES {
        int id PK
        string name
        string slug UK "indexed (e.g. buyerly, media-team)"
        string badge_text "символ или буква бейджа"
        string badge_color "HEX-код цвета (#F5A300)"
        string logo_url "URL логотипа компании"
        int owner_user_id FK "User.id"
        datetime created_at
        datetime updated_at
    }

    WORKSPACE_MEMBERS {
        int id PK
        int workspace_id FK "UniqueConstraint(workspace_id, user_id)"
        int user_id FK
        string role "owner, admin, buyer, viewer"
        datetime joined_at
    }

    WORKSPACE_INVITES {
        int id PK
        int workspace_id FK
        string token UK "inv_... (URL-safe crypto token)"
        string email "nullable (для целевых инвайтов)"
        string role "admin, buyer, viewer"
        int inviter_user_id FK
        string status "pending, accepted, revoked, expired"
        int max_uses "1 = разовый, 0 = безлимит"
        int used_count
        datetime expires_at "nullable (UTC)"
        datetime created_at
        datetime updated_at
    }

    EMAIL_VERIFICATION_CODES {
        int id PK
        string email "indexed"
        string code "6-значный OTP код"
        datetime expires_at "UTC (15 мин)"
        boolean is_used
        int failed_attempts "защита от брутфорса (макс 5)"
        datetime created_at
    }
```

---

## 3. Ролевая модель (RBAC)

Внутри каждого воркспейса действует гранулярное разделение прав:

| Действие / Возможность | Owner (Владелец) | Admin (Админ) | Buyer (Байер) | Viewer (Наблюдатель) |
|---|:---:|:---:|:---:|:---:|
| Просмотр аналитики, сводок и KPI | ✅ | ✅ | ✅ | ✅ |
| Просмотр списка кабинетов и групп | ✅ | ✅ | ✅ | ✅ |
| Добавление и удаление кабинетов Meta | ✅ | ✅ | ✅ | ❌ |
| Создание, редактирование и запуск правил | ✅ | ✅ | ✅ | ❌ |
| Создание групп кабинетов | ✅ | ✅ | ✅ | ❌ |
| Отмена действий автоматики (Undo) | ✅ | ✅ | ✅ | ❌ |
| Создание инвайтов в команду | ✅ | ✅ | ❌ | ❌ |
| Управление ролями участников | ✅ | ✅ (кроме Admin/Owner) | ❌ | ❌ |
| Исключение участников из воркспейса | ✅ | ✅ (кроме Admin/Owner) | ❌ | ❌ |
| Настройки воркспейса (имя, лого, слаг) | ✅ | ✅ | ❌ | ❌ |
| Передача прав владения (`transfer-ownership`) | ✅ | ❌ | ❌ | ❌ |
| Удаление воркспейса | ✅ | ❌ | ❌ | ❌ |

---

## 4. Аутентификация и безопасность сессий

### 4.1. Dual-Channel Authentication (Почта, Пароль, OTP, Telegram)

1. **Email Passwordless OTP (Одноразовый код)**:
   - Пользователь вводит свой рабочий Email на странице `/auth/sign-in`.
   - Если пароль не указан, клиент вызывает `POST /api/auth/request-temporary-password`.
   - Генерируется 6-значный криптографически стойкий код (`secrets.randbelow(900000) + 100000`), сохраняемый в `EmailVerificationCode` со сроком жизни 15 минут.
   - Сервис Resend отправляет брендированное HTML-письмо с кодом.
   - Пользователь переходит на `/auth/temporary-password` и вводит полученный код.
   - Проверка выполняется через безопасное константное сравнение (`hmac.compare_digest`).
   - При 5 неверных попытках код навсегда аннулируется для защиты от перебора.
   - При успешном вводе код помечается `is_used = True`, а сервер создаёт ограниченную по времени web-сессию.

2. **Email / Логин + Пароль**:
   - При вводе сохранённого пароля происходит проверка через `bcrypt.checkpw`.
   - Пароли защищены адаптивным алгоритмом хэширования с авто-рехэшингом при устаревании параметров.

3. **Telegram Mini App (TMA)**:
   - При открытии веб-приложения внутри Telegram проверяется криптографическая подпись `initData` с использованием токена бота.
   - Пользователь бесшовно связывается с записью `User` по `telegram_id`.

### 4.2. Безопасность и Rate Limiting
- **Web-сессии**: Browser secret хранится только в `Secure + HttpOnly + SameSite` cookie, в PostgreSQL остаётся SHA-256 hash. Сессия действует не более 24 часов, токен регулярно ротируется, а `last_seen_at` и metadata устройства позволяют увидеть и отозвать отдельный вход.
- **CSRF**: Все изменяющие запросы cookie-сессии требуют совпадающий double-submit токен из cookie `buyerly_csrf` и заголовка `X-CSRF-Token`.
- **Миграция**: Старые `User.auth_token` переносятся в короткоживущие hashed sessions и очищаются; frontend удаляет legacy bearer из `localStorage` после первого успешного запроса.
- **Защита от DoS и Brute-Force**: Все эндпоинты входа, запроса OTP, проверки слагов и инвайтов защищены sliding-window rate-лимитером (`core/rate_limit.py`).
- **Скрытие токенов**: Токены Meta шифруются алгоритмом Fernet; session secrets хранятся только как односторонние хэши и не возвращаются в JSON API.

---

## 5. Онбординг в стиле Attio CRM

Онбординг сопровождает нового пользователя от регистрации до создания первого воркспейса и приглашения команды:

```
[ 1. Sign In ] ──► [ 2. Temporary Code ] ──► [ 3. Personal Details ]
 /auth/sign-in       /auth/temporary-password   /welcome/personal-details
                                                         │
                                                         ▼
[ 6. Workspace Home ] ◄── [ 5. Invite Team ] ◄── [ 4. Workspace Details ]
 /<slug>/home              /welcome/invite-team     /welcome/workspace-details
```

### 5.1. Шаги онбординга:
1. **`/auth/sign-in`** — Ввод рабочего email и пароля/запрос OTP.
2. **`/auth/temporary-password`** — Ввод 6-значного проверочного кода, таймер обратного отсчёта и кнопка повторной отправки («Resend code»).
3. **`/welcome/personal-details`**:
   - Загрузка пользовательского аватара (`POST /api/onboarding/avatar`, хранение в `webapp/uploads/avatars/`).
   - Ввод имени и фамилии (обновляет `first_name`, `last_name`, `full_name`).
   - Переключатель подписки на продуктовые обновления.
4. **`/welcome/workspace-details` (или `/workspace/new`)**:
   - Ввод названия компании/команды.
   - Автоматическая генерация URL-слага (например, `media-team` → `buyerly.app/media-team`).
   - Живая проверка доступности слага в реальном времени (`GET /api/onboarding/check-slug`) с защитой зарезервированных системных путей (`api`, `auth`, `admin`, `settings` и др.).
   - Загрузка логотипа компании (`POST /api/onboarding/workspace/logo`, сохранение в `webapp/uploads/workspaces/`).
   - **Интерактивный Live Preview**: правая колонка в реальном времени визуализирует сайдбар и карточку создаваемого воркспейса.
5. **`/welcome/invite-team`**:
   - Массовое добавление коллег (`POST /api/onboarding/invites`) с назначением ролей (`Member`, `Admin`, `Viewer`).
   - Автоматическая отправка писем с приглашениями через Resend.
   - Быстрое копирование общей инвайт-ссылки.
   - Кнопка быстрого пропуска («Skip for now»).

---

## 6. Система инвайтов (Invitations)

### 6.1. Типы приглашений
1. **Персональное приглашение по Email**:
   - Создаётся администратором с указанием email получателя.
   - Сервис Resend автоматически отправляет персонализированное письмо со ссылкой вида `https://buyerly.app/invite/inv_xxxxxxxxxxxx`.
   - Инвайт привязан к конкретному email: другой пользователь не сможет активировать ссылку.
2. **Публичная ссылка для команды**:
   - Создаётся без указания email с параметром `max_uses: 0` (многоразовая) или `max_uses: N`.
   - Позволяет быстро подключить несколько байеров в один клик.

### 6.2. Жизненный цикл инвайта
- `pending` — приглашение создано и ожидает активации.
- `accepted` — приглашение принято (или исчерпан лимит использований `max_uses`).
- `revoked` — приглашение отозвано администратором (`DELETE /api/workspaces/{id}/invites/{invite_id}`).
- `expired` — срок действия приглашения истёк (по умолчанию 7 дней).

### 6.3. Экран принятия инвайта (`/invite/<token>`)
- При переходе по ссылке вызывается публичный эндпоинт `GET /api/invites/{token}`.
- Отображается карточка воркспейса, логотип, автор приглашения и назначенная роль.
- По кнопке «Accept & Join» вызывается `POST /api/invites/{token}/accept`, пользователь добавляется в `WorkspaceMember`, и активный воркспейс переключается автоматически.

---

## 7. Сервис транзакционных рассылок (Resend)

Отправка всех служебных писем реализована в модуле [`core/email.py`](file:///home/hiurano/Projects/ai-mediabuyer/core/email.py) через официальный REST API сервиса **Resend**.

### 7.1. Конфигурация переменных окружения

| Переменная | Обязательна | Назначение | Пример значения |
|---|:---:|---|---|
| `RESEND_API_KEY` | Да (для prod) | API-ключ сервиса Resend | `re_123456789_abcdef...` |
| `EMAIL_FROM` | Нет | Имя и обратный адрес отправителя | `Buyerly <team@buyerly.app>` |
| `WEBAPP_URL` | Да | Базовый публичный URL платформы | `https://buyerly.app` |

### 7.2. Реализованные шаблоны писем

1. **OTP Verification Code Email (`send_otp_verification_email`)**:
   - Тема: `Your Buyerly verification code is 123456`
   - Адаптивный HTML-шаблон с крупным отображением кода, моноширинным шрифтом, информацией о сроке действия (15 минут) и предупреждением о безопасности.

2. **Workspace Invitation Email (`send_workspace_invitation_email`)**:
   - Тема: `{Inviter Name} invited you to join {Workspace Name} on Buyerly`
   - Адаптивный HTML-шаблон с названием воркспейса, назначенной ролью, прямой ссылкой/кнопкой «Join Workspace» (`https://buyerly.app/invite/{token}`) и инструкциями.

### 7.3. Локальный режим разработки (Dev Fallback)
Если переменная `RESEND_API_KEY` не задана в `.env`, модуль `core/email.py` автоматически переключается в локальный режим:
- Письма не отправляются наружу, исключения не выбрасываются.
- Тело письма, адрес получателя и OTP-код выводятся в стандартный лог бэкенда (`logger.info("[DEV EMAIL] ...")`), что позволяет разработчикам тестировать вход и инвайты без подключения к интернету.

---

## 8. Справка по структуре файлов

```text
api/
├── routers/
│   ├── auth.py          # /api/auth/* — OTP, логин, пароли, профиль
│   ├── workspaces.py    # /api/workspaces/* — создание, переключение, настройки
│   ├── members.py       # /api/workspaces/{id}/members, /invites/*
│   └── onboarding.py    # /api/onboarding/* — шаги, аватары, лого, слоги
├── schemas/
│   ├── auth.py          # Pydantic-схемы авторизации и профиля
│   ├── workspaces.py    # Схемы воркспейсов
│   ├── members.py       # Схемы участников и инвайтов
│   └── onboarding.py    # Схемы шагов онбординга
core/
├── email.py             # Клиент Resend REST API и HTML-шаблоны писем
├── rate_limit.py        # Sliding-window защита от перебора и DoS
database/
├── models.py            # SQLAlchemy модели (User, Workspace, WorkspaceMember, WorkspaceInvite, EmailVerificationCode)
webapp/
├── js/app.js            # Фронтенд: Onboarding Flow, Workspace Switcher, Members Modal, Live Preview
├── index.html           # Разметка экранов Sign In, Verify, Personal Details, Workspace Details
└── uploads/             # Локальное хранилище медиа (аватары, логотипы)
```
