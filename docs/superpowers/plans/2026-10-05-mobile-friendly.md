# Mobile-friendly web app, page by page

Date: 2026-10-05. Branch: `mobile-friendly`. Owner request: make every page of
the website usable on a phone, with every button and link working exactly as
it does on desktop.

Spec: this file plus `docs/DESIGN.md` §11.6-11.9. There is no separate spec
document. §11.9 currently says `/research` is desktop-only. The owner's
request of 2026-10-05 overrides that line, and Task 1 rewrites it.

## Context

The app is Next.js 15 (App Router) under `web/`. All styling lives in one
file, `web/app/globals.css` (2,548 lines), with five `@media (max-width:
720px)` blocks and one `@media (max-width: 900px)` block already present.
Components are in `web/components/`, pages in `web/app/`.

Routes:

| route | page file | main components |
|---|---|---|
| `/` | `app/page.tsx`, `app/loading.tsx` | `Screener`, `DatePicker`, `OpenSelected`, `TickerSearch`, `EventRows`, `InferenceCell`, `PredictButton`, `InferenceModal` |
| `/ticker/[sym]` | `app/ticker/[sym]/page.tsx`, `loading.tsx` | `Ticker`, `TickerChart`, `LivePrice`, `EventRows`, `TickerSearch` |
| `/research` | `app/research/page.tsx` | `Research` |
| `/model` | `app/model/page.tsx` | `Reliability` |
| `/chat` | `app/chat/page.tsx` | `Chat` |

A dev server is already running at `http://localhost:3100` with auth
disabled, reading the workstation research database. Do not start another
one and do not stop this one. It hot-reloads on save.

## Global Constraints

These bind every task.

1. **Desktop is frozen.** At viewport widths of 721px and wider, layout,
   content and behaviour must not change. Put every new CSS rule inside a
   `@media (max-width: 720px)` block (or a narrower max-width query). A JSX
   change is allowed only when CSS cannot do the job, and it must leave the
   desktop DOM rendering and behaviour identical. Prove it with before and
   after screenshots at 1440x900.
2. **Function is preserved 1:1.** Every link, button, input, select, sort
   control, modal, hover readout and keyboard interaction reachable on
   desktop must be reachable and produce the same result on a 390px wide
   touch screen. If desktop reaches something by hover or through an
   element hidden on mobile (for example `table.screen thead` is
   `display: none` under 720px, which hides the sortable headers), add a
   mobile path to the same function. Never remove a function to make a
   layout fit.
3. **No horizontal page scroll.** At 390x844 and 360x740,
   `document.documentElement.scrollWidth <= window.innerWidth` on every
   route and in every state you can reach. A wide table may scroll inside
   its own container. The page body may not.
4. **Touch targets.** Under 720px every interactive control has a hit area
   at least 36px tall and 36px wide, with at least 6px between adjacent
   targets. Text inputs, selects and textareas use `font-size: 16px` or
   larger under 720px, so iOS Safari does not zoom on focus.
5. **Design direction is pinned** (`DESIGN.md` §11.6-11.9, tokens at the top
   of `globals.css`): dense instrument panel, monospace numerals, dark
   ground, colour carries meaning only, five states per data component
   (loading, empty, suppressed, stale, error). Reuse the existing tokens.
   Add no new colours, no new fonts, no decoration.
6. **No new dependencies.** `web/package.json` and `package-lock.json` stay
   untouched. Verify in the browser with the Playwright MCP tools
   (`mcp__plugin_playwright_playwright__*`), loaded through ToolSearch.
7. **No data-layer changes.** Do not edit `web/lib/`, `web/app/api/`,
   `web/middleware.ts`, any Python, or any migration. Do not write to any
   database. A client component must not import a function from a module
   that imports `./db` (`tests/boundary.test.ts` checks this).
8. **Gates per task**, run from `web/`:
   `"/c/Program Files/nodejs/npx" tsc --noEmit` and
   `"/c/Program Files/nodejs/npx" vitest run`. Both must pass. Do **not**
   run `next build` (it rewrites `.next/` under the live dev server and
   breaks it). Never run `pytest` in any form. The shell is Git Bash with no
   user PATH, so use full paths for `node`/`npx`.
9. **Commits.** One or more commits per task on `mobile-friendly`, plain
   imperative subject lines. No `Co-Authored-By` trailer, no "Generated
   with" line, no mention of Claude or any AI tool anywhere in the message.
   The author is the configured git user and nobody else. Do not push. Do
   not touch the Pi or `wivie`.
10. **Comment style.** `globals.css` and the components carry explanatory
    comments stating why a rule exists. Match that density and tone for new
    rules.

## Verification protocol (every page task)

Do this in the browser with Playwright, and record the results in the task
report.

1. **Inventory first, before editing.** At 1440x900, load the route and list
   every interactive element: each link (with its href), button, input,
   select, sortable header, hover readout, modal trigger, keyboard
   shortcut. For each, record what it does (resulting URL or DOM change).
   Take a desktop baseline screenshot.
2. **Baseline mobile.** Resize to 390x844, screenshot, and record what
   overflows, clips, overlaps or is unreachable.
3. **Implement.**
4. **Mobile pass at 390x844 and 360x740.** For every inventory item:
   confirm it is visible, not clipped, not covered by another element
   (Playwright's click fails on a covered element; do not use
   `force: true`), meets the touch-target rule, and produces the same
   result as desktop. Record pass or fail per item in a table. Assert the
   no-horizontal-scroll rule in each reachable state (default, modal open,
   menu open, empty state, error state where reachable).
5. **Desktop regression at 1440x900.** Screenshot and compare with the
   baseline. Re-run a sample of the inventory.
6. **Console.** No new console errors on the route.

Save screenshots under the task workspace given in the dispatch, never in
the repository.

## Task 1: Shared foundation

Files: `web/app/layout.tsx`, `web/app/globals.css`, `web/app/loading.tsx`,
`docs/DESIGN.md`.

- Export an explicit `viewport` from `app/layout.tsx` (`width:
  "device-width"`, `initialScale: 1`, `themeColor` equal to the `--ground`
  token value). Do not disable user zoom.
- In `globals.css`, add one clearly labelled shared mobile block for rules
  every page needs: page gutter for `.wrap` under 720px, `overflow-wrap`
  for long unbroken strings, `-webkit-text-size-adjust: 100%`, safe-area
  padding (`env(safe-area-inset-*)`), `touch-action: manipulation` on
  controls, and the 16px form-control font size from constraint 4. Keep it
  to rules that are shared. Page-specific rules belong to later tasks.
- Check the shared header pattern (`.rs-head`, `.back`) used by `/chat` and
  `/research` and the top bar of the screener for wrap behaviour at 360px.
  Fix only what is shared.
- `app/loading.tsx` (the root skeleton) must not overflow at 360px.
- Rewrite `docs/DESIGN.md` §11.9 so it states the new scope: every route is
  in mobile scope as of 2026-10-05 by owner request, wide tables scroll
  inside their own container, and desktop layout is unchanged above 720px.
  Keep the section short and keep the reason for the change in it.
- Verification: the protocol's steps 2, 4 (overflow assertion only), 5 and
  6 on all five routes (`/`, `/ticker/NVDA`, `/research`, `/model`,
  `/chat`). A route still overflowing for a page-specific reason is
  expected at this point. List each such case in the report for the later
  tasks.

## Task 2: Screener (`/`)

Files: `web/components/Screener.tsx`, `DatePicker.tsx`, `OpenSelected.tsx`,
`TickerSearch.tsx`, `EventRows.tsx`, `InferenceCell.tsx`,
`PredictButton.tsx`, `InferenceModal.tsx`, `web/app/page.tsx`,
`web/app/globals.css`.

- Full verification protocol on `/`, including a past date with rows (use
  the date controls to find one) and an empty date.
- Known gaps to resolve: the sortable column headers are hidden under 720px,
  so sorting needs a mobile control that drives the same sort state and
  URL. The card layout hides `thead`, so each cell needs a visible label on
  mobile where the value alone is ambiguous (check what exists first). The
  date picker, the previous/next/latest links, the ticker search menu, the
  multi-select "open selected" flow, the predict button and the inference
  modal all need a full pass.
- The inference modal on a phone: fits the viewport, scrolls inside itself,
  closes by its close control, by Escape and by whatever else closes it on
  desktop, and locks nothing after closing.
- Invariant 8 of the project: any probability shown keeps `n_eff` and its
  interval reachable. Where desktop shows `n_eff` in hover text, give touch
  users a path to the same text.

## Task 3: Ticker (`/ticker/[sym]`)

Files: `web/components/Ticker.tsx`, `TickerChart.tsx`, `LivePrice.tsx`,
`EventRows.tsx` (ticker usage only), `web/app/ticker/[sym]/page.tsx`,
`web/app/ticker/[sym]/loading.tsx`, `web/app/globals.css`.

- Full verification protocol on `/ticker/NVDA` and `/ticker/QQQ` (an ETF
  with no sector), plus an unknown symbol for the error or empty state.
- The chart (`lightweight-charts`): resizes to the viewport width, keeps
  its range buttons, legend, crosshair readouts and caption usable, and
  supports touch pan and pinch without trapping the page's vertical scroll
  in a way that makes the content below unreachable. Verify the range
  links (`?range=`), the history `?all=1` link, the back link and the
  search.
- The state rail, price panel and history table follow §11.9: price panel
  first, state rail below.
- The loading skeleton matches the mobile layout and does not overflow.

## Task 4: Research (`/research`)

Files: `web/components/Research.tsx`, `web/app/research/page.tsx`,
`web/app/globals.css`.

- Remove the `@media (max-width: 900px)` "designed for a desktop screen"
  banner. Keep horizontal scroll inside each table's own container, with
  the first column readable, and make every other block (headings, prose,
  notes, controls) reflow to the viewport.
- A table wrapped in a scroll container needs a keyboard-focusable
  container and a visible cue that it scrolls.
- Full verification protocol on `/research`, including every control the
  page has (filters, toggles, sort, links).

## Task 5: Model (`/model`)

Files: `web/components/Reliability.tsx`, `web/app/model/page.tsx`,
`web/app/globals.css`.

- Full verification protocol on `/model`. Charts and tables fit or scroll
  inside their own container. Axis labels and numerals stay legible at
  360px.

## Task 6: Chat (`/chat`)

Files: `web/components/Chat.tsx`, `web/app/chat/page.tsx`,
`web/app/globals.css`.

- Full verification protocol on `/chat`. The composer stays reachable with
  an on-screen keyboard (use `dvh` units or an equivalent, not a fixed
  `100vh`), the message list scrolls, rendered markdown (tables, code
  blocks, long tickers) stays inside the viewport or scrolls inside its
  own block, and every control works.
- **Send at most two real messages in total.** Each one spends API tokens
  and needs the MCP server, which is probably not running locally. An
  error reply is an acceptable result for the layout check, and the error
  state must itself fit the viewport. Verify the submit path (button and
  Enter key) fires the same request as desktop.
