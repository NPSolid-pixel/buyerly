# Buyerly Meta Trust Flow — implementation plan

Date: 2026-08-29
Branch: `feat/ux-meta-trust-flow`
Status: implemented; awaiting GitHub Actions

## Goal

Сделать подключение Facebook-профиля прозрачным и доверительным сценарием от объяснения ценности до подтверждённого импорта кабинетов. Сохранить официальный Meta OAuth, одноразовый `state`, workspace/RBAC isolation, шифрование токенов и существующие API contracts.

## UX contract

1. До перехода в Facebook пользователь видит, что Buyerly получит, зачем нужны разрешения и чего продукт не делает.
2. Сценарий описывается честной последовательностью `Подключение → выбор кабинетов → проверка доступа → готово`.
3. Этап получает статус completed только после подтверждённого результата API или OAuth callback. Во время сетевого запроса используется indeterminate loading без фиктивного процента.
4. Любой import/refresh/validate/reconnect показывает локальный busy state, понятный результат и безопасный retry.
5. Partial success сохраняет успешные элементы и явно перечисляет ошибки; состояние не передаётся только цветом.
6. Manual System User token остаётся отдельным advanced flow и никогда не отображается, не логируется и не сохраняется в browser storage.

## Work packages

### 1. Value before OAuth

- Добавить `modalMetaOAuthIntro` с кратким результатом подключения, required permissions, privacy assurances и статической картой процесса.
- Основная CTA открывает intro; только явная кнопка `Продолжить в Facebook` вызывает существующий `/api/meta/oauth/start`.
- Reconnect использует тот же trust contract и сохраняет `reconnect_connection_id`.

### 2. Honest connection progress

- Добавить reusable `.meta-flow-steps` и JS helper для состояний `complete/current/pending/error`.
- После OAuth callback отметить подключение завершённым и открыть выбор активов.
- На импорте убрать искусственные `30%/35%`: до ответа API показывать indeterminate verification; после ответа — фактический completed/partial/error receipt.
- Не менять backend OAuth/state/import semantics.

### 3. Durable states and feedback

- Добавить page-level `aria-live` feedback для загрузки списка подключений и результатов операций.
- Дать refresh/validate/reconnect/import controls собственный busy state и восстановление label/disabled state.
- Сформулировать безопасные error messages без token/scopes payload dumps и с конкретным retry action.
- Сохранить существующие confirm barriers для удаления и отзыва.

### 4. Dialog and motion contract

- Закрепить sticky modal footer для scrollable trust flows и безопасный mobile bottom-sheet layout.
- Использовать motion 140–200ms только для интерактивной обратной связи; полностью отключать transform/animation при `prefers-reduced-motion`.
- Обновить invite/manual-token copy так, чтобы основной OAuth и advanced flow были визуально и семантически разделены.

### 5. Verification and documentation

- Расширить frontend contract tests для intro modal, stepper, live feedback, honest progress и no-token-output invariants.
- Обновить `docs/DESIGN_SYSTEM.md`, `docs/FACEBOOK_AUTHORIZATION_PLAN.md` и `CHANGELOG.md`.
- Browser QA: desktop + 390/768/1024/1440, empty/loading/error/connected/modal states, zero document-level horizontal overflow.
- Локальные тесты не запускать. После push открыть PR, дождаться зелёного GitHub Actions и остановиться до merge/deploy.

## Non-goals

- изменение permissions, Graph API version, OAuth callback или token lifecycle;
- ослабление `state`, workspace isolation, RBAC, encryption, redaction или disconnect semantics;
- автоматическое включение правил после импорта;
- fake progress, urgency, metrics, permissions или optimistic success до ответа API;
- переработка страниц вне Connections и shared interaction primitives.

## Definition of done

- пользователь понимает ценность и последствия до OAuth redirect;
- completed steps основаны только на реальных callback/API results;
- подключение, выбор, проверка и итог имеют distinct accessible states;
- loading/error/reconnect/partial success восстанавливаются без перезагрузки страницы;
- modal actions остаются видимыми на длинном контенте и mobile;
- sensitive token values не появляются в DOM, toast, logs, URL или документации;
- PR проходит GitHub Actions и остаётся неслитым для координированного merge.

## Verification record

- Production baseline audited in the authenticated Browser before implementation; no OAuth, validate, refresh, import, reconnect, delete or other state-changing action was triggered.
- Local UI/CSS was previewed in-memory over the production Connections DOM without API mutations, then the tab was reloaded to remove all injected preview state.
- Responsive Browser QA passed at `390`, `768`, `1024` and `1440` with zero document-level horizontal overflow.
- Value-before-OAuth, sticky mobile actions and honest indeterminate progress were visually inspected; progress has no `aria-valuenow` while waiting.
- `prefers-reduced-motion: reduce` resolves both progress animation and transition duration to `none/0s`.
- Browser runtime parsed `webapp/js/app.js` with `new Function` successfully. Local test suites were intentionally not run; GitHub Actions is the required test authority.
