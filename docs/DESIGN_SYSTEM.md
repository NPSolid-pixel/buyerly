# Buyerly Design System

Version: Foundation 1.0  
Owner: Product / Frontend  
ClickUp: BL-101 `86eyr6073`

## Principles

1. **Action is not warning.** Brand amber может использоваться в identity, primary action использует более тёмный доступный `--action-primary`, warning — отдельные foreground/background/border tokens.
2. **Readable by default.** Базовый UI-текст — 14px; 12px разрешён для secondary metadata, но не для основного действия или значения.
3. **Numbers scan, identifiers copy.** Числа используют tabular numerals; mono применяется только для ID, code, SHA и технических значений.
4. **State before decoration.** Loading, empty, partial, error, permission и success должны быть понятны без цвета.
5. **Progressive migration.** Новый компонент получает `ui-*` contract; legacy selector может сосуществовать до переноса всех consumers.

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
| Modal | `.ui-modal` | title, body, actions, escape/close |
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
- hero, Alert, Button и action cards ведут только к существующим product routes;
- нет fake metrics или controls без обработчика.

### Automations

- selector: `[data-ui-pilot="automations"]`;
- shared page header, primary Button и EmptyState;
- существующие rule groups, detail, modal и API contracts сохранены.

### Connections

- selector: `[data-ui-pilot="connections"]`;
- shared page header, Buttons, Table и EmptyState;
- OAuth, invite и manual-token handlers сохранены.

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

