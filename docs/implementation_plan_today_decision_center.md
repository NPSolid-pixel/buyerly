# Today Decision Center — implementation plan

Date: 2026-08-29
Branch: `feat/ux-today-decision-center`
Status: implemented and Browser-QA verified; awaiting GitHub Actions

## Goal

Turn Today from a static navigation showcase into a workspace-scoped decision
surface that answers three questions from existing production data: what needs
attention now, why it matters, and which single next action is safest.

## Data contract

- `/api/accounts`: account activity, automation coverage, persisted Today
  metrics, and per-account health.
- `/api/meta/connections`: active and unhealthy Meta access states.
- `/api/health/overview`: overall health, account counts, token problems,
  worker lag, and action error rate.
- `/api/audit-events?page=1&page_size=5`: latest real decisions and status
  counts.

Every request is read-only and workspace-isolated. Partial results remain
useful when one endpoint is unavailable; no metric, progress, urgency, or state
is inferred without a real source.

## Experience

1. Keep the existing premium command hero, but replace its decorative loop with
   live workspace status and three compact real signals.
2. Make the command bar the single deterministic next-best action, ordered by
   missing setup, access/token problems, critical/degraded health, automation
   coverage, recent action errors, then healthy overview.
3. Replace three oversized navigation cards with one divided operations surface:
   signal explanations on the left and the five latest audit events on the right.
4. Preserve compact contextual links to Connections, Automations, and History.
5. Provide loading, partial, empty, healthy, warning, and critical states without
   fake skeleton values or color-only meaning.

## Verification

- Extend frontend contract tests for Today markup, API use, deterministic
  priority order, partial-state copy, and shared UI selectors.
- Run no local test suite per repository policy; use `git diff --check` and
  static inspection locally.
- Browser QA at 1440, 1024, 768, and 390px with zero document-level horizontal
  overflow.
- Push one isolated PR and require green GitHub Actions; do not merge or deploy.

## Definition of done

- The first actionable issue and its reason are visible without opening another
  page.
- The primary CTA always routes to an existing screen or account detail.
- Health, connection, coverage, and audit content is sourced from current APIs.
- One failed endpoint does not hide successful signals from the other sources.
- Existing API, navigation, modal, and workspace contracts remain unchanged.

## Browser QA result

- Verified the production shell first, then rendered the implemented Today
  surface locally with the last observed production values and audit records.
- `1440`, `1024`, `768`, and `390px` all reported
  `documentElement.scrollWidth === documentElement.clientWidth`.
- At `1440/1024` the divided signal/history surface stays two-column; at
  `768/390` it becomes one column. At `390px` the next-best action remains in
  the first viewport and expands to the full command-bar width.
- Temporary QA markup and server were removed after the check.
