# Visual Quality Gate — implementation plan

Date: 2026-08-29
ClickUp: BL-109 / `86eyrx9ch`
Branch: `feat/bl-109-visual-quality-gate`

## Goal

Make visual quality a repeatable release contract for every authenticated Buyerly
workspace route. The gate must catch document-level horizontal overflow,
unusable responsive layouts, missing meaningful states, basic accessibility
regressions and unintended visual changes before a pull request can merge.

## Scope

The audited route matrix is Today, Connections, Ad accounts, Automations,
Efficiency, Action History and Settings at desktop and mobile widths. Coverage
includes loading, empty, error, partial-data and adversarial long-content
fixtures. Work from BL-103–107 is treated as the current product baseline; this
task changes shared contracts and guardrails rather than repeating route-specific
redesigns.

## Architecture

1. Add a deterministic browser fixture layer that serves the real production
   HTML, CSS and JavaScript and mocks only backend responses. Fixtures remain
   workspace-scoped and expose explicit loading, empty, error, partial and
   long-content scenarios.
2. Add Playwright coverage for every main workspace route on desktop and mobile.
   Assertions cover route visibility, document overflow, viewport containment,
   keyboard focus, accessible names, duplicate IDs and broken images.
3. Add stable visual snapshots for a focused representative matrix, while the
   full route/state matrix uses structural assertions to keep CI useful and
   maintainable.
4. Add shared responsive safety rules for grid/flex/table/dialog/content
   boundaries discovered by the audit. Preserve intentional local table
   scrolling while forbidding page-level horizontal scrolling.
5. Run the browser gate as a required GitHub Actions job, upload diagnostics and
   screenshots on failure, and keep the existing Python suite unchanged.
6. Document the route/state contract and the process for intentionally updating
   visual baselines.

## Verification

- Local: JavaScript syntax checks, package/config validation, CSS/HTML contract
  inspection and `git diff --check`. No local `pytest`, `unittest`,
  `python -m unittest` or repository test suite.
- Remote: existing unit/integration job plus the new Playwright visual-quality
  job in GitHub Actions.
- PR: inspect failed logs and uploaded browser artifacts through GitHub Actions;
  require all checks to pass before stopping for review. Do not merge.

## Definition of done

- Every main workspace route is covered at desktop and mobile viewport sizes.
- Loading, empty, error, partial and long-content behavior is represented by
  deterministic fixtures and validated by the gate.
- No route can introduce global horizontal overflow at the audited widths.
- Interactive controls retain visible keyboard focus and accessible names;
  documents have one main landmark and no duplicate IDs.
- Representative screenshots have reviewed, versioned baselines and a documented
  update workflow.
- CI emits actionable failure artifacts and is green on the pull request.
- The PR lists ClickUp BL-109 / `86eyrx9ch`, the changes and verification. The PR
  remains unmerged and the ClickUp task remains open until production deploy.
