# Buyerly Visual Polish — implementation plan

Status: implementation

## Goal

Сделать Buyerly визуально характерным premium operations console, сохранив единую геометрию, читаемость, рабочую плотность и все существующие product contracts.

## Scope

1. Расширить общий UI-kit semantic accent tokens, ambient depth, interactive elevation и reduced-motion rules.
2. Сделать Today выразительной точкой входа: command hero, живая дата, реальный контур продукта и три рабочих перехода.
3. Усилить Automations через lane hierarchy и action semantics без карточек-контейнеров вокруг канбана.
4. Усилить Efficiency через доминирующий Spend и цветовую группировку реальных KPI.
5. Проверить 390, 768, 1024 и 1440px в production browser session без document-level horizontal overflow.
6. Доставить через pull request, обязательный GitHub Actions CI и production deploy.

## Non-goals

- изменение API, бизнес-логики, rule engine или данных;
- добавление фиктивных метрик, графиков и controls;
- редизайн существующей информационной архитектуры;
- локальный запуск тестов, запрещённый правилами репозитория.

## Acceptance

- Today, Automations и Efficiency визуально различимы, но используют один UI-kit;
- вложенные поверхности не возвращаются;
- keyboard focus и reduced motion сохранены;
- на контрольных ширинах отсутствует горизонтальный scroll документа;
- CI и production deploy завершены успешно.
