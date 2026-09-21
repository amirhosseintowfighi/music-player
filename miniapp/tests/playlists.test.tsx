import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { TrackActions } from '@/components/TrackActions';
import { LikedScreen, PlaylistScreen, Playlists, SharedPlaylistScreen } from '@/screens/Playlists';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const tracks = [
  makeTrack({ id: 1, title: 'شب بارونی' }),
  makeTrack({ id: 2, title: 'پل' }),
  makeTrack({ id: 3, title: 'خانه' }),
];

const playlist = {
  id: 7,
  name: 'شب‌های بارونی',
  description: null,
  kind: 'manual' as const,
  is_public: false,
  is_collaborative: false,
  share_slug: null,
  share_url: null,
  tracks_count: 3,
  duration_total: 735,
  is_owner: true,
  can_edit: true,
  updated_at: '2026-09-18T10:00:00Z',
};

beforeEach(() => {
  __setAuthForTests('token');
  usePlayer.setState({ queue: [], current: null, index: 0, isPlaying: false });
  useUi.setState({ toasts: [], upsell: null, actionTrack: null });
  window.location.hash = '#/';
});

function baseRoutes(overrides: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'POST /v1/tracks/thumbs': { items: {} },
    'GET /v1/playlists': [playlist],
    'GET /v1/playlists/7': { ...playlist, items: tracks },
    ...overrides,
  };
}

describe('playlists', () => {
  it('lists playlists with the liked shortcut', async () => {
    mockApi(baseRoutes());
    renderApp(<Playlists />);
    expect(await screen.findByText('شب‌های بارونی')).toBeInTheDocument();
    expect(screen.getByText('آهنگ‌های لایک‌شده')).toBeInTheDocument();
  });

  it('creates a playlist', async () => {
    const { calls } = mockApi(
      baseRoutes({ 'POST /v1/playlists': { ...playlist, id: 8, name: 'تازه', items: [] } }),
    );
    renderApp(<Playlists />);
    await userEvent.click(screen.getByLabelText('پلی‌لیست جدید'));
    await userEvent.type(await screen.findByLabelText('نام پلی‌لیست'), 'تازه');
    await userEvent.click(screen.getByText('ذخیره'));
    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST' && call.url === '/v1/playlists')?.body).toEqual({
        name: 'تازه',
        track_ids: [],
      }),
    );
    expect(useUi.getState().toasts[0]?.text).toBe('پلی‌لیست ساخته شد');
  });

  it('shows the paywall when the free playlist limit is hit', async () => {
    mockApi(
      baseRoutes({
        'POST /v1/playlists': new Response(
          JSON.stringify({ error: { code: 'plan_limit', message: 'x', details: { kind: 'playlists', limit: 5 } } }),
          { status: 402 },
        ),
      }),
    );
    renderApp(<Playlists />);
    await userEvent.click(screen.getByLabelText('پلی‌لیست جدید'));
    await userEvent.type(await screen.findByLabelText('نام پلی‌لیست'), 'ششم');
    await userEvent.click(screen.getByText('ذخیره'));
    await waitFor(() => expect(useUi.getState().upsell).toEqual({ kind: 'playlists', limit: 5 }));
  });

  it('plays, removes and reorders inside a playlist', async () => {
    window.location.hash = '#/playlist/7';
    const { calls } = mockApi(
      baseRoutes({
        'POST /v1/tracks/1/stream': {
          url: 'https://cdn.test/s/1?t=x',
          thumb_url: null,
          expires_at: Math.floor(Date.now() / 1000) + 300,
          size: 10,
          mime: 'audio/mpeg',
        },
        'DELETE /v1/playlists/7/tracks/2': null,
        'POST /v1/playlists/7/move': null,
      }),
    );
    renderApp(<PlaylistScreen />, { route: '/playlist/7', path: '/playlist/:id' });

    await userEvent.click(await screen.findByText('پخش'));
    await waitFor(() => expect(usePlayer.getState().current?.id).toBe(1));
    expect(usePlayer.getState().source).toBe('playlist');

    const removeButtons = await screen.findAllByLabelText('حذف');
    await userEvent.click(removeButtons[1] as HTMLElement);
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/tracks/2'))).toBe(true),
    );
  });

  it('shares a playlist and copies the link', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    window.location.hash = '#/playlist/7';
    mockApi(
      baseRoutes({
        'PATCH /v1/playlists/7': {
          ...playlist,
          is_public: true,
          share_slug: 'abc1234567',
          share_url: 'https://t.me/bot?startapp=pl_abc1234567',
        },
      }),
    );
    renderApp(<PlaylistScreen />, { route: '/playlist/7', path: '/playlist/:id' });
    await userEvent.click(await screen.findByLabelText('بیشتر'));
    await userEvent.click(await screen.findByText('اشتراک‌گذاری و کپی لینک'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('https://t.me/bot?startapp=pl_abc1234567'));
    expect(useUi.getState().toasts.at(-1)?.text).toBe('لینک کپی شد');
  });

  it('opens a shared playlist read-only and can join a collaborative one', async () => {
    const shared = {
      ...playlist,
      is_owner: false,
      can_edit: false,
      is_public: true,
      is_collaborative: true,
      share_slug: 'abc1234567',
      items: tracks,
    };
    const { calls } = mockApi(
      baseRoutes({
        'GET /v1/playlists/shared/abc1234567': shared,
        'POST /v1/playlists/shared/abc1234567/join': { ...shared, can_edit: true },
      }),
    );
    renderApp(<SharedPlaylistScreen />, { route: '/shared/abc1234567', path: '/shared/:slug' });
    expect(await screen.findByText('شب‌های بارونی')).toBeInTheDocument();
    await userEvent.click(screen.getByText('پیوستن'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/join'))).toBe(true),
    );
    expect(useUi.getState().toasts.at(-1)?.text).toBe('به پلی‌لیست پیوستی');
  });
});

describe('likes and track actions', () => {
  it('lists liked tracks', async () => {
    mockApi(baseRoutes({ 'GET /v1/library/likes': { items: [tracks[0]], next_cursor: null } }));
    renderApp(<LikedScreen />);
    expect(await screen.findByText('شب بارونی')).toBeInTheDocument();
  });

  it('likes a track optimistically and adds it to a playlist', async () => {
    const { calls } = mockApi(
      baseRoutes({
        'PUT /v1/tracks/1/like': { liked: true, likes_count: 1 },
        'POST /v1/playlists/7/tracks': { ...playlist, tracks_count: 4 },
      }),
    );
    renderApp(<TrackActions track={tracks[0]!} open onClose={() => undefined} />);
    await userEvent.click(await screen.findByText('لایک'));
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'PUT' && call.url === '/v1/tracks/1/like')).toBe(true),
    );

    await userEvent.click(screen.getByText('افزودن به پلی‌لیست'));
    await userEvent.click(await screen.findByText('شب‌های بارونی'));
    await waitFor(() =>
      expect(
        calls.find((call) => call.method === 'POST' && call.url === '/v1/playlists/7/tracks')?.body,
      ).toEqual({ track_ids: [1] }),
    );
  });

  it('queues a track and sends it to the chat', async () => {
    const { calls } = mockApi(baseRoutes({ 'POST /v1/tracks/1/send': null }));
    usePlayer.setState({ queue: [tracks[1]!], index: 0, current: tracks[1]! });
    renderApp(<TrackActions track={tracks[0]!} open onClose={() => undefined} />);

    await userEvent.click(await screen.findByText('بعدی پخش شود'));
    // It lands in the manual queue, ahead of whatever the source would play next.
    expect(usePlayer.getState().manual.map((track) => track.id)).toEqual([1]);
    expect(usePlayer.getState().queue.map((track) => track.id)).toEqual([2]);

    renderApp(<TrackActions track={tracks[0]!} open onClose={() => undefined} />);
    await userEvent.click((await screen.findAllByText('ارسال به چت من'))[0] as HTMLElement);
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'POST' && call.url === '/v1/tracks/1/send')).toBe(true),
    );
  });
});
