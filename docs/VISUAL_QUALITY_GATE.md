# Buyerly visual quality gate

ClickUp: BL-109 / `86eyrx9ch`

## Release contract

The pull-request gate opens the real production `webapp/index.html`, CSS and
JavaScript with deterministic, workspace-scoped API fixtures. It audits the
seven first-class routes at `1440×1000` and `390×844`:

| Route | Loading | Empty | Error | Partial | Long content |
|---|---:|---:|---:|---:|---:|
| Today | ✓ | covered by zero-account priority | via partial sources | ✓ | ✓ |
| Connections | ✓ | ✓ | ✓ | ✓ | ✓ |
| Ad accounts | ✓ | ✓ | ✓ | covered by batch mutation contract | ✓ |
| Automations | ✓ | ✓ | covered by mutation alerts | covered by batch mutation contract | ✓ |
| Efficiency | ✓ | ✓ | ✓ | ✓ | ✓ |
| Action History | ✓ | ✓ | ✓ | covered by status counts | ✓ |
| Settings | ✓ | session empty state | ✓ | health fallback | ✓ |

Every matrix entry also checks:

- no document-level horizontal overflow;
- one `main` landmark and a visible route heading;
- unique DOM IDs and no broken visible images;
- accessible names for visible controls;
- keyboard focus on desktop and mobile navigation.

Wide operational tables may scroll inside their own `.table-responsive`,
`.attio-table-viewport` or board container. They must never expand the document.

## Visual regression model

GitHub Actions checks out the pull request and its exact base SHA. Both versions
run in the same Chromium process with the same viewport, locale, frozen clock,
fixtures and reduced-motion preference. Each populated workspace route is
captured on desktop and mobile and compared pixel-by-pixel. A change above 0.5%
fails the job and uploads current, baseline and diff PNGs with the Playwright
trace.

This base-SHA model avoids stale committed screenshots. External font requests
are replaced with a deterministic local fallback so network availability cannot
change layout or pixels.

Intentional visual work must first be reviewed from the uploaded diff. A narrow
exception can then be added to `visual-tests/approved-visual-changes.json` for
one route and one viewport. Every entry must contain the exact 40-character base
commit SHA, a `BL-…` ClickUp ID, a meaningful rationale and the smallest reviewed
`maxChangedRatio`. The cap is 15%; current, baseline and diff images are still
uploaded whenever the default 0.5% budget is exceeded. Binding an exception to
the base SHA prevents it from silently weakening later pull requests.

```json
{
  "baselineSha": "0123456789abcdef0123456789abcdef01234567",
  "route": "home",
  "viewport": "mobile",
  "clickupId": "BL-109",
  "rationale": "Reviewed mobile navigation spacing update.",
  "maxChangedRatio": 0.012
}
```

Do not raise the global threshold, use a blanket exception or change a fixture
to make an unexplained regression pass. Remove obsolete approvals after their
base commit is no longer used.

## Adding a route or state

1. Add the route and meaningful states to `visual-tests/fixtures.mjs`.
2. Return deterministic data for every API the route reads. Never use production
   credentials, tokens, cookies or live Meta data.
3. Add state evidence in `visual-tests/quality-gate.spec.mjs` so the test proves
   that loading, empty, error or partial UI was actually rendered.
4. Keep long strings human-readable and adversarial; do not hide overflow with a
   global `overflow-x: hidden` fix.
5. Open a pull request and inspect the uploaded diagnostic set if the gate fails.
6. If a reviewed visual change needs an exception, scope it to the failing route,
   viewport and exact PR base SHA as described above.

Repository policy forbids running the local test suite. The visual gate runs in
GitHub Actions together with the existing unit/integration checks.
