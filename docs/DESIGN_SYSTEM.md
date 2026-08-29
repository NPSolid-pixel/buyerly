# Buyerly Design System

Version: Unified UI 2.0
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

## Production architecture

- `webapp/css/styles.css` сохраняет legacy и domain-specific поведение;
- `webapp/css/ui-system.css` загружается последним и является единым production-контрактом геометрии, типографики, surfaces и responsive;
- все семь authenticated-разделов помечены `data-ui-pilot` и используют одну ширину `--ui-page-max` и gutter `--ui-page-gutter`;
- все 22 modal overlays и command palette получают `.ui-dialog`, `role="dialog"` и `aria-modal="true"` без изменения id и JavaScript handlers;
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
- hero остаётся на canvas, рабочий обзор оформлен строкой с разделителями, а быстрые действия собраны в один segmented surface без россыпи вложенных карточек;
- Alert, Button и action segments ведут только к существующим product routes;
- нет fake metrics или controls без обработчика.

### Automations

- selector: `[data-ui-pilot="automations"]`;
- shared page header, primary Button и EmptyState;
- Kanban wrapper и lanes прозрачные: единственная самостоятельная surface в рабочей области — rule card;
- существующие rule groups, detail, modal и API contracts сохранены.

### Connections

- selector: `[data-ui-pilot="connections"]`;
- shared page header, Buttons, Table и EmptyState;
- OAuth, invite и manual-token handlers сохранены.

## Responsive contract

- `390px`: шесть mobile destinations помещаются без горизонтального scroll; длинные desktop-названия сокращены до `Сводка`, `Правила` и `Связи`;
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
- KPI, stats и summary groups используют divided surface, а не россыпь вложенных карточек;
- у каждой таблицы только один внешний surface и собственный horizontal scroll region;
- все dialog families визуально проходят через `.ui-dialog`.
- presentation rules живут в UI-kit, а не в `style="..."` внутри страниц или динамических строк.
