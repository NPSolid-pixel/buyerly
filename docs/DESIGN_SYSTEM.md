# Buyerly Design System

Version: Unified UI 2.2
Owner: Product / Frontend
ClickUp: BL-101 `86eyr6073`

## Principles

1. **Action is not warning.** Brand amber может использоваться в identity, primary action использует более тёмный доступный `--action-primary`, warning — отдельные foreground/background/border tokens.
2. **Readable by default.** Базовый UI-текст — 14px; 12px разрешён для secondary metadata, но не для основного действия или значения.
3. **Numbers scan, identifiers copy.** Числа используют tabular numerals; mono применяется только для ID, code, SHA и технических значений.
4. **State before decoration.** Loading, empty, partial, error, permission и success должны быть понятны без цвета.
5. **Progressive migration.** Новый компонент получает `ui-*` contract; legacy selector может сосуществовать до переноса всех consumers.
6. **One surface per job.** Карточка не используется как универсальный контейнер. Заголовок страницы живёт на canvas, метрики объединяются в один divided surface, а таблица получает только один внешний data-surface.
7. **Hierarchy before decoration.** Иерархию создают размер текста, интервалы и разделители. Тень используется только для самостоятельной surface, popover и dialog.
8. **Character without card salad.** Характер создают ambient background, типографический контраст, semantic accent и одна доминирующая поверхность — не карточки внутри карточек.

## Production architecture

- `webapp/css/styles.css` сохраняет legacy и domain-specific поведение;
- `webapp/css/ui-system.css` загружается последним и является единым production-контрактом геометрии, типографики, surfaces и responsive;
- все семь authenticated-разделов помечены `data-ui-pilot` и используют одну ширину `--ui-page-max` и gutter `--ui-page-gutter`;
- все 23 modal overlays и command palette получают `.ui-dialog`, `role="dialog"` и `aria-modal="true"` без изменения id и JavaScript handlers;
- production HTML не содержит presentation-specific inline styles: разрешены только шесть стартовых `display:none` для экранов, чья видимость переключается JavaScript; вычисляемые ширины таблиц и user-configured colors остаются в runtime markup;
- public legal pages используют ту же neutral/action palette через собственный маленький набор semantic tokens в `legal.css`.

## Tokens

| Group | Contract |
|---|---|
| Typography | `--font-sans`, `--font-mono`, `--font-size-xs/sm/md/lg/xl`, line heights |
| Spacing | `--space-1/2/3/4/6/8` = 4/8/12/16/24/32px |
| Controls | `--control-sm/md/lg` = 32/36/44px |
| Action | `--action-primary`, `--action-primary-hover`, `--action-primary-soft` |
| Warning | `--warning-fg`, `--warning-bg`, `--warning-border` |
| Focus | `--focus-ring`; всегда visible для keyboard focus |
| Elevation | `--elevation-card`, existing dropdown/modal/tooltip shadows |
| Layers | `--layer-sticky/popover/modal` |
| Motion | `--motion-fast`, `--motion-standard` |
| Page layout | `--ui-page-max`, `--ui-page-readable`, `--ui-page-gutter`, `--ui-page-top` |
| Surface | `--ui-canvas`, `--ui-surface`, `--ui-line`, `--ui-radius-surface`, `--ui-shadow-surface` |
| Dialog | `--ui-dialog-sm/md/lg/xl`, `--ui-radius-dialog`, `--ui-shadow-dialog` |

## Visual polish layer

- Тёмная command-surface разрешена только для Today: это точка входа в продукт, а не новый универсальный тип карточки.
- Amber обозначает spend, primary action и brand emphasis; blue — traffic/leads и информационный контур; violet — registrations и rule grouping; teal — purchases, healthy connection и безопасное действие.
- Цвет всегда дублируется подписью, числом, иконкой или геометрией; он не является единственным носителем состояния.
- Ambient gradients, тонкая texture и shadow живут на внешней surface. Вложенные рабочие элементы остаются плоскими и разделяются линиями или spacing.
- Visual polish не добавляет fake metrics, фиктивные controls или новые действия без существующего handler/API contract.
- Hover и lift используются только там, где элемент интерактивен; при `prefers-reduced-motion` переходы отключаются.

Новые компоненты не добавляют direct hex или inline styles. Исключения допустимы только для внешнего brand asset (например, Meta blue) и user-configured color.

## Components

| Component | Selector | Required states |
|---|---|---|
| Button | `.ui-button`, `.ui-button-primary`, `.ui-button-danger` | default, hover, focus-visible, disabled, busy |
| IconButton | `.ui-icon-button` | label/title, hover, focus-visible, disabled |
| Input | `.ui-input` | empty, filled, focus, invalid, disabled |
| Select | `.ui-select` | closed, open, selected, disabled |
| Tabs | `.ui-tabs`, `.ui-tab` | selected via `aria-selected`, focus, overflow |
| Badge | `.ui-badge` + semantic modifier | neutral, success, warning, danger |
| Tooltip | `.ui-tooltip` | short explanation; not a required action |
| Popover | `.ui-popover` | anchored, dismissible, viewport-safe |
| Modal | `.ui-modal`, `.ui-dialog` | title, body, actions, escape/close, responsive sheet |
| Drawer | `.ui-drawer` | desktop side panel, mobile full-width |
| Table | `.ui-table` | loading, empty, populated, long IDs, sticky context |
| KPI | `.ui-kpi-value` | value, no data, partial, comparison |
| Chart | `.ui-chart` | loading, no data, populated, accessible summary |
| EmptyState | `.ui-empty-state` | reason, next step, one primary CTA |
| Alert | `.ui-alert` + semantic modifier | info, warning, danger, success/action |
| Skeleton | `.ui-skeleton` | stable geometry, reduced layout shift |

## Pilot screens

### Today

- selector: `[data-ui-pilot="today"]`;
- удалён декоративный AI composer;
- тёмный command hero показывает живую дату и фактическое состояние workspace по `/api/meta/connections`, `/api/health/overview` и `/api/accounts`;
- три hero-сигнала означают только реальные величины: активные/все Meta-подключения, healthy/все кабинеты и покрытые/активные кабинеты;
- command bar содержит ровно одно next-best action с фиксированным порядком: setup → доступ/токен → critical/degraded health → покрытие правилами → ошибки действий → эффективность;
- сигналы и пять последних `/api/audit-events` собраны в одну divided operations surface, без карточек внутри карточек;
- при частичной ошибке успешные источники остаются видимыми, недоступные значения получают `—` и явное объяснение; данные не вычисляются и не подменяются;
- secondary navigation сохраняет только существующие product routes, а каждая интерактивная строка имеет реальный handler.

### Automations

- selector: `[data-ui-pilot="automations"]`;
- shared page header, primary Button и EmptyState;
- Kanban wrapper и lanes прозрачные: единственная самостоятельная surface в рабочей области — rule card;
- lane headers используют компактные semantic bands, а rule cards получают left rail по фактическому типу действия: stop, start/increase, decrease или notify;
- существующие rule groups, detail, modal и API contracts сохранены.

#### Guided Rule Builder

- create и edit используют одну трёхшаговую модель `Условия → Действие → Проверка`, сохраняя существующие DOM ids, handlers и payload;
- шаг отображает только реальное состояние навигации и валидации: никаких процентов, таймеров или fake progress;
- новый шаблон не получает action и threshold автоматически; опасное действие требует явного выбора;
- дополнительные ограничения и уведомления раскрываются по запросу, но обязательные guardrails остаются в основном потоке;
- review всегда содержит human-readable `ЕСЛИ / ТО` и preflight: workspace, объекты, текущий охват кабинетов, частоту проверки, cooldown, логику и action-specific limits;
- создание шаблона не означает назначение кабинета: это явно написано в preflight, а новое назначение остаётся отдельным существующим действием;
- безопасный create draft хранит только non-secret form values, версионируется, валидируется и изолируется ключом workspace; edit draft не восстанавливается из-за риска stale overwrite;
- состояние шага, готовность, warning и восстановленный draft обозначаются текстом/символом вместе с цветом;
- на `390px` condition row складывается в одну колонку, footer actions переносятся, dialog не создаёт horizontal overflow.

### Efficiency

- selector: `[data-ui-pilot="efficiency"]`;
- Spend остаётся главным KPI и занимает две колонки; остальные метрики группируются по смыслу, а не превращаются в одинаковые карточки;
- blue/violet/teal accents помогают сканировать путь `traffic → registration → purchase`, при этом значения и подписи остаются достаточными без цвета;
- фильтры, freshness status и refresh action используют общую control geometry и существующий data contract.

### Connections

- selector: `[data-ui-pilot="connections"]`;
- shared page header, Buttons, Table и EmptyState;
- OAuth, invite и manual-token handlers сохранены;
- основной OAuth-сценарий начинается с value-before-OAuth dialog: пользователь до перехода в Facebook видит результат подключения, запрашиваемые permissions и влияние на автоматизации;
- реальная последовательность одинакова на странице и в модальных окнах: `Подключение → Выбор кабинетов → Проверка доступа → Готово`;
- завершённым отмечается только шаг, подтверждённый ответом Facebook/Meta или Buyerly API; ожидание ответа показывается indeterminate progress без вымышленных процентов;
- refresh, validate, import и reconnect используют локальный busy-state, `aria-live` feedback и сохраняют доступный повторный action после ошибки;
- manual-token остаётся явно техническим advanced-сценарием и не маскируется под основной OAuth flow.

## Trust flow и motion contract

`meta-flow-steps` применяется только к конечным процессам с реальными контрольными точками. Он не является декоративным progress bar и не должен предсказывать длительность операции.

- `is-current` означает, что действие пользователя или ответ внешнего сервиса ожидается сейчас;
- `is-complete` выставляется только после фактического завершения шага;
- неизвестная длительность использует indeterminate track и понятный текст текущей операции;
- ошибка завершает loading, сохраняет контекст и показывает следующий безопасный action; успешные частичные результаты не скрываются;
- footer у длинных trust dialogs остаётся sticky, чтобы `Отмена` и primary action были доступны при любом размере viewport;
- interaction transitions используют диапазон `140–200ms`; текущий базовый timing — `160ms` для control state и `180ms` для progress/state transition;
- `prefers-reduced-motion: reduce` отключает движение и оставляет статическое, текстово различимое состояние;
- секреты, токены, пароли и cookies никогда не используются как display data, progress metadata или diagnostic copy.

## Responsive contract

- `390px`: шесть mobile destinations помещаются без горизонтального scroll; длинные desktop-названия сокращены до `Сводка`, `Правила` и `Связи`;
- `390px`: Today сохраняет live status, складывает operations surface и context links в одну колонку, а primary next action занимает полную ширину;
- `390–480px`: KPI используют две колонки, а главный Spend занимает всю строку; при ширине до `360px` сетка безопасно складывается в одну колонку;
- `768px`: sidebar уступает место mobile navigation, data surfaces не создают document-level horizontal overflow;
- `1024px+`: sidebar и content shell сохраняют независимую геометрию, таблицы прокручиваются только внутри собственного viewport;
- иконки внутри action buttons всегда имеют явный размер `16×16px`, чтобы native SVG intrinsic size не ломал mobile layout;
- auth footer переносится на несколько строк и не выходит за mobile viewport.

## Migration map

| Legacy family | Foundation target | Rule |
|---|---|---|
| `.btn*`, `.attio-header-btn` | `.ui-button*` | добавлять foundation class, затем удалять legacy после всех consumers |
| `.attio-checkbox`, form-specific inputs | `.ui-input` / native control tokens | не менять ids/API payloads |
| `.settings-subnav-btn`, `.record-tab-btn` | `.ui-tab` | сохранить `data-*` и onclick contract |
| `.status-pill`, `.badge*`, `.label-badge` | `.ui-badge*` | semantic name, не color name |
| `.attio-dropdown-menu`, custom popovers | `.ui-popover` | единый layer/elevation/focus loop |
| `.modal-card`, `.modal-dialog` | `.ui-modal` | единый header/body/footer contract |
| `.attio-table`, `.data-table`, `.logs-table` | `.ui-table` | shared typography/row height, domain columns remain |
| `.empty-state`, `.rules-empty-card` | `.ui-empty-state` | reason + next action |
| `.loading-state`, `.spinner` | `.ui-skeleton` | skeleton для layout, spinner только для atomic action |

## Review checklist

- primary action и warning визуально различаются;
- interactive target не меньше 36px, mobile priority target 44px;
- keyboard focus видим;
- основной текст не меньше 14px;
- каждый control имеет действие или удалён;
- state не передаётся только цветом;
- новые styles используют semantic tokens;
- desktop/mobile сохраняют один information model.
- визуальный характер создаётся semantic accent и иерархией, а не дополнительными nested surfaces;
- KPI, stats и summary groups используют divided surface, а не россыпь вложенных карточек;
- у каждой таблицы только один внешний surface и собственный horizontal scroll region;
- все dialog families визуально проходят через `.ui-dialog`.
- presentation rules живут в UI-kit, а не в `style="..."` внутри страниц или динамических строк.
