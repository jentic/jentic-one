# Toolkit Detail Sheet — Implementation Plan

Branch: `ia/credentials-on-monitor`

## Goal

Convert the toolkit detail view (`/toolkits/:id`, currently a ~900-line full page)
into a reusable, URL-driven **slide-over detail sheet** — the same pattern used by
`CredentialEditSheet` — that can be opened from the toolkits list and the Workspace
page, while **keeping the route working** for deep links and the browser back button.
Also enhance the clunky internals (cramped key rows, inline kill-switch row jammed
into the keys card, accordion permissions, settings hidden behind a separate dialog).

## Decisions (locked with user)

- **Route**: keep `/toolkits/:id` route working AND add the sheet. Safest; preserves
  the large existing `ToolkitDetailPage.test.tsx` suite.
- **Entry points**: toolkits list (`ToolkitsPage` / `ToolkitCard`) + a toolkits
  affordance on the Workspace page.
- **Width**: ~640px (`sm:w-[640px]`), wider than credentials' 480px because toolkit
  detail is denser (keys + credentials + permissions + settings).

## Existing patterns to reuse (verified)

- `ui/src/components/ui/SheetPrimitive.tsx` — accessible right-side slide-over:
  focus trap, Escape/backdrop close, 300ms animation, sticky-while-exiting via
  `onAfterClose`. `side="right"`, panel default `sm:w-[480px]`; pass `className`
  to widen.
- `ui/src/hooks/useCredentialEditSheet.ts` — URL-driven open state on `?edit=<id>`
  with `stickyId` (survives close animation), `openSheet`/`closeSheet`/`clearSticky`.
  Its own docstring already names `ToolkitDetailPage` as a future host.
- `ui/src/components/credentials/CredentialEditSheet.tsx` — the structural model:
  `SheetPrimitive` → `flex h-full flex-col` → pinned `<header>` + scrollable body.
- `ui/src/components/discovery/ApiDetailSheetHeader.tsx` — header idiom:
  `VendorIcon` + title + `code`/`CopyButton` + status pills + close button,
  `sticky top-0 z-10 border-b bg-card`.

## Architecture: extract body, host in two shells

1. **`ToolkitDetailBody`** (NEW, `ui/src/components/toolkits/ToolkitDetailBody.tsx`)
   - All detail content driven by props: `toolkitId: string`, `layout: 'page' | 'sheet'`,
     and an `onRequestClose?: () => void` (sheet supplies it; page passes navigate-back).
   - Owns the queries + mutations currently in `ToolkitDetailPage` (toolkit, keys,
     access-requests; create/revoke key, unbind, update, delete, killswitch).
   - Keeps the SAME visible text/roles the tests assert on (see "Test safety").
   - `layout` only changes chrome: `sheet` => no `BackButton`, no page `<h1>` (the
     sheet header owns identity), full-height flex with internal scroll; `page` =>
     keeps `BackButton` + heading block as today.

2. **`ToolkitDetailSheet`** (NEW, `ui/src/components/toolkits/ToolkitDetailSheet.tsx`)
   - `SheetPrimitive side="right"` with `className="sm:w-[640px] sm:max-w-[92vw]"`.
   - Props mirror `CredentialEditSheet`: `toolkitId | null`, `open`, `onClose`,
     `onAfterClose?`.
   - Pinned header (VendorIcon + name + ID/copy + status pill + close), then
     `<ToolkitDetailBody layout="sheet" toolkitId=… onRequestClose={onClose} />`.

3. **`ToolkitDetailPage`** (KEEP, `ui/src/pages/ToolkitDetailPage.tsx`)
   - Becomes a thin wrapper: `PageShell` + `<ToolkitDetailBody layout="page" … />`.
   - Reads `:id` from the route, navigates `/toolkits` on close/delete.

4. **`useToolkitDetailSheet`** (NEW, `ui/src/hooks/useToolkitDetailSheet.ts`)
   - Copy of `useCredentialEditSheet` keyed on `?toolkit=<id>` (configurable param).

5. **Entry points**
   - `ToolkitsPage`: mount `useToolkitDetailSheet` + `<ToolkitDetailSheet>`; cards
     open the sheet instead of navigating. `ToolkitCard` currently renders an
     `AppLink href="/toolkits/:id"` — add an optional `onOpen?(id)` so the card calls
     it (preventDefault) when provided, falling back to the href for deep-link/SSR.
   - Workspace: add a lightweight toolkits affordance that opens the same sheet.
     Lowest-risk option: a small "Toolkits" section/row using existing
     `WorkspaceView` toolkit data, or reuse `ToolkitCard`. Keep additive; do not
     disturb API/workflow grids or their tests.

## Internal enhancements (fix "clunky")

- **Header**: identity via `VendorIcon` (seeded by name) + name + `ID + CopyButton`
  + a single status pill (Suspended / simulate / Active).
- **Kill switch**: lift out of the keys-card inline row into a header/footer-level
  control with `ConfirmInline`; keep the exact button labels ("Kill switch",
  "Kill access", "Restore") the tests match.
- **Keys**: consistent `bg-card` container, tighter rows, cleaner inline create form.
- **Credentials**: `VendorIcon` rows (already added on the page), permissions panel
  tidied (still inline-expand, but calmer surface).
- **Settings + delete**: surface within the sheet body (a "Settings" section /
  footer danger zone) instead of a separate modal — but KEEP a `Dialog` titled
  "Toolkit Settings" reachable in `page` layout so the existing delete-on-500 test
  (which clicks "Settings" → "Delete Toolkit" → "Delete Forever") keeps passing.
  Simplest: keep the Settings `Dialog` in the shared body for BOTH layouts initially;
  iterate later if we want it inline. (Avoids test churn.)
- **Motion**: subtle `framer-motion` stagger on sections, matching the toolkits list.

## Test safety (must stay green)

`ui/src/__tests__/pages/ToolkitDetailPage.test.tsx` renders `<ToolkitDetailPage />`
directly and asserts on:
- "Test Toolkit" / "A test toolkit"; "Loading toolkit"; "Toolkit not found" + Back.
- "No keys yet"; key label + `prefix...`; "Create API Key"/"Generate"/"Generating...".
- "Bound Credentials" rows ("Stripe Token", "stripe.com").
- "Pending access request" badge.
- Settings button hidden for `default`; Settings → Delete flow.
- Kill switch confirm → "block all api access" → "kill access".
- Unbind via `ConfirmInline`.
- axe: no critical/serious violations.

=> All this lives in `ToolkitDetailBody` with identical text/roles. Page wrapper keeps
the route + back button. Don't rename labels. Run this suite after refactor.

New test: `ToolkitDetailSheet.test.tsx` — opens via `?toolkit=`, shows name, closes on
Escape/backdrop, renders keys/credentials.

## Revisions after subagent review (IMPORTANT)

- **`?edit=` collision**: the body currently mounts `useCredentialEditSheet()` + a
  nested `CredentialEditSheet` (on `?edit=`). Hosting the body inside the toolkit
  sheet on `/toolkits?toolkit=X` would let clicking a bound credential stack a 2nd
  `SheetPrimitive` (`?edit=Y`) on top — two focus traps. **Decision**: in `sheet`
  layout, the credential-name click navigates to `/credentials?edit=Y` (or just
  shows the credential read-only). Simplest faithful fix: keep the nested
  `CredentialEditSheet` ONLY in `page` layout; in `sheet` layout the credential row's
  name is not a sheet-opener (it stays a plain label / links to `/credentials`).
- **Header idiom**: mirror `ApiDetailSheetHeader` exactly (flex-column shell, NOT
  `sticky`). Use `border-border/60`, `bg-card`, `VendorIcon`, `h2` + `code` +
  `CopyButton` for the ID, status pill using **`ToolkitCard`'s** tokens/labels
  (`SUSPENDED` / `simulate`). Wire `ariaLabelledBy` to the `<h2 id>`.
- **Settings**: ship **dialog-only** (keep the existing "Toolkit Settings" `Dialog`
  in the shared body for BOTH layouts, unchanged). Do NOT also add an inline settings
  section — that would create two delete entry points. Defer inline settings.
- **Kill switch**: relocate to a header/footer-level control LAST (enhancement step),
  and keep the literal "Toolkit Suspended" string + "Kill switch"/"Kill Access"/
  "Restore" labels the tests match. Keep keys-card border state coherent after move.
- **Loading / not-found**: keep these returns positioned so `page` layout never shows
  TWO "Back"-named buttons (the BackButton + EmptyState "Back") at once — the
  not-found test does `getByRole('button', {name:/back/i})` (throws on 2 matches).
- **Hook**: prefer a thin wrapper around `useCredentialEditSheet({ paramName:
  'toolkit' })` over copy-pasting the sticky/animation logic. Expose
  `toolkitId/stickyId/open/openSheet/closeSheet/clearSticky`.
- **Back button**: open the sheet with a real history entry (`replace: false`) so the
  browser Back button closes it (the credential hook uses `replace:true`; the toolkit
  wrapper should override to push). Re-confirm deep-link/refresh still fine.
- **Workspace**: add a NEW additive `<section data-testid="workspace-section-toolkits">`
  reusing `ToolkitCard` + the new `onOpen`, fed by WIDENING the existing
  `['workspace','toolkits']` query (retain disabled/simulate/counts) — no new network
  call, not the stats strip (stats strip is intentionally non-clickable). Update
  `PageHelp` copy to mention "click a toolkit tile to open its detail sheet".
- **Width**: `cn()`/tailwind-merge lets a later `className` win over the primitive's
  `sm:w-[480px]`. Use `sm:w-[640px] sm:max-w-[92vw]`. Verify the credentials-section
  3-button action row + keys header don't wrap awkwardly at ~600px usable.
- **Motion**: do NOT double-animate (sheet 300ms slide + per-section stagger). At most
  a single subtle body fade; prefer none.
- **Polling**: list page + open sheet DO co-exist → ~5 polling queries. Acceptable;
  consider relaxing the sheet body's `refetchInterval` when `layout==='sheet'`.

## Step order (revised — each step keeps the suite green)

1. **Pure extraction, zero behavior change**: move the entire render + all hooks into
   `ToolkitDetailBody`, `layout="page"` hard-wired. Wrapper passes `toolkitId={id!}`,
   `onRequestClose={() => navigate('/toolkits')}`. Keep loading/not-found, Settings
   Dialog, kill-switch row, nested `CredentialEditSheet` exactly as-is.
   Run full toolkit suite → MUST be green.
2. Introduce `layout` prop; gate only `BackButton` + heading block (and the nested
   `CredentialEditSheet`) on `'page'`. Re-run suite → green.
3. Add `useToolkitDetailSheet` (`?toolkit=`, push history) + `ToolkitDetailSheet`
   (`ApiDetailSheetHeader`-style header + `<ToolkitDetailBody layout="sheet">`).
4. Wire `ToolkitsPage`/`ToolkitCard` (`onOpen` + href fallback + `replace:false`).
5. Workspace toolkits section (additive) opening the same sheet; update PageHelp copy.
6. Enhancements pass LAST, one concern per commit (header identity/CopyButton,
   kill-switch relocation, keys/credentials polish). Re-run suite after EACH.
7. Validation triad: `npm run lint:fix` → `npm run build` →
   `PLAYWRIGHT_BROWSERS_PATH=0 npm run test:run`. Add `ToolkitDetailSheet.test.tsx`
   (open via `?toolkit=`, Escape/backdrop close, renders name/keys; axe).

## Watchpoints

- Keep all `data-testid`s and visible labels/roles intact.
- Don't regress `WorkspaceView`/`WorkspacePage` tests when adding the toolkits section.
- Verify `tailwind-merge` dedupes `w-[480px]`→`w-[640px]` and `max-w-[90vw]`→`[92vw]`.
- Confirm `AppLink` supports `onClick` (for `onOpen` preventDefault) without tripping
  the repo's `no-restricted-syntax` anchor/button lint.
