import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { TrackActions } from '@/components/TrackActions';
import { Discover } from '@/screens/Discover';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const weekly = makeTrack({ id: 11, title: 'کشف اول' });
const trendingTrack = makeTrack({ id: 21, title: 'داغ هفته' });
const mostAdded = makeTrack({ id: 31, title: 'ترک پرتکرار' });

const feed = {
  sections: [
    { id: 'pl-5', kind: 'playlist', title: 'کشف هفتگی', playlist_id: 5, items: [] },
    {
      id: 'trending-7d',
      kind: 'tracks',
      title: 'پرشنونده‌های این هفته',
      playlist_id: null,
      items: [trendingTrack],
    },
    { id: 'most-added', kind: 'tracks', title: 'پرتکرار در کانال‌ها', playlist_id: null, items: [mostAdded] },
  ],
};

const playlistDetail = {
  id: 5,
  name: 'Discover Weekly',
  description: null,
  kind: 'discover_weekly' as const,
  is_public: false,
  is_collaborative: false,
  share_slug: null,
  share_url: null,
  tracks_count: 1,
  duration_total: 245,
  is_owner: true,
  can_edit: false,
  updated_at: '2026-09-18T10:00:00Z',
  items: [weekly],
};

function routes(extra: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'GET /v1/discover': feed,
    'GET /v1/playlists/5': playlistDetail,
    'GET /v1/trending': { items: [trendingTrack], next_cursor: null },
    'POST /v1/tracks/thumbs': { items: {} },
    ...extra,
  };
}

beforeEach(() => {
  __setAuthForTests('token');
  usePlayer.setState({ queue: [], current: null, index: 0, isPlaying: false });
  useUi.setState({ toasts: [], upsell: null, actionTrack: null });
  vi.unstubAllGlobals();
});

describe('Discover screen', () => {
  it('renders generated playlists and trending sections', async () => {
    mockApi(routes());
    renderApp(<Discover />);

    expect(await screen.findByText('کشف هفتگی')).toBeInTheDocument();
    expect(await screen.findByText('کشف اول')).toBeInTheDocument();
    expect(screen.getByText('پرشنونده‌های این هفته')).toBeInTheDocument();
    expect(screen.getByText('پرتکرار در کانال‌ها')).toBeInTheDocument();
  });

  it('plays the whole section as a queue, not just one track', async () => {
    mockApi(routes());
    renderApp(<Discover />);

    await userEvent.click(await screen.findByText('کشف اول'));
    await waitFor(() => expect(usePlayer.getState().current?.id).toBe(11));
    expect(usePlayer.getState().queue).toHaveLength(1);
  });

  it('asks the server for the selected trending window and kind', async () => {
    const api = mockApi(routes());
    renderApp(<Discover />);

    await userEvent.click(await screen.findByText('امروز'));
    await userEvent.click(screen.getByText('در حال صعود'));

    await waitFor(() => {
      const last = api.calls.filter((call) => call.url.includes('/v1/trending')).at(-1);
      expect(last?.url).toContain('window=24h');
      expect(last?.url).toContain('kind=rising');
    });
  });

  it('refreshes the mixes on demand', async () => {
    const refreshed = {
      sections: [
        { id: 'pl-9', kind: 'playlist', title: 'کشف هفتگی', playlist_id: 9, items: [] },
      ],
    };
    const api = mockApi(
      routes({ 'POST /v1/discover/refresh': refreshed, 'GET /v1/playlists/9': playlistDetail }),
    );
    renderApp(<Discover />);

    await userEvent.click(await screen.findByText('به‌روزرسانی'));
    await waitFor(() =>
      expect(api.calls.some((call) => call.url.includes('/discover/refresh'))).toBe(true),
    );
  });

  it('says so when there is nothing to recommend yet', async () => {
    mockApi(
      routes({ 'GET /v1/discover': { sections: [] }, 'GET /v1/trending': { items: [], next_cursor: null } }),
    );
    renderApp(<Discover />);
    expect(await screen.findByText('هنوز چیزی برای پیشنهاد نیست')).toBeInTheDocument();
  });

  it('recovers from a failed feed', async () => {
    mockApi(
      routes({
        'GET /v1/discover': new Response(
          JSON.stringify({ error: { code: 'unavailable', message: 'down' } }),
          { status: 503 },
        ),
      }),
    );
    renderApp(<Discover />);
    expect(await screen.findByText('دوباره امتحان کن')).toBeInTheDocument();
  });
});

describe('radio', () => {
  it('starts a station from the track sheet', async () => {
    const station = [makeTrack({ id: 41, title: 'شروع ایستگاه' }), makeTrack({ id: 42 })];
    mockApi({
      ...authRoutes(),
      'GET /v1/tracks/41/radio': { items: station, next_cursor: null },
      'GET /v1/playlists': [],
      'POST /v1/tracks/thumbs': { items: {} },
      'GET /v1/tracks/41/stream': {
        url: 'blob:x',
        thumb_url: null,
        expires_at: Math.floor(Date.now() / 1000) + 300,
        size: 1,
        mime: 'audio/mpeg',
      },
    });
    renderApp(<TrackActions track={station[0]!} open onClose={() => {}} />);

    await userEvent.click(await screen.findByText('رادیو بر اساس این آهنگ'));
    await waitFor(() => expect(usePlayer.getState().queue).toHaveLength(2));
    expect(usePlayer.getState().current?.id).toBe(41);
  });
});
