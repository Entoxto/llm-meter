# Research workflow — design QA

Date: 2026-09-26. **final result: passed** (research desktop interface).

## Visual truth and captures

- Approved sources: `docs/research-redesign/screens/01-experiment-builder.png`,
  `02-work-plan.png`, `03-results-and-reports.png` (1586×992 pixels, approximately 16:10).
- Qt captures: `docs/research-redesign/qa/experiment-ui-{builder,plan,results}-1280.png`.
  Native Qt Quick viewport 1280×800 logical pixels, captured at density 1.
- Additional captures: `experiment-ui-conditions-1280.png` and
  `experiment-ui-context-menu-1280.png` in the same directory.
- Sources and corresponding rendered screens were opened together for comparison.
  Layout is compared at the same 16:10 ratio, normalizing the source by ~0.807.
  Qt captures use explicit fictional fixture data; they are not benchmark evidence.

## Findings resolved

1. P1: QVariant lists initially produced empty parameter chips. Fixed list handling.
2. P2: bottom method text and primary action touched card borders. Increased card
   height and reduced internal spacing; both retain at least 14 px bottom padding.
3. P2: results footer and last rows fell below the application status bar.
   Moved task access to the tab row, resized the table, added table scrollbars.
   The 12-configuration fixture fits fully; larger experiments scroll inside the table.
4. P2: filled tab buttons and tiny default checkboxes differed from the approved
   hierarchy. Tabs now use an underline, checked controls use existing SVG icons.
5. P2: a check glyph rendered as an empty box. Removed the unsupported glyph.
6. P1: stale preview and implicit first-row selection could disagree with actions.
   Preview uses revision guards; result selection is synchronized explicitly.

## Required fidelity surfaces

- Typography: existing Segoe UI; clear title/section/body hierarchy. Denser table
  text is intentional for the user's 2K monitor with scaling. No clipped labels.
- Spacing/layout: two-column constructor, plan table + inspector, results table +
  inspector follow the chosen concepts. No overlap; primary controls remain visible.
- Color/tokens: existing navy panels, borders, violet selection/action, green proof,
  red errors. Uses the application's Theme rather than a separate theme.
- Assets: existing application mark and licensed SVG icon set. No model-specific art.
- Copy/content: explicit comparison axes and common conditions; exact new-check counts;
  copy outcome/model summary/full history/selected result; no “reports by filters”.

## Interaction checks

Preview and launch share the Cartesian planner. Tests cover 48 combinations,
partial evidence reuse in both directions, cancellation/resume, changed preview,
independent drafts, report snapshots and applying settings without starting a model.
The installed user database was checked through a backup only: Qwen 32K/Q4 Off
is reused while Draft 2 requires a new check. No GPU workload was started.

## Remaining scope

- P3: a future pass can add sortable result columns and richer compact provenance icons.
- A full GPU experiment through the redesigned installed UI is not claimed here.
- OpenCode Web is a separate integration change. Real browser launch from the updated
  installed Studio is deferred while the user has an active process; this QA pass
  does not certify that end-to-end scenario.
