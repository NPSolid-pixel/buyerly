# Unified UI System — implementation plan

Date: 2026-08-29
Branch: `feat/unified-ui-system`

## Goal

Bring every Buyerly page, data view, form, overlay and modal into one calm,
product-grade visual system. Remove the “card inside a card” look, normalize
type and spacing, and preserve the existing API, DOM id and JavaScript action
contracts.

## Design direction

- Quiet neutral canvas with one clear surface per meaningful data region.
- Page hierarchy is expressed with spacing and typography, not nested boxes.
- Dense data remains compact, but primary copy and controls stay readable.
- All pages share one shell width, page header, toolbar and responsive rhythm.
- All dialogs share one overlay, width scale, header/body/footer and mobile
  bottom-sheet behavior.
- Semantic states use semantic colors; brand amber is reserved for identity and
  focused/selected emphasis.

## Work packages

1. Extend semantic tokens for typography, spacing, radii, borders, surfaces,
   page widths and modal sizes.
2. Add shared page, surface, toolbar, table, form, button, badge, empty-state and
   modal primitives in a dedicated UI system stylesheet.
3. Apply shared contracts to Today, Connections, Ad accounts, Automations,
   Efficiency, Action History and Settings.
4. Flatten nested panels in settings, analytics, history and rule-building
   flows; keep a single containing surface around each data region.
5. Normalize all modal families, including record/detail overlays and mobile
   sheets, without changing their ids or handlers.
6. Verify static contracts, CSS parsing, JavaScript parsing, desktop widths and
   the mobile breakpoints. Project policy keeps the Python test suite in CI.

## Definition of done

- All authenticated top-level pages use the same page gutter and max-width.
- Page titles, descriptions, labels, body text and metadata follow one type
  scale; primary UI copy is never rendered as 10–12px microtext.
- Tables are not wrapped in decorative cards inside other cards.
- Cards are used only for standalone entities or metrics; grouped settings use
  divided sections inside one surface.
- Buttons, inputs, tabs and badges have consistent heights, radii and focus.
- Every modal has consistent spacing, close action, footer treatment and safe
  mobile behavior.
- No page-level horizontal overflow at 1440, 1024, 768 or 390px.
- Existing ids, `data-*` hooks, inline handlers and API payload contracts remain
  intact.
- Presentation-specific inline styles are removed from production HTML; runtime
  styles are limited to genuinely computed column widths and user colors.
