# Buyerly UX baseline

Дата baseline: 29 августа 2026 года
Задача: BL-100 / ClickUp `86eyr6074`
Следующие этапы: BL-101 `86eyr6073`, BL-102 `86eyrx9bz`

## 1. Резюме

Buyerly уже содержит функционально богатый desktop/mobile интерфейс, но сейчас это набор нескольких визуальных систем и двух языков. Главная проблема не в отсутствии экранов, а в отсутствии единого продуктового контракта: действие, предупреждение и бренд используют один amber; базовый текст начинается с 13px и во многих местах опускается до 10–12px; одинаковые сущности называются по-разному; часть элементов выглядит интерактивной, но не выполняет действие.

Кодовая инвентаризация выявила:

- 7 authenticated разделов и отдельные public/onboarding flows;
- 22 modal overlays, 58 button instances, 17 KPI cards и несколько несовместимых table/card patterns;
- 195 inline-style declarations в `index.html` и ещё 148 в generated markup `app.js`;
- 746 прямых color expressions в CSS/HTML/JS;
- сотни объявлений текста меньше 14px;
- параллельные legacy `--tg-*`, Attio-like и semantic переменные.

Поэтому BL-101 должен начать с токенов и shared primitives, а BL-102 — с jobs-based навигации и одного языка. Переписывание всего интерфейса за один релиз создаст высокий риск; правильная миграция начинается с трёх пилотов: **Today**, **Automations**, **Connections**.

## 2. Карта интерфейса

| Будущее имя | Текущий route / selector | Основная задача пользователя | Основные состояния |
|---|---|---|---|
| Sign in | `/sign-in`, `#onboardingSignInStep` | Войти паролем или запросить временный пароль | idle, validation, submitting, wrong credentials, access denied, rate limit |
| Temporary password | `/auth/temporary-password`, `#onboardingVerifyStep` | Подтвердить владение email | idle, resend cooldown, invalid/expired, locked, success |
| Personal details | `/welcome/personal-details`, `#onboardingPersonalStep` | Завершить профиль | empty, avatar upload, long names, invalid upload, success |
| Workspace setup | create-workspace screen | Создать первое рабочее пространство | empty, slug preview, validation, upload, error, success |
| Today | `/home`, `#tab-home` | Увидеть приоритеты и быстро перейти к действию | сейчас только статичный AI composer |
| Connections | `/facebook-accounts`, `#tab-fb_accounts` | Подключить и проверить Facebook profile / Business Manager | loading, empty, populated, token health, reconnect, error, partial import |
| Ad accounts | `/accounts`, `#tab-accounts` | Найти кабинет, понять состояние, выполнить bulk action | loading, empty, populated, filters, long table, selection, partial failure, permission denied |
| Automations | `/rules`, `#tab-rules` | Создать, понять и управлять правилами | loading, empty, grouped, selected, detail, validation, partial mutation failure |
| Efficiency | `/summary`, `#tab-summary` | Понять результат и качество данных | loading, no snapshot, populated, partial Meta data, currency split, filtered empty, error |
| Action History | `/logs`, `#tab-logs` | Проверить действие, причину и возможность undo | loading, empty, populated, stopped actions, filters, pagination, undo allowed/denied/error |
| Settings | `/settings`, `#tab-settings` | Управлять профилем, безопасностью, автоматикой и SLO | loading, role-restricted, validation, saving, success, API error |
| Meta connect invite | `/connect/meta/{token}` | Подключить профиль по одноразовой ссылке | loading, valid, expired/used/revoked, OAuth error, success |
| Legal | `/privacy`, `/terms`, `/data-deletion` | Прочитать обязательные документы | content, long content, broken-link check |

### Source snapshot contract

Для каждой строки source snapshot определяется не сохранённой копией сторонней страницы, а воспроизводимой связкой:

1. точный production route;
2. стабильный DOM selector;
3. функция загрузки/рендера в `webapp/js/app.js`;
4. состояние API, которое воспроизводит UI;
5. desktop viewport `>= 1280px` и mobile viewport `390×844`.

Public production `https://buyerly.app/sign-in` визуально проверен 29 августа 2026 года на desktop и mobile `390×844`. Mobile layout не имеет горизонтального overflow и сохраняет доступность формы, но выявлены четыре baseline-дефекта: wordmark почти исчезает на белом фоне, primary CTA использует несистемный синий, весь flow остаётся на английском, а поле принудительно требовало email-формат вместо уже поддерживаемого API username. Последний дефект вынесен в отдельный fix-релиз. Authenticated snapshot дополняется после входа в production; отсутствие сессии не меняет source contract выше.

## 3. Матрица meaningful states

| Состояние | Где обязательно проверять | Наблюдаемая проблема | Ожидаемый эффект после foundation |
|---|---|---|---|
| Loading | Connections, Ad accounts, Automations, Efficiency, History, Settings | разные spinner/текстовые шаблоны, скачки layout | единый Skeleton, сохранение геометрии, понятный label |
| Empty | Connections, Ad accounts, Automations, Efficiency, History | разные иллюстрации, размеры и CTA | единый EmptyState: причина, следующий шаг, один primary CTA |
| Populated | таблицы, KPI, rules groups, settings | высокая плотность, 10–13px текст, смешанные действия | base 14px, устойчивые toolbars, единые rows/cards |
| Error | auth, Meta, summary, rules, logs | toast и inline error конкурируют, технический текст | Alert с причиной, влиянием, retry и support path |
| Partial | Meta import, bulk mutations, summary coverage | частичный успех недостаточно отделён от полной ошибки | явный итог: выполнено / пропущено / ошибка |
| Long content | таблицы, identifiers, names, logs, legal | truncation и horizontal overflow не унифицированы | tooltip/copy для IDs, wrap для human text, sticky context |
| Permission denied | settings, mutations, undo, workspace admin | контроль может выглядеть доступным до API 403 | скрыть или disabled с объяснением роли |

## 4. Инвентаризация и проблемы

### Навигация и hierarchy

- Текущие top-level labels отражают внутренние сущности: `FB Аккаунты`, `Правила`, `Сводка`, `Логи`.
- `Главная` обещает overview, но показывает неработающий AI prompt.
- `Все кабинеты` находится во вложенной секции, хотя это частый операционный экран.
- Settings доступны через профиль и не представлены одинаково на desktop/mobile.
- Рекомендация BL-102: `Today`, `Efficiency`, `Automations`, `Action History`, `Connections`, `Settings`; Ad accounts становятся рабочим представлением внутри Connections/Efficiency, а не конкурирующим top-level понятием.

### Decorative и broken controls

- Home textarea `Ask anything…`, model selector `Auto`, attachment, voice и send визуально обещают AI workflow, но не имеют product action contract.
- Header `Справка и документация` не имеет связанного действия.
- В интерфейсе встречаются `Sort`, `Sort by`, `Calculate`, `Share`, `Add Account`, `More`, которые нарушают выбранный русский язык.
- Иконки `?`, буквы `R/F/L/O/U`, символ `¤` и emoji используются вместо одной icon/tooltip системы.

### Терминология и локализация

- Одновременно используются `Facebook аккаунт`, `Facebook Профиль`, `FB Аккаунты`, `BM`, `Бизнес-менеджеры`, `кабинет`, `аккаунт`, `act_ID`, `ad set`, `адсет`.
- Auth/onboarding почти полностью английский, authenticated shell преимущественно русский, метрики смешанные.
- Рекомендация: основной UI-язык — русский; неизменяемые Meta/API термины показываются как русское имя с каноническим сокращением в tooltip или secondary label.

### Visual system

- Base font `13px`; table, KPI, hints и controls часто используют `10–12.5px`, что ухудшает scanability и доступность.
- Amber `#F5A300` одновременно является brand action, active border и частью warning palette.
- Public sign-in добавляет отдельный синий primary action, а белый wordmark почти не различим на белом фоне.
- Direct colors и inline styles мешают управлять contrast, dark/accessible variants и миграцией.
- Есть несколько button/input/table/modal families: `btn-*`, `attio-*`, `settings-*`, record-specific и generated markup.
- Mono должен остаться только для ID, code, SHA и технических значений; сейчас он попадает в пользовательские labels и account metadata шире необходимого.

### Density и responsive

- Desktop tables функциональны, но headers, calculators, filters и bulk bars используют разные высоты и alignment.
- Mobile имеет отдельные cards/lists, однако top-level навигация отличается от desktop и не содержит Settings.
- Длинные списки имеют поиск в Summary/Logs, но Connections и часть account/rule selectors не используют единый search/filter/saved-view pattern.

## 5. Пилотные экраны BL-101

### Today

Почему: снимает самый опасный broken promise — декоративный AI prompt. Пилот проверяет page header, KPI/attention cards, EmptyState, Alert, Button и responsive layout.

### Automations

Почему: главный продуктовый workflow с высокой ценой ошибки. Пилот проверяет tabs/groups, table/cards, badges, modals, permission states и destructive actions.

### Connections

Почему: первый setup workflow и источник большинства operational failures. Пилот проверяет table, token health, empty/loading/error, primary action, drawer/modal и long identifiers.

Efficiency и Action History переходят на те же primitives после подтверждения пилотов; URL и API-контракты сохраняются.

## 6. Priorities

### P0 — до расширения продукта

- убрать или заменить неработающий AI composer;
- развести action amber и warning semantic color;
- ввести base 14px и устойчивую numeric typography;
- унифицировать loading/empty/error/partial states;
- не показывать запрещённые действия как доступные.

### P1 — в BL-101/BL-102

- единые tokens/primitives и запрет новых inline/direct colors;
- jobs-based IA и единый русский язык;
- общая терминология Meta entities;
- одинаковая desktop/mobile navigation model;
- единый search/filter/saved-view pattern для длинных списков.

### P2 — после foundation

- перенос оставшихся legacy families на primitives;
- удаление старых variables/selectors после доказанной миграции;
- визуальная регрессия для ключевых states.

## 7. Acceptance checklist

- [x] Карта public, onboarding и authenticated routes.
- [x] Loading, empty, populated, error, partial, long-content и permission states.
- [x] Инвентаризация nav, tables, cards, filters, modal patterns и tokens.
- [x] Broken/decorative controls и AI prompt зафиксированы.
- [x] Localization, terminology, hierarchy, density, contrast и responsive debt зафиксированы.
- [x] Для проблем указаны scenario и ожидаемый эффект.
- [x] Выбраны три пилотных экрана BL-101.
- [ ] Authenticated production snapshots подтверждены после входа владельца сессии.
