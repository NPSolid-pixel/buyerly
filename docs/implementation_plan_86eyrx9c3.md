# BL-103 — Today workspace completion plan

Date: 2026-08-29
ClickUp: `86eyrx9c3` / `BL-103`
Branch: `feat/bl-103-today-workspace`
Status: implemented and Browser-QA verified; awaiting GitHub Actions

## Goal

Finish Today as the default media-buyer workspace: within the first five
seconds it must explain whether the workspace is healthy, what requires
attention, why it matters, and which existing safe action to take next.

## Production audit

- The current decision center already uses real workspace-scoped accounts,
  Meta connection health, account health and audit events.
- The screen does not yet expose the reporting period, currency, source,
  freshness, period comparison, spend/event movement or a compact funnel.
- Account anomalies are only summarized as aggregate health counts, so the
  affected cabinet and effect are not visible without leaving Today.
- Loading and unavailable history are plain text placeholders; analytical
  failures have no dedicated retry or partial-data explanation.
- The production mobile DOM has no document-level horizontal overflow at
  `390px`, and the completed screen must preserve that contract.

## Implementation

1. Keep existing workspace status and deterministic next-best-action logic.
2. Load cached/fact-store `/api/summary` data for `today` and `yesterday`
   alongside the existing read-only sources, without forcing a Meta refresh.
3. Add a provenance rail with period, source, generated time, freshness and
   data-quality coverage; never combine spend across currencies.
4. Add a divided KPI surface for spend, leads, registrations and purchases,
   with truthful same-length period comparisons only when both periods are
   comparable.
5. Add compact real-data views for spend/event movement, funnel conversion and
   anomalous accounts. Do not draw empty charts or infer unavailable values.
6. Replace text-only loading/empty/error states with accessible shared state
   surfaces, keep partial results visible, and provide a single retry action.
7. Tighten desktop/mobile hierarchy, target sizes, wrapping and overflow rules
   without changing other routes.

## Verification

- Extend `tests/test_frontend_contract.py` for Today markup, API use, state
  handling, multi-currency safeguards, accessibility and responsive selectors.
- Run only repository-approved local checks: JavaScript syntax validation,
  `git diff --check`, static contract inspection and Browser QA. Do not run
  pytest or unittest locally.
- Validate desktop and `390x844` layouts with zero document-level horizontal
  overflow.
- Push one isolated PR with ClickUp ID, implementation summary and verification
  evidence; require green GitHub Actions and stop before merge.

## Definition of done

- Today contains no decorative or fabricated metric.
- Partial failures do not hide successful sources.
- Mixed currencies are separated or explicitly unavailable as a combined KPI.
- Loading, empty, populated, partial and error states remain actionable and
  accessible on desktop and mobile.
- The PR CI is green; merge and ClickUp closure remain pending explicit user
  approval and successful production deployment.

## Browser QA result

- Production was audited in the authenticated Buyerly workspace before changes;
  the existing screen had no document-level overflow at `390px` but lacked the
  analytical scope listed above.
- The completed Today surface was rendered with the observed partial production
  state at `1440`, `1024`, `768` and `390x844`.
- Every viewport reported `documentElement.scrollWidth === clientWidth`.
  Intelligence changes from three columns to two at `1024px` and one at
  `768/390px`; KPIs change from four columns to two without page overflow.
- Mobile interactive targets are at least `44px`; keyboard focus uses the
  shared visible focus ring and reduced-motion rules cover new interactive and
  loading elements.
