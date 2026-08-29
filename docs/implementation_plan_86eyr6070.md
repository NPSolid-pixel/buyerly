# Implementation plan — BL-104 Efficiency workspace

ClickUp: `86eyr6070`

## Goal

Finish the Efficiency workspace as a reliable decision surface for current
Meta performance without changing the existing API routes, workspace scope or
saved table preferences.

## Data contract

- `/api/summary?period=...`: current KPI totals, per-account rows, data quality,
  currency buckets, cache provenance and the previous saved snapshot;
- `/api/analytics-view`: persisted period, group, table filters, sort, visible
  columns, order and widths;
- `/api/accounts` and `/api/account-groups`: current account labels, notes and
  workspace-scoped groups.

The UI must remain useful when the response is empty, partially synchronized,
served from cache, stale, or temporarily unavailable. Monetary values from
different currencies must never be combined.

## Work packages

1. Add a dedicated, accessible loading/empty/error status surface that preserves
   layout and offers a real retry or Connections action.
2. Turn the existing KPI area into a responsive, long-value-safe overview with
   previous-snapshot comparisons for the primary funnel metrics.
3. Add an accessible current-period funnel chart sourced only from the existing
   summary totals, including honest unavailable and zero-result states.
4. Harden period, group, search, status and table controls for keyboard and
   screen-reader use while preserving persisted preferences.
5. Keep the account table on desktop and cards on mobile, with contained table
   scrolling and no document-level horizontal overflow.
6. Add a dedicated Efficiency workspace contract test covering markup, state
   transitions, comparison/chart data use, responsive containment, escaping and
   the unchanged API endpoints.

## Verification

- Do not run `pytest`, `unittest` or any local Python test command per `AGENTS.md`.
- Run the repository-permitted JavaScript syntax validation and `git diff --check`.
- Verify the populated, partial, empty, first-load error and cached-refresh
  states at desktop and mobile widths.
- Push an isolated PR and require green GitHub Actions before stopping. Do not
  merge or close ClickUp before a confirmed production deployment.

Completed locally with repository-permitted checks:

- every `webapp/js/*.js` file passes `node --check` using the bundled runtime;
- `git diff --check` passes;
- Browser QA confirms no document-level horizontal overflow at
  390/768/1024/1440px; the wide desktop table scrolls only inside its own
  container;
- populated partial-data, loading-to-ready, empty, first-load error and
  cached-refresh-error states expose the intended status, content visibility
  and recovery actions;
- period, account-group, search, status, keyboard sort and columns-dialog focus
  behavior match their accessible state.

The Python contract and full suite remain intentionally deferred to GitHub
Actions, as required by `AGENTS.md`.

## Definition of done

- KPI, funnel, comparison and account breakdown agree with the selected period
  and account-group scope.
- Loading, empty, error, cached-refresh and partial-data states are explicit and
  never present placeholder values as real performance.
- Long account names, notes, currency amounts and counters remain readable
  without page-level horizontal overflow.
- Period, view, status and sort controls expose their selected/sort state and are
  fully keyboard-operable.
- Existing data/API contracts and saved analytics views remain compatible.
- Dedicated cloud contract tests and the full GitHub Actions quality gate pass.
