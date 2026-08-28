# Implementation plan — BL-100 UX baseline

ClickUp: `86eyr6074`

## Цель

Зафиксировать проверяемый baseline текущего Buyerly до миграции интерфейса: экраны, состояния, проблемы, пользовательские сценарии и ожидаемый эффект. Baseline служит входным контрактом для BL-101 (design system) и BL-102 (information architecture).

## Источники

- production: `https://buyerly.app`;
- production-контракт: `webapp/index.html`, `webapp/css/styles.css`, `webapp/js/app.js`;
- маршруты и API-контракты из `TAB_ROUTES` и функций загрузки каждого раздела;
- responsive-контракт: desktop shell от `768px`, mobile navigation и card/table variants ниже `768px`.

## Этапы

1. Составить карту всех публичных, onboarding и authenticated экранов.
2. Для каждого экрана зафиксировать loading, empty, populated, error, partial, long-content и permission-denied состояния, когда они применимы.
3. Провести инвентаризацию компонентов, терминов, визуальных токенов и интерактивных паттернов.
4. Выделить риски и три пилотных экрана для BL-101.
5. Сохранить baseline в `docs/UX_BASELINE_2026-08-29.md` и защитить документ облачным documentation contract.

## Definition of Done

- все production-маршруты и meaningful states присутствуют в карте;
- каждая проблема связана с пользовательским сценарием и ожидаемым эффектом;
- отмечены неработающие или вводящие в заблуждение контролы;
- зафиксированы терминология, локализация, иерархия, плотность, контраст и navigation debt;
- выбраны пилоты BL-101 без изменения текущего product behavior;
- GitHub Actions проходит полностью.
