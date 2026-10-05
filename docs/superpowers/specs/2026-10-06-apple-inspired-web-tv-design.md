# Web — Apple-inspired desktop + full responsive + Android TV — Design Spec

Date: 2026-10-06
Status: Approved (B + 10-foot A + breakpoints C + FullPlayer A)
Visual companion: `docs/visual-apple-tv.html`

## 1) Outcome & success criteria
- Goal: make the standalone `/web` app feel like Apple Music desktop (music.apple.com) while keeping our tabs/features, and be fully responsive from phone (320px) to Android TV 10-foot, without touching `miniapp` on `app.*`.
- Success: mobile BottomNav+MiniPlayer unchanged; desktop ≥768 shows a narrow Apple-like sidebar + minimal header (glass kept); TV ≥1600 + pointer:coarse shows large focus ring, big hit targets, horizontal snap rows, safe-area, full-screen player; bundle ≤200KB gzip; RTL with Vazirmatn; i18n/tour/PWA preserved.

## 2) Constraints
- `web/` isolated, `vite-plugin-pwa`, reuse `api/client`, `vercel` budget 200KB, RTL, reduced-motion, lowPerf.
- Nginx already proxies widget (`/telegram-widget.js`, `/embed`, `/auth`, `/css`, `/js`) — no change needed for this spec.
- No new runtime deps if possible; Motion + Router + Query already present.

## 3) Approach (chosen): Incremental Shell
We keep Gate/Shell/DesktopNav/BottomNav in `web/src/App.tsx` and add a thin layout layer that only renders on `md:`+. All grids gain `container-type:inline-size` + `clamp` sizing. TV gets a separate `tv.css` behind `@media (min-width:1600px) and (pointer: coarse)` so phones never pay the cost. 10-foot focus is a tiny `useSpatialNav` hook (roving tabindex + Arrow/Enter/Escape) — no external spatial library.

## 4) Architecture
```
web/src/
  layouts/
    DesktopShell.tsx   # md:block sidebar 220/240 + sticky header 48
    Header.tsx         # search + lang/theme + safe-area
  hooks/
    useSpatialNav.ts   # roving tabindex, Arrow/Enter/Escape, focus-visible ring
  styles/
    tv.css             # @media (1600px + pointer:coarse) overrides
  lib/
    font.ts            # Vazirmatn import + fallbacks
  App.tsx              # Gate/Shell unchanged logic, Shell composes DesktopShell
docs/visual-apple-tv.html  # visual companion (already committed)
```

## 5) Breakpoints & responsive (C)
- Breakpoints: 320 / 360 / 768 / 1024 / 1280 / 1920 (Tailwind sm/md/lg/xl/2xl).
- Grids: `grid-template-columns: repeat(auto-fill, minmax(clamp(110px,18vw,200px), 1fr))`, `gap: clamp(10px,1.2vw,16px)`, font `clamp(12px,1.1vw,18px)`.
- TV layer: overrides card 200px, font 15-18px, hit-target ≥48dp, safe-area `max(16px, 5vw)` + `env(safe-area-inset-*)`, header 64px, sidebar 260px.

## 6) Components & TV navigation (A)
- DesktopShell: sidebar 220px @lg, 240px @xl, no collapse; header 48px (64px on TV); content `max-w-6xl` centered.
- useSpatialNav: container ref, items `[tabindex]`, Arrow moves focus with wrap, Enter/Space clicks, Escape/Back bubbles to close. CSS: `.tv-focus:focus-visible { outline:3px solid #6c5cff; outline-offset:2px; box-shadow:0 0 0 6px rgba(108,92,255,.25); transform:scale(1.02) }`.
- Rows: `overflow-x:auto; scroll-snap-type:x mandatory; scroll-behavior:smooth` on TV, snap per card.

## 7) Player (A) — Full-screen on TV
- Default on TV: full-screen cover behind (blur 24 + dim 0.45), controls 88px row, play button 56px, progress 8px, lyrics line-height 1.8 with 10-foot padding.
- Entry: OK on cover or Play; Exit: Back/Escape. Non-TV FullPlayer unchanged (existing behavior).
- Reduced-motion: TV animations disabled when `prefers-reduced-motion: reduce`.

## 8) Typography
- Persian: Vazirmatn (weights 400/500/700) via `web/src/lib/font` (imported in `main.tsx`), `html[lang=fa] { font-family: "Vazirmatn", system-ui, ... }`. Fallback stack: Vazirmatn, system-ui, -apple-system, Segoe UI.

## 9) Data flow & errors
- No API change. Gate -> Shell -> pages. TV is presentation only. Errors keep existing toasts. Budgets checked by `vite build` (current ~189KB gz).

## 10) Testing
- Manual: Chrome DevTools widths 320/768/1024/1280/1920 + emulated TV 1920x1080 pointer:coarse; keyboard D-pad (Tab/Arrow/Enter/Escape); RTL/LTR; reduced-motion; lowPerf.
- No new automated tests required for this visual step (existing web tests still run).
- Rollout: single PR on `main`, only `web/` + `docs/`; server rebuild via `frontend --profile build` (2 min) + `curl` index hash check.

## 11) Execution order
1. Font wiring + tv.css + useSpatialNav + DesktopShell/Header
2. Shell composition + Home/Library/Discover container queries
3. FullPlayer TV mode + focus polish + final bundle check
