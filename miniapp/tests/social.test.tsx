import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { Profile, FriendsFeedScreen } from '@/screens/Profile';
import { Settings } from '@/screens/Settings';
import { Wrapped } from '@/screens/Wrapped';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeMe, makeTrack, mockApi, renderApp } from './helpers';

const track = makeTrack({ id: 5, title: 'شب بارونی' });

const theirProfile = {
  user_id: 42,
  first_name: 'علی',
  username: 'ali',
  is_pro: true,
  followers: 3,
  following: 10,
  playlists: 2,
  tracks_played: 120,
  joined_at: '2026-01-05T00:00:00Z',
  is_me: false,
  is_following: false,
  top_artists: [{ id: 1, name: 'معین', plays: 40 }],
  top_tracks: [track],
  public_playlists: [{ id: 9, name: 'شب‌ها', tracks_count: 12, share_slug: null }],
};

function routes(extra: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'GET /v1/users/42/profile': theirProfile,
    'GET /v1/me/profile': { ...theirProfile, user_id: 7, is_me: true, first_name: 'سارا' },
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

describe('profile', () => {
  it('shows someone else’s profile with a follow button', async () => {
    mockApi(routes());
    renderApp(<Profile />, { route: '/user/42', path: '/user/:id' });

    expect(await screen.findByText('علی')).toBeInTheDocument();
    expect(screen.getByText('@ali')).toBeInTheDocument();
    expect(screen.getByText('دنبال کردن')).toBeInTheDocument();
    expect(screen.getByText('معین')).toBeInTheDocument();
    expect(screen.getByText('شب بارونی')).toBeInTheDocument();
  });

  it('follows optimistically and rolls back when the server refuses', async () => {
    const api = mockApi(
      routes({
        'PUT /v1/users/42/follow': new Response(
          JSON.stringify({ error: { code: 'not_found', message: 'gone' } }),
          { status: 404 },
        ),
      }),
    );
    renderApp(<Profile />, { route: '/user/42', path: '/user/:id' });

    await userEvent.click(await screen.findByText('دنبال کردن'));
    // The optimistic bump is rolled back, so the count ends where it started and the
    // button is still an invitation to follow.
    await waitFor(() => expect(screen.getByText('۳')).toBeInTheDocument());
    expect(screen.getByText('دنبال کردن')).toBeInTheDocument();
    expect(api.calls.some((call) => call.method === 'PUT')).toBe(true);
  });

  it('unfollows through the same button', async () => {
    const api = mockApi(
      routes({
        'GET /v1/users/42/profile': { ...theirProfile, is_following: true },
        'DELETE /v1/users/42/follow': { following: false, changed: true },
      }),
    );
    renderApp(<Profile />, { route: '/user/42', path: '/user/:id' });

    await userEvent.click(await screen.findByText('دنبال نکن'));
    await waitFor(() =>
      expect(api.calls.some((call) => call.method === 'DELETE')).toBe(true),
    );
  });

  it('opens the followers list', async () => {
    mockApi(
      routes({
        'GET /v1/users/42/followers': [
          { user_id: 8, first_name: 'مریم', username: null, is_pro: false, is_following: false },
        ],
      }),
    );
    renderApp(<Profile />, { route: '/user/42', path: '/user/:id' });

    await userEvent.click(await screen.findByText('دنبال‌کننده'));
    expect(await screen.findByText('مریم')).toBeInTheDocument();
  });

  it('explains a hidden profile instead of showing an error', async () => {
    mockApi(
      routes({
        'GET /v1/users/42/profile': new Response(
          JSON.stringify({ error: { code: 'not_found', message: 'gone' } }),
          { status: 404 },
        ),
      }),
    );
    renderApp(<Profile />, { route: '/user/42', path: '/user/:id' });
    expect(await screen.findByText('این پروفایل در دسترس نیست')).toBeInTheDocument();
  });

  it('shows the Wrapped entry only on my own profile', async () => {
    mockApi(routes());
    const { unmount } = renderApp(<Profile />, { route: '/user/42', path: '/user/:id' });
    await screen.findByText('علی');
    expect(screen.queryByText(/مرور سال/)).not.toBeInTheDocument();
    unmount();

    renderApp(<Profile />);
    expect(await screen.findByText(/مرور سال/)).toBeInTheDocument();
  });
});

describe('friends feed', () => {
  it('lists what friends played and starts the whole feed as a queue', async () => {
    mockApi(
      routes({
        'GET /v1/social/feed': [
          {
            user_id: 42,
            first_name: 'علی',
            username: 'ali',
            played_at: '2026-09-18T10:00:00Z',
            track,
          },
        ],
      }),
    );
    renderApp(<FriendsFeedScreen />);

    expect(await screen.findByText('علی گوش داد')).toBeInTheDocument();
    await userEvent.click(screen.getByText('شب بارونی'));
    await waitFor(() => expect(usePlayer.getState().current?.id).toBe(5));
  });

  it('says so when nobody is followed yet', async () => {
    mockApi(routes({ 'GET /v1/social/feed': [] }));
    renderApp(<FriendsFeedScreen />);
    expect(await screen.findByText('هنوز فعالیتی نیست')).toBeInTheDocument();
  });
});

describe('wrapped', () => {
  const wrapped = {
    year: 2026,
    plays: 812,
    minutes: 2140,
    unique_tracks: 190,
    active_days: 143,
    top_artists: [{ id: 1, name: 'معین', plays: 90 }],
    top_tracks: [{ id: 5, title: 'شب بارونی', plays: 30 }],
    busiest_day: { day: '2026-03-21', plays: 44 },
    tracks: [track],
  };

  it('shows the year summary', async () => {
    mockApi(routes({ 'GET /v1/me/wrapped': wrapped }));
    renderApp(<Wrapped />);

    expect(await screen.findByText('سال ۲۰۲۶ تو')).toBeInTheDocument();
    expect(screen.getByText('۲٬۱۴۰')).toBeInTheDocument();
    expect(screen.getByText('خواننده‌های سال')).toBeInTheDocument();
    expect(screen.getByText(/۲۰۲۶-۰۳-۲۱|2026-03-21/)).toBeInTheDocument();
  });

  it('handles a year with no listening', async () => {
    mockApi(
      routes({
        'GET /v1/me/wrapped': { ...wrapped, plays: 0, minutes: 0, top_artists: [], tracks: [] },
      }),
    );
    renderApp(<Wrapped />);
    expect(await screen.findByText('امسال هنوز چیزی گوش نداده‌ای.')).toBeInTheDocument();
  });
});

describe('settings', () => {
  it('toggles the public profile switch', async () => {
    const api = mockApi({
      ...authRoutes(makeMe({ public_profile: true })),
      'GET /v1/me/notifications': { prefs: { new_tracks: true, digest: true } },
      'PUT /v1/me/public-profile': {},
    });
    renderApp(<Settings />);

    const switches = await screen.findAllByRole('switch');
    expect(switches[0]).toBeChecked();
    await userEvent.click(switches[0]!);

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.includes('public-profile'));
      expect(call?.body).toEqual({ public: false });
    });
  });

  it('saves a notification preference', async () => {
    const api = mockApi({
      ...authRoutes(),
      'GET /v1/me/notifications': {
        prefs: {
          new_tracks: true,
          digest: true,
          discover_ready: true,
          sub_expiry: true,
          payment: true,
          system: true,
        },
      },
      'PUT /v1/me/notifications': { prefs: { digest: false } },
    });
    renderApp(<Settings />);

    await screen.findByText('خلاصهٔ هفتگی');
    const switches = await screen.findAllByRole('switch');
    await userEvent.click(switches[2]!); // public profile, new_tracks, digest

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.endsWith('/v1/me/notifications') && entry.method === 'PUT');
      expect(call?.body).toEqual({ prefs: { digest: false } });
    });
  });
});
