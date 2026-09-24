/**
 * A design harness — not shipped, not routed, not linked from anywhere.
 *
 * The app itself refuses to render outside Telegram (no initData, no API), which
 * makes it impossible to *look* at a restyle while making it. This page mounts the
 * pieces that carry the design — rows, chrome, controls, the credit line — against
 * mock data, in both themes, so a contrast mistake is seen rather than guessed at.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import type { Track } from '@/api/client';
import { TabBar } from '@/components/TabBar';
import { TrackRow } from '@/components/TrackRow';
import { Cover, Credit, EmptyState, Glass } from '@/components/ui';
import { I18nProvider } from '@/i18n';
import '@/design/tokens.css';

const track = (id: number, title: string, artist: string): Track =>
  ({
    id,
    title,
    artists: [{ id: 1, name: artist, role: 'primary' }],
    album: '1989',
    duration: 218,
    language: 'en',
    year: 2014,
    has_thumb: false,
    channels_count: 3,
    playable: true,
    liked: id % 2 === 0,
    channel: null,
    palette: null,
  }) as unknown as Track;

const tracks = [
  track(1, 'Wildest Dreams', 'Taylor Swift'),
  track(2, 'Shake It Off', 'Taylor Swift'),
  track(3, 'گل سنگم', 'هایده'),
];

function Panel({ theme }: { theme: 'light' | 'dark' }) {
  return (
    <div
      data-theme={theme}
      style={{ background: 'var(--bg-0)', color: 'var(--ink)' }}
      className="min-h-screen w-1/2 px-4 pt-4"
    >
      <h1 className="mb-1 text-[22px] font-bold">{theme}</h1>
      <p className="mb-4 text-[13px] text-[var(--ink-dim)]">Secondary label · ۳:۳۸</p>

      <h2 className="mb-2 text-[15px] font-bold">Rows</h2>
      <div className="list-group list-rows mb-5">
        {tracks.map((item) => (
          <TrackRow key={item.id} track={item} onPlay={() => undefined} />
        ))}
      </div>

      <h2 className="mb-2 text-[15px] font-bold">Controls</h2>
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="rounded-xl bg-[var(--accent)] px-4 py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)]"
        >
          Play
        </button>
        <button type="button" className="rounded-full bg-[var(--fill)] px-3.5 py-1.5 text-[12.5px]">
          Chip
        </button>
        <button
          type="button"
          className="rounded-full bg-[var(--fill-strong)] px-3.5 py-1.5 text-[12.5px]"
        >
          Chip, selected
        </button>
        <Cover seed="album" size={56} glyph="♫" />
      </div>

      <h2 className="mb-2 text-[15px] font-bold">Chrome</h2>
      <Glass className="mb-1 flex items-center gap-3 border-t border-[var(--separator)] px-3 py-2">
        <Cover seed={7} size={42} glyph="♫" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-semibold">Wildest Dreams</span>
          <span className="block truncate text-[11.5px] text-[var(--ink-faint)]">Taylor Swift</span>
        </span>
      </Glass>
      <TabBar />

      <h2 className="mb-2 mt-5 text-[15px] font-bold">Transport</h2>
      <div className="mb-5 flex items-center justify-center gap-7">
        <button type="button" className="p-2 text-[var(--ink-dim)]">⤨</button>
        <button type="button" className="p-2 text-[var(--ink)]">⏮</button>
        <button
          type="button"
          className="grid h-16 w-16 place-items-center rounded-full text-[42px] leading-none text-[var(--ink)] active:bg-[var(--fill)]"
        >
          ⏸
        </button>
        <button type="button" className="p-2 text-[var(--ink)]">⏭</button>
        <button type="button" className="p-2 text-[var(--ink-dim)]">⟳</button>
      </div>
      <div className="mb-5 h-1 w-full rounded-full bg-[var(--fill)]">
        <div className="relative h-1 w-1/2 rounded-full bg-[var(--accent)]">
          <span className="absolute -top-1.5 -end-2 block h-4 w-4 rounded-full bg-[var(--ink)] shadow-[0_1px_3px_rgba(0,0,0,0.3)]" />
        </div>
      </div>

      <h2 className="mt-5 mb-2 text-[15px] font-bold">Empty & credit</h2>
      <EmptyState title="چیزی نیست" body="بعداً دوباره سر بزن" />
      <Credit />
    </div>
  );
}

const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={client}>
        <I18nProvider lang="fa">
          <MemoryRouter>
            <div className="flex">
              <Panel theme="light" />
              <Panel theme="dark" />
            </div>
          </MemoryRouter>
        </I18nProvider>
      </QueryClientProvider>
    </StrictMode>,
  );
}

export {};
