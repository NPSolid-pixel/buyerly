# Guided Rule Builder — implementation plan

## Status

- Owner: Buyerly product UI
- Branch: `feat/ux-guided-rule-builder`
- Scope: create and edit automation rules
- Contract: preserve existing DOM ids, JavaScript handlers, API payloads, RBAC, workspace isolation and security behavior
- Delivery: pull request and GitHub Actions only; no merge or deploy in this task

## Browser audit baseline

The production create/edit flow was inspected before implementation.

Observed issues:

1. A destructive `turn_off` action and a numeric threshold of `2` are preselected for a new rule.
2. Create can be submitted without a rule name; the UI does not make the incomplete state clear.
3. The builder does not show which cabinets or objects may be affected, what will change, how often the rule runs, or which limits apply.
4. The IF/THEN summary exists as plain text but is not the primary decision surface.
5. Cancelling a partially completed create form loses the draft.
6. Edit does not disclose the rule's current reach before saving.
7. At 390 px, the condition editor creates an internal horizontal scrollbar and clips controls.
8. Status and warnings rely too heavily on compact color accents.

No production data was changed during the audit.

## Product outcome

Turn the existing flat modal into a guided, reversible workflow that helps an operator answer four questions before saving:

1. **IF:** what exact evidence must be true?
2. **THEN:** what explicit action may be taken?
3. **WHERE:** which workspace, cabinets and objects are in scope?
4. **WHEN / LIMITS:** how often can it run, and what guardrails constrain it?

The workflow must remain compact enough for experienced operators while preventing accidental destructive configuration.

## Information architecture

Use three real steps inside the existing create and edit dialogs:

1. **Условия** — condition rows and AND/OR logic.
2. **Действие** — required action plus action-specific fields; advanced timing/notification controls are progressively disclosed.
3. **Проверка** — name/group, human-readable IF/THEN preview and consequence preflight.

The step rail reports real navigation and validation state only. It must not simulate progress or completion.

Existing field ids and event handlers remain authoritative. Panels only reorganize the presentation.

## Safety decisions

- New rules start with no action selected.
- The scaffold condition may start with metric/operator/period, but its threshold starts empty.
- Submit is available only on the final step and remains disabled until required data is valid.
- No rule is assigned to a cabinet automatically.
- Create preflight explicitly states that the new template changes nothing until assigned.
- Edit preflight derives linked cabinets from existing client state and lists the current reach.
- Existing saved values are never replaced by new smart defaults while editing.
- State and warnings include text and/or icons, never color alone.

## Draft persistence

Persist only safe, non-secret create-form values in browser preferences, namespaced by workspace. Validate the stored shape before restore.

- Save after meaningful create-form changes.
- Restore with an explicit notice and a discard action.
- Clear after successful creation or explicit discard.
- Do not persist edit drafts: restoring an old edit over a server-side rule is a higher stale-write risk.
- Never store tokens, credentials, cabinet data or server responses.

No new backend draft API is needed.

## Preflight content

The review step must disclose:

- readable `ЕСЛИ` conditions and `ТО` action;
- workspace context;
- object type (`ad set`);
- linked cabinet count and names for edits, or zero-assignment explanation for creates;
- evaluation frequency from the existing payload contract;
- cooldown/repeat restriction;
- AND/OR logic;
- action-specific budget percentage and ceiling when applicable;
- Telegram notification behavior;
- validation warnings and non-blocking limitations.

## Responsive behavior

- 1440/1024: two-column review where space permits; one coherent dialog surface.
- 768: panels stack without clipped content.
- 390: condition controls become a single-column flow, footer actions wrap, and no document or dialog-content horizontal overflow is allowed.
- Preserve visible focus, keyboard navigation, semantic labels and reduced-motion behavior.

## Implementation sequence

1. Reorganize the existing create/edit dialog markup into shared builder primitives while preserving ids.
2. Add step navigation and validation gates to the existing controllers.
3. Replace dangerous create defaults with explicit empty choices.
4. Add workspace-scoped create-draft persistence using the existing browser preferences layer.
5. Render the IF/THEN decision preview and consequence preflight from the same current form values used for payload construction.
6. Derive edit reach from existing account/preset state without new API calls.
7. Add responsive, token-based styles in `ui-system.css` and remove mobile horizontal overflow.
8. Extend frontend contract tests for ids, safety defaults, draft isolation and preflight semantics.
9. Update the design-system documentation and changelog.
10. Review the diff, run non-test static checks only, push, open a PR and use GitHub Actions as the sole test runner.
11. Perform Browser QA at 390, 768, 1024 and 1440 widths. Do not merge or deploy.

## Acceptance criteria

- Create and edit complete end-to-end with existing API payload compatibility.
- A new rule cannot accidentally inherit a destructive action or threshold.
- IF/THEN and consequence preflight update from current field values before save.
- Create drafts restore only within the same workspace and can be discarded.
- Edit accurately states current linked-cabinet reach.
- All controls remain usable by keyboard and state is not communicated only by color.
- No horizontal overflow at 390/768/1024/1440.
- Existing RBAC, workspace and security contracts remain unchanged.
- GitHub Actions is green; PR remains unmerged and undeployed.

## Risks and rollback

- **Risk:** markup reordering may break selectors. Mitigation: preserve ids/classes and add contract assertions.
- **Risk:** stored drafts may become incompatible. Mitigation: versioned key, strict validation and fail-closed reset.
- **Risk:** linked-account client state may be incomplete. Mitigation: label it as current assignment data and never expand scope from it.
- **Risk:** added guidance may make the modal too tall. Mitigation: bounded dialog body, responsive stacking and progressive disclosure.
- **Rollback:** revert the frontend commit; no API or database migration is introduced.
