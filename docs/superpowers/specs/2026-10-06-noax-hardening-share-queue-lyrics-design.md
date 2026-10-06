# Noax — Fase 1: Hardening + Share + Queue + Lyrics — Design Spec

Date: 2026-10-06
Status: Approved (Path A — Fase 1)
Scope: Fase 1 only — Critical debt + Quick Wins. Fases 2-4 are separate specs.
Prior art: `2026-10-06-apple-inspired-web-tv-design.md` (Desktop B + TV A + breakpoints C) stays in force.

## 1) Outcome & success criteria

- Goal: make share actually open the system sheet (Instagram/WhatsApp/Telegram...), not just `Image saved`; fix critical debt that blocks future work; ship two small product wins (lyrics, queue reorder) without breaking budget, brand, or layout.
- Success (all must hold):
  - `Share` on a track opens the OS sheet where available; inside Telegram WebView (where `canShare(files)` is false) it opens an in-app ShareSheet with Telegram/WhatsApp/Copy/Download (+ Story hint). No more dead `Image saved`-only path.
  - `web` and `miniapp` stay `Noax | نوکس`, full-width web shell (`w-full`, no `max-w-6xl` gutters), TV 10-foot intact.
  - Icons built by `npm run icons` (no ad-hoc `sharp` install/uninstall); `favicon.ico` is real multi-size; `og` has `webp` variant ~120KB with `png` fallback and `og:image:{width,height}`.
  - Vazirmatn self-hosted (`public/fonts/*.woff2` + `@font-face`, `font-display: swap`); no `@import https://fonts.googleapis.com`.
  - CORS/CSP origins from one truth (no manual drift between nginx template and vite).
  - Queue `rest` and `manual` are both reorderable with correct `index` bookkeeping; `Reorder` already in bundle.
  - Lyrics MVP: synced (LRC) where available (LRCLIB proxied + 24h Redis), plain fallback, auto-scroll to current line, works with `player.position`.
  - `npm run build` green in both apps, gzip ≤ 200KB, no `%VITE_API_URL%` warning, existing tests + new unit tests pass.
  - Manual QA: Android Telegram WebView, iOS Telegram, Chrome desktop — share sheet, queue drag, lyrics scroll, RTL/LTR, reduced-motion.

## 2) Constraints

- No new runtime deps if avoidable. `framer-motion/Reorder` already bundled; `sharp` stays dev-only.
- Keep `web/` and `miniapp` deployable via existing `infra/compose/solo.yml --profile build frontend` + `nginx recreate`.
- Do not touch `miniapp` API contract or `backend` auth; lyrics proxy is the only new endpoint.
- Budget: `dist/assets/*.js` gzip ≤ 200KB (checked by `scripts/check-size.mjs`).
- Brand: `Noax` / `نوکس` bilingual, `manifest.webmanifest` already `Noax`.

## 3) Approach (chosen)

**Incremental hardening** — no full monorepo in Fase 1. One PR, touched areas only:

```
scripts/generate-icons.mjs        # replaces ad-hoc sharp use
web/public/fonts/Vazirmatn-*.woff2
web/src/design/tokens.css         # @font-face, remove googleapis import
web/src/lib/share.ts              # new: tiered share (files → url → sheet)
web/src/components/ShareSheet.tsx # new: Telegram/WA/Copy/Download/Story
web/src/lib/shareCard.ts          # kept, called by share.ts with real coverSrc
web/src/components/TrackActions.tsx  # pass hiRes cover, use share.ts
web/src/components/FullPlayer.tsx    # same + queue reorder for `rest`
web/src/store/player.ts           # reorderQueue(from,to) with index fix
backend/app/api/routers/lyrics.py # new: GET /v1/lyrics/:trackId → LRCLIB proxy
web/src/components/Lyrics.tsx     # synced highlight + plain fallback
infra/nginx/solo.conf.template    # real favicon.og handling already fine
shared/config/cors.ts or infra/env.cors.ts  # single truth for origins
```

Why not full monorepo now: Fase 1 must ship in ~1 week and stay diff-small. Full `pnpm-workspace + packages/ui` is Fase 4.

## 4) Architecture

### 4.1 Shared UI (light)
- Create `packages/shared-ui/` as an internal npm package (or `web/src/shared/` aliased) that exports `Glass`, `Cover`, `Sheet`, `format`, `share`. In Fase 1 only wire `share` through it; migrate `ui.tsx/tokens.css` fully in Fase 4. This removes the next duplication without a big move.

### 4.2 Icons & assets
- `scripts/generate-icons.mjs` (Node ESM, `sharp` devDep) reads `web/public/brand/noax.jpg` and writes:
  `web/public/icons/icon-*.png`, `web/public/icons/apple-touch-icon.png`, `web/public/favicon.ico` (multi-size 16/32/48 via `sharp` + `png-to-ico` or manual ICO packing), `web/public/og.png` + `web/public/og.webp` (1200×630, ~120KB), and mirrors to `miniapp/public/**`. `package.json` adds `"icons": "node scripts/generate-icons.mjs"`.

### 4.3 Share (the bug fix)
- `lib/share.ts`:
  ```ts
  export type ShareOutcome = 'shared' | 'copied' | 'saved' | 'sheet';
  export async function shareTrack(track: Track, coverSrc: string|undef, t: T): Promise<ShareOutcome>
  // 1) try navigator.share({files:[file]}) if canShare
  // 2) else try navigator.share({title, text, url: t.me link})
  // 3) else return 'sheet' so caller opens ShareSheet
  ```
- `components/ShareSheet.tsx` — `Sheet` with rows: `Telegram (t.me/share/url?url=)`, `WhatsApp (wa.me/?text=)`, `Copy Link (clipboard)`, `Download card (shareCard → download)`, hint `share.storyHint` for Instagram Story (file saved, open Instagram). Uses existing `Sheet` + `haptic`.
- Callers: `TrackActions` `share.card` action and `FullPlayer` share `Glass` both call `shareTrack` with `hiRes(thumbs[track.id]) ?? thumbs[track.id]` (not `undefined`) and handle outcomes (`shared/copied/saved → toast`, `sheet → setShareSheetOpen(true)`).

### 4.4 Lyrics
- Backend: `GET /v1/lyrics/{trackId}` — loads `Track` by id, calls `https://lrclib.net/api/get?artist_name=&track_name=&duration=` (and `api/search` fallback), 24h Redis cache key `lyrics:{trackId}`, returns `{ synced: {t,text}[]|null, plain: string|null, source }`. No DB write.
- Frontend: `components/Lyrics.tsx` — if `synced` present, binary-search `position` → active index, `scrollIntoView({block:'center'})`, `font-display` for RTL detection `/[؀-ۿ]/`. If only `plain`, render pre-wrapped text. `FullPlayer` `lyricsOpen` already toggles it; no new route.

### 4.5 Queue reorder
- `store/player.ts` — add `reorderQueue(from:number, to:number)` that splices `queue` and adjusts `index` (if `from < index <= to` → `index--`, etc.; if moving current, keep `current` stable). Also `reorderManual` already via `Reorder.Group`.
- `FullPlayer/QueueSheet` — wrap `rest` in `Reorder.Group` (like `manual`), each `QueueRow` as `Reorder.Item value={track}`; drag handle is the row itself (existing `touch-none`).

### 4.6 Fonts & OG & CORS truth
- Fonts: add `web/public/fonts/Vazirmatn-{400,500,700}.woff2` (from Google Fonts download), `tokens.css` replaces `@import url(googleapis)` with `@font-face { font-family: Vazirmatn; src: url(/fonts/Vazirmatn-*.woff2); font-display: swap }`.
- OG: `index.html` adds `<meta property="og:image" content="/og.webp">` + fallback `/og.png` + `og:image:width/height` (1200/630). nginx already serves static.
- CORS truth: `shared/config/cors.ts` (or `infra/cors.ts`) exports `ALLOWED_ORIGINS`; `infra/nginx/solo.conf.template` and `web/vite.config.ts` dev proxy read it at build. Minimal in Fase 1: at least a `scripts/check-cors.mjs` that asserts `CORS_ORIGINS` env and `solo.conf.template` `connect-src` list match.

## 5) Data flow & errors

- Share: never throws to caller; returns outcome. `AbortError` from `navigator.share` → `shared` (user dismissed, not error).
- Lyrics: `GET /v1/lyrics/:id` 200 even on upstream miss (`{synced:null, plain:null}`); 404 only if track not found; 429 upstream → 200 with `plain:null` + `Retry-After` header. Frontend shows `lyrics.empty` toast, not error.
- Queue: reorder is local Zustand only; no API.
- Fonts/OG: static, no runtime error path.

## 6) Testing

- Unit (vitest):
  - `lib/share.test.ts` — mock `navigator.share/canShare/clipboard`, cover `files→shared`, `url→shared`, `sheet` fallback, `AbortError`.
  - `components/Lyrics.test.tsx` — synced highlight at `position`, `plain` fallback, RTL dir.
  - `store/player.reorder.test.ts` — `reorderQueue` index bookkeeping (before/after current, moving current).
- Manual checklist: Telegram Android/iOS WebView share, Chrome desktop share, queue drag on touch + mouse, lyrics scroll at 2× speed, RTL, reduced-motion, TV D-pad unchanged.
- Budget: `npm run build` in `web` + `miniapp`; `scripts/check-size.mjs` must pass.

## 7) Execution order

1. `scripts/generate-icons.mjs` + fonts + og + favicon fix (no UI risk)
2. `lib/share.ts` + `ShareSheet.tsx` + wire `TrackActions/FullPlayer` (bug fix)
3. `backend lyrics proxy` + `Lyrics.tsx` polish
4. `player reorderQueue` + `QueueSheet` reorder for `rest`
5. CORS truth check + final `npm run build` + manual QA

## 8) Rollout

- Single PR `fase-1-hardening` on `main`, only `web/ + miniapp/ + backend/app/api/routers/lyrics.py + scripts/ + infra/ + docs/`.
- Server: `git pull --ff-only` → `docker compose -f infra/compose/solo.yml --env-file .env --profile build run --rm frontend` → `up -d --force-recreate nginx` → `curl -I https://noax.virgule.studio/og.webp` + `curl -I .../favicon.ico`.
- Client: hard refresh (`Ctrl+Shift+R`) for `sw.js` + fonts.
