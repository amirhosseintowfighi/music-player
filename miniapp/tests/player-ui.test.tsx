import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { FullPlayer } from '@/components/FullPlayer';
import { TrackActions } from '@/components/TrackActions';
import { resetForTests } from '@/player/engine';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { makeTrack, mockApi, renderApp } from './helpers';

const opened: string[] = [];
vi.mock('@/lib/telegram', async () => {
  const actual = await vi.importActual<typeof import('@/lib/telegram')>('@/lib/telegram');
  return {
    ...actual,
    openTelegramLink: (url: string) => opened.push(url),
    haptic: () => undefined,
  };
});

const channel = {
  id: 5,
  username: 'persianhits',
  title: 'Persian Hits',
  is_featured: true,
  subscribers_count: 12_000,
  joined: false,
};

const track = makeTrack({ id: 1, channel, channels_count: 3 });

beforeEach(() => {
  resetForTests();
  opened.length = 0;
  __setAuthForTests('token');
  usePlayer.setState({
    queue: [track],
    manual: [],
    index: 0,
    current: track,
    isPlaying: true,
    isLoading: false,
    position: 30,
    duration: 245,
  });
  useUi.setState({ playerOpen: true });
});

describe('channel attribution', () => {
  it('invites the listener into the channel that published the track', async () => {
    mockApi({});
    renderApp(<FullPlayer thumbs={{}} />);

    const chip = await screen.findByText(/Persian Hits/);
    await userEvent.click(chip);

    expect(opened).toEqual(['https://t.me/persianhits']);
    // And it says how many other channels carry it, without listing them.
    expect(screen.getByText(/\+۲/)).toBeInTheDocument();
  });

  it('says "from" instead of "join" for a channel the listener already has', async () => {
    mockApi({});
    usePlayer.setState({ current: { ...track, channel: { ...channel, joined: true } } });
    renderApp(<FullPlayer thumbs={{}} />);

    expect(await screen.findByText(/از Persian Hits/)).toBeInTheDocument();
  });

  it('shows no chip at all when nothing is known about the source', async () => {
    mockApi({});
    usePlayer.setState({ current: { ...track, channel: null, channels_count: 1 } });
    renderApp(<FullPlayer thumbs={{}} />);

    await screen.findByText(track.title);
    expect(screen.queryByText(/Persian Hits/)).not.toBeInTheDocument();
  });
});

describe('the track menu', () => {
  const baseRoutes = {
    'GET /v1/playlists': [],
    'GET /v1/tracks/1/similar': { items: [], next_cursor: null },
  };

  it('opens the source channel from the menu', async () => {
    mockApi(baseRoutes);
    renderApp(<TrackActions track={track} open onClose={() => undefined} />);

    await userEvent.click(await screen.findByText(/Persian Hits/));
    expect(opened).toEqual(['https://t.me/persianhits']);
  });

  it('shares a deep link that opens the app on this track', async () => {
    mockApi(baseRoutes);
    renderApp(<TrackActions track={track} open onClose={() => undefined} />);

    await userEvent.click(await screen.findByText('اشتراک‌گذاری در تلگرام'));
    const [url] = opened;
    expect(url).toContain('t.me/share/url');
    expect(decodeURIComponent(url ?? '')).toContain('startapp=tr_1');
  });

  it('reports a problem with one tap and a reason', async () => {
    const api = mockApi({ ...baseRoutes, 'POST /v1/tracks/1/report': { id: 3, status: 'open' } });
    renderApp(<TrackActions track={track} open onClose={() => undefined} />);

    await userEvent.click(await screen.findByText('گزارش مشکل'));
    await userEvent.click(await screen.findByText('عنوان یا خواننده اشتباه است'));

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.endsWith('/v1/tracks/1/report'));
      expect(call?.body).toEqual({ reason: 'wrong_metadata' });
    });
  });

  it('offers similar tracks inside the sheet', async () => {
    mockApi({
      ...baseRoutes,
      'GET /v1/tracks/1/similar': {
        items: [makeTrack({ id: 77, title: 'پل' })],
        next_cursor: null,
      },
    });
    renderApp(<TrackActions track={track} open onClose={() => undefined} />);

    expect(await screen.findByText('پل')).toBeInTheDocument();
  });

  it('hides the album entry for a track with no album', async () => {
    mockApi(baseRoutes);
    renderApp(<TrackActions track={track} open onClose={() => undefined} />);

    await screen.findByText('گزارش مشکل');
    expect(screen.queryByText('رفتن به آلبوم')).not.toBeInTheDocument();
  });
});

describe('the queue sheet', () => {
  it('does not put five hundred rows in the DOM at once', async () => {
    mockApi({});
    const many = Array.from({ length: 500 }, (_, i) => makeTrack({ id: i + 100, title: `T${i}` }));
    usePlayer.setState({ queue: [track, ...many], index: 0, current: track, manual: [] });
    renderApp(<FullPlayer thumbs={{}} />);

    await userEvent.click(await screen.findByRole('button', { name: 'صف پخش' }));
    const sheet = await screen.findByText('بعدی');
    const list = sheet.parentElement as HTMLElement;

    const rows = within(list).getAllByRole('listitem');
    expect(rows.length).toBeLessThanOrEqual(40);
    expect(rows.length).toBeGreaterThan(0);
  });

  it('shows the manual queue above what the source plays next', async () => {
    mockApi({});
    usePlayer.setState({
      queue: [track, makeTrack({ id: 2, title: 'از منبع' })],
      manual: [makeTrack({ id: 9, title: 'دستی' })],
      index: 0,
      current: track,
    });
    renderApp(<FullPlayer thumbs={{}} />);

    await userEvent.click(await screen.findByRole('button', { name: 'صف پخش' }));
    expect(await screen.findByText('در صف شما')).toBeInTheDocument();
    expect(screen.getByText('دستی')).toBeInTheDocument();
    expect(screen.getByText('از منبع')).toBeInTheDocument();
  });
});
