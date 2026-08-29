# BL-105 — Automations completion plan

## Scope and constraints

- ClickUp: `BL-105` (`86eyr606z`)
- Branch: `feat/bl-105-automation-builder`
- Preserve the existing rules API, payloads, workspace isolation, permissions and runtime behavior.
- Do not run local `pytest`, `unittest` or other test packages. GitHub Actions is the test runner.
- Keep the pull request unmerged and do not close ClickUp before a successful production deployment.

## Product outcome

Finish the Automations workspace and guided create/edit flows so operators can understand rule state, recover from loading and request failures, work safely with destructive actions, and complete every supported operation on desktop and mobile without horizontal overflow.

## Implementation sequence

1. Audit the current board, cards, groups, bulk actions and create/edit dialogs at 390, 768, 1024 and 1440 px.
2. Normalize the page structure and rule surfaces, including long names and compact/mobile layouts.
3. Add explicit loading, empty, error and disabled states with accessible status semantics and retry paths.
4. Add confirmations for destructive rule and group operations and prevent duplicate submissions.
5. Complete accessible names, keyboard/focus behavior and semantic live feedback in the builder and board.
6. Extend frontend contract tests without changing the backend contract.
7. Update product documentation and the changelog.
8. Run repository-permitted static checks, inspect the target-only diff, push, create a PR and use GitHub Actions as the sole test runner.
9. Stop with a green, open PR; do not merge or deploy.

## Acceptance criteria

- Existing create, edit, assign, detach, move, reorder and delete behavior remains API-compatible.
- Dangerous actions require an explicit, contextual confirmation and cannot be double-submitted.
- Loading, empty, error and disabled states are visible, understandable without color alone, and usable with assistive technology.
- Long rule/group/account names wrap or truncate intentionally without hiding primary actions.
- The page and dialogs have no document-level horizontal overflow at 390, 768, 1024 or 1440 px.
- Keyboard focus remains visible and modal focus returns to the invoking control.
- Contract tests cover the added UI states, safety and responsive guarantees.
- GitHub Actions is green and the PR contains only BL-105 changes.

## Risk and rollback

- DOM changes can break existing inline handlers; preserve existing ids and API calls and add contract assertions around new state primitives.
- Mobile fixes can weaken desktop density; scope responsive overrides to the smallest affected components.
- Confirmation changes can interrupt bulk workflows; keep messages contextual and perform mutations only after confirmation.
- Rollback is a single frontend/docs/test commit series with no migration or backend data change.
