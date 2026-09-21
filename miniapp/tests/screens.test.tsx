import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { Home } from '@/screens/Home';
import { Library } from '@/screens/Library';
import { Search } from '@/screens/Search';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeChannel, makeMe, makeTrack, mockApi, Providers, renderApp } from './helpers';

const page = <T,>(items: T[]) => ({ items, next_cursor: null });
const tracks = [
  makeTrack({ id: 1, title: 'شب بارونی' }),
  makeTrack({ id: 2, title: 'پل', artists: [{ id: 11, name: 'گوگوش', role: 'primary' }] }),
];

beforeEach(() => {
  __setAuthForTests('token');
  usePlayer.setState({ queue: [], current: null, isPlaying: false, index: 0 });
  useUi.setState({ upsell: null, toasts: [] });
});

function homeRoutes(overrides: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'GET /v1/library/channels': [makeChannel({ status: 'indexing', progress_pct: 62 })],
    'GET /v1/library/tracks': page(tracks),
    'GET /v1/channels/featured': page([makeChannel({ id: 9, title: 'Rap Farsi', is_featured: true })]),
    'POST /v1/tracks/thumbs': { items: {} },
    'POST /v1/tracks/1/stream': {
      url: 'https://cdn.test/s/1?t=x',
      thumb_url: null,
      expires_at: Math.floor(Date.now() / 1000) + 300,
      size: 1000,
      mime: 'audio/mpeg',
    },
    ...overrides,
  };
}

describe('Home', () => {
  it('shows the greeting, indexing progress and recent tracks', async () => {
    mockApi(homeRoutes());
    renderApp(<Home />);

    expect(await screen.findByText(/سارا/)).toBeInTheDocument();
    expect(await screen.findByText('در حال ایندکس · ۶۲٪')).toBeInTheDocument();
    expect(await screen.findByText('Rap Farsi')).toBeInTheDocument();
    const rows = await screen.findAllByText('شب بارونی');
    expect(rows.length).toBeGreaterThan(0);
  });

  it('plays a track from the recent list', async () => {
    mockApi(homeRoutes());
    renderApp(<Home />);
    const row = await screen.findAllByText('پل');
    await userEvent.click(row[0] as HTMLElement);
    await waitFor(() => expect(usePlayer.getState().current?.id).toBe(2));
    expect(usePlayer.getState().queue).toHaveLength(2);
  });

  it('offers the empty state when no channel is connected', async () => {
    mockApi(homeRoutes({ 'GET /v1/library/channels': [], 'GET /v1/library/tracks': page([]) }));
    renderApp(<Home />);
    expect(await screen.findByText('هنوز کانالی وصل نکرده‌ای')).toBeInTheDocument();
  });
});

describe('Search', () => {
  it('debounces, searches and renders results', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/search/suggest': { history: [], tracks: [] },
      'GET /v1/search': { items: [tracks[1]], total: 1, offset: 0, limit: 30, degraded: false },
      'POST /v1/tracks/thumbs': { items: {} },
    });
    renderApp(<Search />);
    await userEvent.type(screen.getByRole('searchbox'), 'googoosh');
    expect(await screen.findByText('پل')).toBeInTheDocument();
    const searchCalls = calls.filter((call) => call.url.startsWith('/v1/search?'));
    expect(searchCalls).toHaveLength(1); // one request for eight keystrokes
    expect(searchCalls[0]?.url).toContain('scope=library');
  });

  it('switches scope and shows the degraded banner', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/search/suggest': { history: ['moein'], tracks: [] },
      'GET /v1/search': { items: [], total: 0, offset: 0, limit: 30, degraded: true },
      'POST /v1/tracks/thumbs': { items: {} },
    });
    renderApp(<Search />);
    await userEvent.click(screen.getByText('همهٔ کانال‌ها'));
    await userEvent.type(screen.getByRole('searchbox'), 'x');
    expect(await screen.findByText(/کیفیت کمتر/)).toBeInTheDocument();
    expect(await screen.findByText('چیزی پیدا نشد')).toBeInTheDocument();
  });
});

describe('Library', () => {
  it('adds a channel', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/library/tracks': page(tracks),
      'GET /v1/library/channels': [],
      'GET /v1/library/albums': [],
      'GET /v1/library/artists': page([]),
      'POST /v1/tracks/thumbs': { items: {} },
      'POST /v1/library/channels': { channel: makeChannel(), created: true },
    });
    renderApp(<Library />);
    await userEvent.click(screen.getByLabelText('افزودن کانال'));
    await userEvent.type(await screen.findByPlaceholderText('@PersianMusic'), '@persianhits');
    await userEvent.click(screen.getByText('افزودن'));
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'POST' && call.url === '/v1/library/channels')).toBe(true),
    );
    expect(useUi.getState().toasts[0]?.text).toBe('اضافه شد');
  });

  it('turns a plan limit into the upsell sheet', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/library/tracks': page(tracks),
      'GET /v1/library/channels': [],
      'GET /v1/library/albums': [],
      'GET /v1/library/artists': page([]),
      'POST /v1/tracks/thumbs': { items: {} },
      'POST /v1/library/channels': new Response(
        JSON.stringify({ error: { code: 'plan_limit', message: 'x', details: { kind: 'channels', limit: 3 } } }),
        { status: 402 },
      ),
    });
    renderApp(<Library />);
    await userEvent.click(screen.getByLabelText('افزودن کانال'));
    await userEvent.type(await screen.findByPlaceholderText('@PersianMusic'), '@four');
    await userEvent.click(screen.getByText('افزودن'));
    await waitFor(() => expect(useUi.getState().upsell).toEqual({ kind: 'channels', limit: 3 }));
  });

  it('explains private links instead of failing silently', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/library/tracks': page([]),
      'GET /v1/library/channels': [],
      'GET /v1/library/albums': [],
      'GET /v1/library/artists': page([]),
      'POST /v1/tracks/thumbs': { items: {} },
      'POST /v1/library/channels': new Response(
        JSON.stringify({ error: { code: 'invalid_input', message: 'x', details: { reason: 'private_link' } } }),
        { status: 422 },
      ),
    });
    renderApp(<Library />);
    await userEvent.click(screen.getByLabelText('افزودن کانال'));
    await userEvent.type(await screen.findByPlaceholderText('@PersianMusic'), 'https://t.me/+secret');
    await userEvent.click(screen.getByText('افزودن'));
    expect(await screen.findByText(/کانال خصوصی است/)).toBeInTheDocument();
  });

  it('filters tracks by language', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/library/tracks': page(tracks),
      'GET /v1/library/channels': [],
      'GET /v1/library/albums': [],
      'GET /v1/library/artists': page([]),
      'POST /v1/tracks/thumbs': { items: {} },
    });
    renderApp(<Library />);
    await userEvent.click(await screen.findByText('فیلتر'));
    await userEvent.click(await screen.findByText('FA'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('language=fa'))).toBe(true),
    );
  });
});

describe('English UI', () => {
  it('renders the same screen in English and LTR', async () => {
    mockApi({ ...authRoutes(makeMe({ lang: 'en', first_name: 'Sara' })), ...homeRoutes() });
    const { container } = renderApp(<Home />, {
      wrapper: ({ children }) => <Providers lang="en">{children}</Providers>,
    });
    expect(await screen.findByText(/Good (morning|afternoon|evening)/)).toBeInTheDocument();
    expect(within(container).getByText('Suggested channels')).toBeInTheDocument();
  });
});
