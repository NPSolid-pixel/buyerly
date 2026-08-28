# Implementation plan — BL-101 Buyerly Design System

ClickUp: `86eyr6073`

## Цель

Создать semantic foundation, который можно внедрять поэтапно без переписывания API и product behavior. Доказать систему на трёх пилотных экранах: Today, Automations, Connections.

## Изменения

1. Добавить semantic color, typography, spacing, size, radius, border, elevation, layer и motion tokens.
2. Развести брендовый amber, доступный primary action и warning semantics.
3. Зафиксировать base text 14px, tabular numbers и mono только для ID/code.
4. Определить Button, IconButton, Input, Select, Tabs, Badge, Tooltip, Popover, Modal, Drawer, Table, KPI, Chart, EmptyState, Alert и Skeleton.
5. Удалить декоративный AI composer с Today и заменить его реальными переходами.
6. Подключить shared primitives к Today, Automations и Connections без изменения API routes.
7. Добавить documentation/frontend contracts и пройти GitHub Actions.

## Риски и защита

- Старые selectors сохраняются как compatibility layer; новые классы добавляются рядом.
- URL, API calls, ids и onclick contracts не меняются.
- Удаляется только неиспользуемый CSS декоративного AI composer.
- Остальные экраны мигрируют после production-подтверждения пилотов.
