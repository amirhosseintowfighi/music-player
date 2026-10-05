# Plan: Apple-inspired Web (B) + Full Responsive + Android TV 10-foot (A+C) — Vazirmatn

Date: 2026-10-06
Spec: `docs/superpowers/specs/2026-10-06-apple-inspired-web-tv-design.md` + `docs/visual-apple-tv.html`

## Context
Standalone `/web` must look like Apple Music desktop on ≥768 while mobile BottomNav+MiniPlayer stays untouched, and be 10-foot operable on Android TV (D-pad, focus ring, safe-area, large hit-targets). Font Persian must be Vazirmatn. No new runtime deps, budget 200KB gzip, PWA/tour/i18n preserved. Incremental Shell approach.

## Tasks (bite-sized, TDD where applicable, commit per task)

### 1. Vazirmatn font wiring
- Files: `web/src/lib/font.ts` (new), `web/src/main.tsx`, `web/src/design/tokens.css`, `web/index.html` (preconnect if needed)
- Install: `npm i` none — use Google Fonts via `@import` or `link` with `display=swap`, weights 400/500/700, subset latin+arabic. Fallback: `system-ui, -apple-system, Segoe UI`.
- Rule: `html[lang=fa] { font-family: "Vazirmatn", system-ui, ... }`, `html[lang=en]` keeps system stack or Vazirmatn latin if desired — keep Vazirmatn for fa only to avoid CLS.
- Verify: `web/dist/assets` contains font not inlined beyond budget — use external link, not bundled woff2 unless needed. `npm run build` passes size check.

### 2. TV stylesheet (isolated)
- File: `web/src/styles/tv.css`
- Content: `@media (min-width:1600px) and (pointer: coarse)` overrides: card `min-width:200px height:200px`, font `clamp(14px,1.1vw,18px)`, header 64px, sidebar 260px, safe-area `padding: max(16px,5vw); padding: env(safe-area-inset-*)`, rows `scroll-snap-type:x mandatory`. `@media (prefers-reduced-motion: reduce)` disables motion. All selectors prefixed `.tv` or via media query only — zero cost on mobile.
- Import in `web/src/main.tsx` after tokens.css.

### 3. useSpatialNav hook (roving tabindex, no dep)
- File: `web/src/hooks/useSpatialNav.ts`
- API: `function useSpatialNav<T extends HTMLElement>(opts?: { wrap?: boolean }) => { containerRef: RefObject<T>, register?: ... }` — simple: containerRef + onKeyDown handler: ArrowUp/Down/Left/Right moves focus to next `[data-focusable]` or `[tabindex]`, Enter/Space triggers click, Escape bubbles CustomEvent `tv:back`.
- CSS helper: `.tv-focus:focus-visible { outline:3px solid #6c5cff; outline-offset:2px; box-shadow:0 0 0 6px rgba(108,92,255,.25); transform:scale(1.02); transition: transform .16s; }`
- Tests: unit test `web/src/hooks/useSpatialNav.test.ts` — mount 3 buttons, ArrowRight moves focus, Enter clicks.

### 4. DesktopShell + Header (md+ only)
- Files: `web/src/layouts/DesktopShell.tsx`, `web/src/layouts/Header.tsx`
- DesktopShell: `aside.hidden md:block` with `w-[220px] lg:w-[240px]` (tv: `w-[260px]` via tv.css), `sticky top-0 h-screen`, nav from `TABS_DESKTOP`, active state `bg-[var(--fill)]`. Main column `container-type:inline-size` + `max-w-6xl mx-auto`.
- Header: `h-12 tv:h-16`, search input + lang toggle + theme, `env(safe-area-inset-top)` padding. Only rendered inside DesktopShell, hidden on mobile.
- Verify: mobile ≤767 renders no aside/header (check DOM), desktop ≥1024 shows sidebar.

### 5. Shell composition + grids (container queries + clamp)
- File: `web/src/App.tsx` (Shell), `web/src/screens/Home.tsx`, `Library.tsx`, `Discover.tsx`, `components/ui.tsx` (if grid helpers)
- Change: Shell wraps content with DesktopShell; BottomNav/MiniPlayer stay outside and hidden on md. Grids: `grid-template-columns: repeat(auto-fill, minmax(clamp(110px,18vw,200px),1fr))`, `gap: clamp(10px,1.2vw,16px)`, container queries for 3→6 cols.
- Rows: horizontal `overflow-x:auto scroll-snap-type` with `data-focusable` for spatial nav.
- Verify: `npm run build` ≤200KB, visual breakpoints 320/768/1024/1280/1920 manual.

### 6. FullPlayer TV mode (A)
- File: `web/src/components/FullPlayer.tsx`, `web/src/styles/tv.css` extension
- Logic: detect TV via `matchMedia('(min-width:1600px) and (pointer: coarse)')` or prop; when matched: cover backdrop `blur(24px) + dim 0.45`, controls row `h-[88px] gap-6`, play button `56px`, progress `h-2`, lyrics `line-height:1.8` with `padding: 5vw`. Entry: OK on cover triggers open; Exit: Back/Escape closes.
- Keep existing FullPlayer behavior for non-TV.
- Verify: no regression on mobile, reduced-motion disables TV animations.

### 7. Build, visual companion, deploy check
- Run `npm --prefix web run build && node scripts/check-size.mjs`, ensure `initial bundle ≤200KB`. Update `docs/visual-apple-tv.html` screenshots if needed.
- Commit per task, push main. Server verify: `docker compose -f infra/compose/solo.yml --env-file .env --profile build run --rm frontend` + `curl` hash.

## Dependencies
- No new npm deps. Vazirmatn via Google Fonts link.

## Risks
- Font CLS — mitigate `display=swap` + preload.
- Spatial nav focus traps — roving tabindex must wrap correctly.

## Execution order
1 → 2 → 3 → 4 → 5 → 6 → 7
