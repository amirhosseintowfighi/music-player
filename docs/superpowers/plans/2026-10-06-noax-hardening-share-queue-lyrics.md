# Fase 1: Hardening + Share + Queue + Lyrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Share to open the system sheet (not just Image saved), harden assets (icons/favicon/og/fonts/CORS), and ship Queue reorder + Lyrics MVP in one deployable PR.

**Architecture:** Incremental hardening — one PR touching only Fase 1 areas: `scripts/generate-icons.mjs` + self-hosted Vazirmatn + `og.webp` + real `favicon.ico`, tiered `lib/share.ts` (files→url→in-app ShareSheet), `GET /v1/lyrics/{id}` LRCLIB proxy + synced highlight, `store/player.reorderQueue` + `QueueSheet` Reorder for `rest`, light `packages/shared-ui` alias for share only (full monorepo is Fase 4).

**Tech Stack:** React 19 + Vite + Tailwind + Framer Motion Reorder + Zustand (web/miniapp), FastAPI + Redis (lyrics proxy), sharp dev-only (generate-icons), Vitest + pytest

**Spec:** `docs/superpowers/specs/2026-10-06-noax-hardening-share-queue-lyrics-design.md`

## Global Constraints

- No new runtime deps if avoidable — `framer-motion/Reorder` already bundled; `sharp` stays dev-only.
- Deploy via `infra/compose/solo.yml --profile build frontend` + `up -d --force-recreate nginx` must stay working.
- Do not touch `miniapp` auth contract or `backend` auth; only new endpoint is `GET /v1/lyrics/{trackId}`.
- Budget gzip ≤ 200KB checked by `scripts/check-size.mjs` in both `web` and `miniapp`.
- Brand `Noax | نوکس` bilingual, `manifest.webmanifest` is `Noax`, web shell stays `w-full` (no `max-w-6xl` gutters), TV 10-foot intact.
- `npm run build` must be green with no `%VITE_API_URL%` warning.

## Review Focus

- Telegram WebView where `navigator.canShare` is missing/false and `navigator.share` only accepts `{title,text,url}` — share must still open chooser, not silently download.
- Cover image from `t.me`/`cdn` with CORS taint — `loadImage(crossOrigin=anonymous)` returns null and card must still render (fallback glyph) and share must not crash.
- LRCLIB upstream 429/timeout — `GET /v1/lyrics/{id}` must return 200 `{synced:null,plain:null}` not 500, and frontend shows `lyrics.empty` not error toast.
- Reordering queue when dragged item is before current index — `player.index` must shift correctly or playback jumps to wrong track.
- Browser without `webp` support requesting `og.webp` — `og:image` must list fallback `og.png` and `og.webp` with `width/height` so crawlers pick available one.

---

### Task 1: Assets hardening — icons script + Vazirmatn self-host + og.webp + real favicon.ico

**Files:**
- Create: `scripts/generate-icons.mjs`
- Create: `web/public/fonts/Vazirmatn-400.woff2`, `Vazirmatn-500.woff2`, `Vazirmatn-700.woff2` (binary)
- Modify: `web/package.json` (add `"icons": "node scripts/generate-icons.mjs"` script, keep `sharp` devDep)
- Modify: `miniapp/package.json` (same `sharp` devDep if missing, no new runtime dep)
- Modify: `web/src/design/tokens.css:1-3` (replace `@import url(googleapis)` with `@font-face`)
- Modify: `miniapp/src/design/tokens.css:1-3` (same)
- Modify: `web/index.html` (add `og:image` webp+png + `og:image:width/height`, `preload` font)
- Modify: `miniapp/index.html` (same)
- Test: `scripts/generate-icons.test.mjs` (node --test) or `web/src/lib/assets-check.test.ts` (optional)

**Interfaces:**
- Consumes: `web/public/brand/noax.jpg` (640×640 source), Google Fonts woff2 files (downloaded once)
- Produces: `scripts/generate-icons.mjs` exports nothing — side-effect: writes `web/public/icons/icon-*.png`, `apple-touch-icon.png`, `web/public/favicon.ico` (multi-size 16/32/48), `web/public/og.webp` (~120KB) + `og.png` fallback, mirrors to `miniapp/public/**`

- [ ] **Step 1: Write the failing test `scripts/generate-icons.test.mjs`**

```js
import assert from 'node:assert/strict';
import { existsSync, statSync } from 'node:fs';
test('generate-icons outputs exist and og.webp < 180KB', () => {
  assert.ok(existsSync('web/public/og.webp'), 'og.webp missing');
  assert.ok(statSync('web/public/og.webp').size < 180*1024, 'og.webp too big');
  assert.ok(existsSync('web/public/favicon.ico'), 'favicon.ico missing');
  // favicon.ico must be multi-size ICO, not PNG-renamed: first 4 bytes 00 00 01 00
  const ico = (await import('node:fs')).readFileSync('web/public/favicon.ico');
  assert.equal(ico[0], 0); assert.equal(ico[2], 1);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test scripts/generate-icons.test.mjs`
Expected: FAIL — `og.webp missing` or ICO header mismatch

- [ ] **Step 3: Implement `scripts/generate-icons.mjs`**

Use `sharp` (dynamic `import('sharp')`) to read `web/public/brand/noax.jpg` and write: 192/512/512-maskable (80% safe zone)/180/apple-touch, `favicon.ico` via `sharp` + manual ICO packing (or `png-to-ico` if already devDep, else pack 16/32/48 PNGs into ICO header), `og.webp` 1200×630 `webp({quality:82})` + `og.png` fallback. Mirror all to `miniapp/public/`. Must be idempotent.

- [ ] **Step 4: Self-host Vazirmatn — download woff2 and patch `tokens.css`**

Download `Vazirmatn-400/500/700.woff2` from Google Fonts to `web/public/fonts/` (and `miniapp/public/fonts/`), replace `@import url('https://fonts.googleapis.com/...Vazirmatn...')` with:

```css
@font-face { font-family: Vazirmatn; src: url(/fonts/Vazirmatn-400.woff2) format(woff2); font-weight: 400; font-display: swap; }
@font-face { font-family: Vazirmatn; src: url(/fonts/Vazirmatn-500.woff2) format(woff2); font-weight: 500; font-display: swap; }
@font-face { font-family: Vazirmatn; src: url(/fonts/Vazirmatn-700.woff2) format(woff2); font-weight: 700; font-display: swap; }
```

Add `<link rel="preload" as="font" type="font/woff2" href="/fonts/Vazirmatn-400.woff2" crossorigin>` to both `index.html`. Remove googleapis preconnect if only used for fonts.

- [ ] **Step 5: Patch `og` meta in both `index.html`**

Add:

```html
<meta property="og:image" content="/og.webp" />
<meta property="og:image:type" content="image/webp" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta property="og:image:alt" content="Noax — نوکس" />
<!-- fallback for crawlers without webp -->
<meta property="og:image" content="/og.png" />
```

Keep existing `/og.png` generation; ensure `vite` does not hash `public/og.*`.

- [ ] **Step 6: Run `node scripts/generate-icons.mjs` and verify test passes**

Run: `node scripts/generate-icons.mjs && node --test scripts/generate-icons.test.mjs`
Expected: PASS

- [ ] **Step 7: Verify builds still pass and budget holds**

Run: `npm run build` in `web` and `miniapp`
Expected: PASS, `check-size.mjs` gzip ≤ 200KB

- [ ] **Step 8: Commit**

```bash
git add scripts/generate-icons.mjs scripts/generate-icons.test.mjs web/package.json miniapp/package.json web/src/design/tokens.css miniapp/src/design/tokens.css web/index.html miniapp/index.html web/public/fonts/* miniapp/public/fonts/* web/public/og.* miniapp/public/og.* web/public/favicon.ico miniapp/public/favicon.ico
git commit -m "feat(assets): self-host Vazirmatn, og.webp, real favicon.ico, generate-icons script"
```

### Task 2: Share fix — tiered share + in-app ShareSheet (the bug)

**Files:**
- Create: `web/src/lib/share.ts`
- Create: `web/src/components/ShareSheet.tsx`
- Create: `miniapp/src/lib/share.ts` (mirror or symlink via shared alias)
- Create: `miniapp/src/components/ShareSheet.tsx` (mirror)
- Modify: `web/src/lib/shareCard.ts` (no logic change, just ensure exported `drawCard` handles null cover)
- Modify: `web/src/components/TrackActions.tsx:333-344` (use `shareTrack` with `hiRes(thumbs[track.id])`)
- Modify: `web/src/components/FullPlayer.tsx:668-676` (same)
- Modify: `miniapp/src/components/TrackActions.tsx` and `miniapp/src/components/FullPlayer.tsx` (same)
- Modify: `web/src/i18n/en.ts` and `fa.ts` (add `share.choose`, `share.copyLink`, `share.whatsapp`, `share.telegram`, `share.download`, `share.storyHint`, `share.copied`)
- Modify: `miniapp/src/i18n/en.ts` and `fa.ts` (same)
- Test: `web/src/lib/share.test.ts`, `web/src/components/ShareSheet.test.tsx`

**Interfaces:**
- Consumes: `web/src/lib/shareCard.ts:drawCard(track,coverSrc,brand)->Promise<HTMLCanvasElement|null>`, `BOT_USERNAME`, `artistNames`, `hiRes`
- Produces: `shareTrack(track:Track, coverSrc:string|undefined): Promise<'shared'|'copied'|'saved'|'sheet'|'failed'>` — tiered: try `share({files})`, then `share({title,text,url})`, then `sheet` fallback. `ShareSheet({open,onClose,track,coverSrc})` component.

- [ ] **Step 1: Write failing tests `web/src/lib/share.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shareTrack, buildShareUrl } from '@/lib/share';
describe('shareTrack', () => {
  it('builds t.me link as https://t.me/<bot>?startapp=tr_<id>', () => {
    expect(buildShareUrl(123, 'noaxofficialbot')).toBe('https://t.me/noaxofficialbot?startapp=tr_123');
  });
  it('uses navigator.share files when canShare true → shared', async () => {
    const nav = { canShare: () => true, share: vi.fn().mockResolvedValue(undefined) } as any;
    vi.stubGlobal('navigator', nav);
    expect(await shareTrack({id:1,title:'A',artists:[]} as any, undefined)).toBe('shared');
  });
  it('falls back to share url when canShare false → shared', async () => {
    const nav = { canShare: () => false, share: vi.fn().mockResolvedValue(undefined) } as any;
    vi.stubGlobal('navigator', nav);
    expect(await shareTrack({id:1,title:'A',artists:[]} as any, undefined)).toBe('shared');
  });
  it('returns sheet when navigator.share missing', async () => {
    vi.stubGlobal('navigator', {} as any);
    expect(await shareTrack({id:1,title:'A',artists:[]} as any, undefined)).toBe('sheet');
  });
  it('maps AbortError to shared not failed', async () => {
    const err = Object.assign(new Error('abort'), {name:'AbortError'});
    const nav = { canShare: () => true, share: vi.fn().mockRejectedValue(err) } as any;
    vi.stubGlobal('navigator', nav);
    // must not return failed
    expect(await shareTrack({id:2,title:'B',artists:[]} as any, undefined)).not.toBe('failed');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- web/src/lib/share.test.ts`
Expected: FAIL — `Cannot find module '@/lib/share'`

- [ ] **Step 3: Implement `web/src/lib/share.ts`**

```ts
export function buildShareUrl(trackId:number, bot:string): string
export async function shareTrack(track:Track, coverSrc:string|undefined): Promise<'shared'|'copied'|'saved'|'sheet'|'failed'>
```

Logic: `drawCard` → `canvas.toBlob` → `File`; if `nav.canShare?.({files:[file]}) && nav.share` try `nav.share({files:[file], title})` → `shared` (catch `AbortError` → `shared`); else if `nav.share` try `nav.share({title: track.title, text: `${title} — ${artistNames(track)}`, url: buildShareUrl(track.id, BOT_USERNAME)})` → `shared`; else return `sheet`. If both throws non-Abort, return `sheet` so caller opens sheet (not download). Never auto-download here; download is via sheet's Download row.

- [ ] **Step 4: Implement `web/src/components/ShareSheet.tsx`**

Props `{open:boolean,onClose:()=>void,track:Track,coverSrc?:string}`. Uses existing `Sheet`. Rows: `Telegram` → `openTelegramLink(t.me/share/url?url=shareUrl&text=...)`, `WhatsApp` → `openExternalLink(https://wa.me/?text=encodeURIComponent(text+' '+url))`, `Copy Link` → `navigator.clipboard.writeText(url)` + `toast(share.copied)`, `Download card` → `shareCard(track,coverSrc,brand)` then download link, `Instagram Story` hint row with `share.storyHint` text. All rows close sheet after action except copy.

- [ ] **Step 5: Wire callers — `TrackActions` and `FullPlayer`**

Replace:

```ts
const outcome = await shareCard(track, undefined, BOT_USERNAME);
```

with:

```ts
import { shareTrack } from '@/lib/share';
const outcome = await shareTrack(track, hiRes(thumbs[track.id]) ?? thumbs[track.id]);
if (outcome === 'sheet') setShareOpen(true);
else if (outcome === 'saved') toast(t('share.saved'),'success');
else if (outcome === 'copied') toast(t('share.copied'),'success');
else if (outcome === 'failed') toast(t('app.error'),'error');
```

Add `const [shareOpen,setShareOpen]=useState(false)` and render `<ShareSheet open={shareOpen} onClose={()=>setShareOpen(false)} track={track} coverSrc={hiRes(thumbs[track.id])} />`. Do same in `miniapp`.

- [ ] **Step 6: Add i18n keys**

`en`: `share.choose: 'Share'`, `share.telegram: 'Telegram'`, `share.whatsapp: 'WhatsApp'`, `share.copyLink: 'Copy link'`, `share.download: 'Download card'`, `share.copied: 'Link copied'`, `share.storyHint: 'Image saved — open Instagram and add to Story'`
`fa`: `share.choose: 'اشتراک‌گذاری'`, `share.telegram: 'تلگرام'`, `share.whatsapp: 'واتساپ'`, `share.copyLink: 'کپی لینک'`, `share.download: 'دانلود کارت'`, `share.copied: 'لینک کپی شد'`, `share.storyHint: 'تصویر ذخیره شد — حالا استوری اینستاگرام را باز کن'`

- [ ] **Step 7: Run tests to verify they pass**

Run: `npm run test -- web/src/lib/share.test.ts web/src/components/ShareSheet.test.tsx`
Expected: PASS (5+ tests)

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/share.ts web/src/components/ShareSheet.tsx miniapp/src/lib/share.ts miniapp/src/components/ShareSheet.tsx web/src/components/TrackActions.tsx web/src/components/FullPlayer.tsx miniapp/src/components/TrackActions.tsx miniapp/src/components/FullPlayer.tsx web/src/i18n/* miniapp/src/i18n/*
git commit -m "fix(share): tiered share (files→url→sheet) with system chooser, pass hiRes cover"
```

### Task 3: Lyrics MVP — backend proxy + frontend highlight

**Files:**
- Create: `backend/app/api/routers/lyrics.py`
- Modify: `backend/app/main.py` (include router)
- Modify: `backend/app/deps.py` or `backend/app/core/redis.py` (reuse existing Redis client)
- Create: `backend/tests/test_lyrics.py`
- Modify: `web/src/components/Lyrics.tsx` (enhance to synced highlight + plain fallback)
- Create: `web/src/components/Lyrics.test.tsx`
- Modify: `miniapp/src/components/Lyrics.tsx` (mirror)
- Modify: `web/src/i18n/en.ts` / `fa.ts` (add `lyrics.empty`, `lyrics.plain`)
- Test: `backend/tests/test_lyrics.py`, `web/src/components/Lyrics.test.tsx`

**Interfaces:**
- Consumes: `Track` by id from DB, `httpx.AsyncClient` (shared AppState), Redis `get/setex`
- Produces: `GET /v1/lyrics/{trackId}` → `{ synced: {t:number,text:string}[]|null, plain:string|null, source:"lrclib"|"none" }` (200 always except 404 track not found)

- [ ] **Step 1: Write failing backend test `backend/tests/test_lyrics.py`**

```python
def test_lyrics_returns_empty_when_no_match(client, track):
    r = client.get(f"/v1/lyrics/{track.id}")
    assert r.status_code == 200
    assert r.json()["synced"] is None

def test_lyrics_404_for_unknown_track(client):
    assert client.get("/v1/lyrics/999999").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_lyrics.py -v`
Expected: FAIL — `404 not found` route missing

- [ ] **Step 3: Implement `backend/app/api/routers/lyrics.py`**

`@router.get("/v1/lyrics/{track_id}")` — load track, key `lyrics:{id}` in Redis (TTL 24h), if miss `httpx.get("https://lrclib.net/api/get", params={artist_name, track_name, duration})` with 3s timeout; on miss try `api/search` first hit; parse `syncedLyrics` LRC lines ` [mm:ss.xx] text` → `{t,text}`; `plainLyrics` fallback; on 429/timeout return `{synced:null,plain:null,source:"none"}` 200. Never leak upstream body.

- [ ] **Step 4: Register router in `backend/app/main.py`**

Add `app.include_router(lyrics.router)`.

- [ ] **Step 5: Write frontend failing test `web/src/components/Lyrics.test.tsx`**

```tsx
it('highlights current line at position', async () => {
  const synced=[{t:0,text:'a'},{t:10,text:'b'}];
  render(<Lyrics synced={synced} plain={null} position={12} />);
  expect(screen.getByText('b').closest('[data-active]')).toBeTruthy();
});
it('renders plain when synced null', () => {
  render(<Lyrics synced={null} plain={'hello\nworld'} position={0} />);
  expect(screen.getByText(/hello/)).toBeInTheDocument();
});
```

- [ ] **Step 6: Implement `web/src/components/Lyrics.tsx` enhancement**

Props `{trackId:number}` fetches `GET /v1/lyrics/{id}` via `useQuery`; if `synced`, binary-search `position` → `activeIndex`, `useEffect` → `activeRef.current?.scrollIntoView({block:'center',behavior:'smooth'})` unless `prefers-reduced-motion`; if `synced` null and `plain` present render `<pre>`; if both null render `lyrics.empty`. Respect `dir` rtl via `/[؀-ۿ]/.test(track.title)`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest backend/tests/test_lyrics.py -v && npm run test -- web/src/components/Lyrics.test.tsx`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/routers/lyrics.py backend/app/main.py backend/tests/test_lyrics.py web/src/components/Lyrics.tsx miniapp/src/components/Lyrics.tsx web/src/i18n/* miniapp/src/i18n/*
git commit -m "feat(lyrics): LRCLIB proxy + synced highlight + plain fallback"
```

### Task 4: Queue reorder — make `rest` draggable with correct index

**Files:**
- Modify: `web/src/store/player.ts`
- Modify: `miniapp/src/store/player.ts` (if exists, else note)
- Modify: `web/src/components/FullPlayer.tsx:185-263` (`QueueSheet`)
- Modify: `miniapp/src/components/FullPlayer.tsx` (same)
- Test: `web/src/store/player.reorder.test.ts`

**Interfaces:**
- Consumes: `queue:Track[]`, `index:number`, `current:Track|null`
- Produces: `reorderQueue(from:number, to:number): void` on Zustand store — splices `queue` and adjusts `index` so current playback doesn't jump

- [ ] **Step 1: Write failing test `web/src/store/player.reorder.test.ts`**

```ts
import { usePlayer } from '@/store/player';
it('reorder rest before current shifts index', () => {
  usePlayer.setState({ queue:[{id:1},{id:2},{id:3},{id:4}] as any, index:2 });
  usePlayer.getState().reorderQueue(0,3); // move id1 (before current) to after current
  expect(usePlayer.getState().index).toBe(1);
});
it('moving current keeps current stable', () => {
  usePlayer.setState({ queue:[{id:1},{id:2},{id:3}] as any, index:1 });
  usePlayer.getState().reorderQueue(1,0);
  expect(usePlayer.getState().current?.id).toBe(2);
  expect(usePlayer.getState().index).toBe(0);
});
it('reorder after current does not shift index', () => {
  usePlayer.setState({ queue:[{id:1},{id:2},{id:3},{id:4}] as any, index:1 });
  usePlayer.getState().reorderQueue(2,3);
  expect(usePlayer.getState().index).toBe(1);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- web/src/store/player.reorder.test.ts`
Expected: FAIL — `reorderQueue is not a function`

- [ ] **Step 3: Implement `reorderQueue` in `web/src/store/player.ts`**

```ts
reorderQueue: (from:number, to:number) => {
  const { queue, index } = get();
  if (from===to) return;
  const next=[...queue]; const [moved]=next.splice(from,1); next.splice(to,0,moved);
  let newIndex=index;
  if (from === index) newIndex = to;
  else if (from < index && to >= index) newIndex = index-1;
  else if (from > index && to <= index) newIndex = index+1;
  set({ queue: next, index: newIndex });
}
```

Same in `miniapp` if file exists.

- [ ] **Step 4: Wrap `rest` in `Reorder.Group` in `QueueSheet`**

Replace `rest.map → <li><QueueRow>` with:

```tsx
<Reorder.Group axis="y" values={rest} onReorder={(order)=>{
  // map order back to queue indices: rebuild queue = queue.slice(0,index+1).concat(order).concat(queue.slice(index+1+rest.length))
  // simpler: call reorderQueue for each drag end; for group, reconstruct
}} />
```

Simpler correct impl: keep `queue` as source, derive `rest=queue.slice(index+1)`; `onReorder` receives new `rest` order → `set({queue: [...queue.slice(0,index+1), ...newRest]})`. Each `Reorder.Item value={track} key={track.id+index}`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test -- web/src/store/player.reorder.test.ts`
Expected: PASS (3/3)

- [ ] **Step 6: Commit**

```bash
git add web/src/store/player.ts miniapp/src/store/player.ts web/src/components/FullPlayer.tsx miniapp/src/components/FullPlayer.tsx
git commit -m "feat(queue): make rest reorderable with index bookkeeping"
```

### Task 5: CORS truth check + final verification

**Files:**
- Create: `scripts/check-cors.mjs`
- Modify: `infra/nginx/solo.conf.template` (if drift found, align `connect-src` with `CORS_ORIGINS`)
- Modify: `web/vite.config.ts` (ensure `server.proxy` and `VITE_API_URL` documented, no `%VITE_API_URL%` warning)
- Test: `scripts/check-cors.test.mjs` (optional), manual `npm run build` in both apps

**Interfaces:**
- Consumes: `.env` `CORS_ORIGINS`, `infra/nginx/solo.conf.template` `add_header Content-Security-Policy`, `web/vite.config.ts` proxy
- Produces: `scripts/check-cors.mjs` that exits 1 if `CORS_ORIGINS` and nginx `connect-src`/`Access-Control-Allow-Origin` disagree

- [ ] **Step 1: Write `scripts/check-cors.mjs` (no failing test needed — script is the check)**

Read `.env` / `CORS_ORIGINS`, read `solo.conf.template`, extract `connect-src` domains and `Access-Control-Allow-Origin` `$cors_origin` logic, assert apex `https://noax.virgule.studio` and `https://api.noax.virgule.studio` both allowed; if mismatch `console.error` and `process.exit(1)`. Run in CI `ci.yml` as `node scripts/check-cors.mjs`.

- [ ] **Step 2: Run it**

Run: `node scripts/check-cors.mjs`
Expected: PASS (or fix template)

- [ ] **Step 3: Final build verification — both apps**

Run: `npm run build` in `web` and `miniapp`
Expected: PASS, no `%VITE_API_URL%` warning, gzip ≤200KB

- [ ] **Step 4: Commit**

```bash
git add scripts/check-cors.mjs infra/nginx/solo.conf.template web/vite.config.ts
git commit -m "chore(cors): single truth check for CORS/CSP origins"
```

## Self-Review

- Spec coverage: 4.1 shared-ui light → Task 2 alias (minimal), 4.2 icons/fonts/og/favicon → Task 1, 4.3 share → Task 2, 4.4 lyrics → Task 3, 4.5 queue → Task 4, 4.6 fonts/og/CORS → Tasks 1+5. All covered.
- Step scan: each test step gives exact assertions; each code step gives exact signature/file; no TBD.
- Type consistency: `shareTrack`/`buildShareUrl`/`ShareSheet`/`reorderQueue(from,to)` names consistent across tasks.
- Review Focus: each of 5 lines maps to a test — WebView sheet → Task 2 tests, CORS cover → Task 2 drawCard fallback, LRCLIB 429 → Task 3 test, reorder index → Task 4 tests, webp fallback → Task 1 og meta.
- Proportion: plan is ~task-sized, not transcribed program — bodies are signatures + logic notes, not full files.
