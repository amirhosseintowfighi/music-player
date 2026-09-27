import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { MiniPlayer } from '@/components/MiniPlayer';
import { resetForTests } from '@/player/engine';
import { JamScreen } from '@/screens/Jam';
import { useJam, type JamState } from '@/store/jam';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { makeMe, makeTrack, mockApi, renderApp } from './helpers';

const opened: string[] = [];
vi.mock('@/lib/telegram', async () => {
  const actual = await vi.importActual<typeof import('@/lib/telegram')>('@/lib/telegram');
  return { ...actual, openTelegramLink: (url: string) => opened.push(url), haptic: () => undefined };
});

const tracks = [makeTrack({ id: 1 }), makeTrack({ id: 2, title: 'پل' }), makeTrack({ id: 3, title: 'خانه' })];

const ticket = (id: number) => ({
  url: `https://cdn.test/s/${id}`,
  thumb_url: null,
  expires_at: Math.floor(Date.now() / 1000) + 300,
  size: 1,
  mime: 'audio/mpeg',
});

function jamState(overrides: Partial<JamState> = {}): JamState {
  return {
    code: 'abcdef',
    share_url: 'https://t.me/bot?startapp=jam_abcdef',
    host_id: 7,
    is_host: true,
    can_control: true,
    guests_can_control: true,
    members: [
      { user_id: 7, first_name: 'سارا', username: null, is_host: true },
      { user_id: 8, first_name: 'Ali', username: null, is_host: false },
    ],
    index: 1,
    playing: true,
    position_s: 20,
    rev: 3,
    qrev: 1,
    queue_length: 3,
    items: tracks.map((track, i) => ({ track, added_by: i === 2 ? 8 : 7 })),
    ...overrides,
  };
}

const streams = {
  'POST /v1/tracks/1/stream': ticket(1),
  'POST /v1/tracks/2/stream': ticket(2),
  'POST /v1/tracks/3/stream': ticket(3),
  'POST /v1/tracks/thumbs': { items: {} },
  'GET /v1/me': makeMe(),
};

beforeEach(() => {
  resetForTests();
  opened.length = 0;
  __setAuthForTests('token');
  useJam.getState().reset();
  usePlayer.setState({
    queue: tracks,
    manual: [],
    index: 0,
    current: tracks[0] ?? null,
    isPlaying: true,
    isLoading: false,
    position: 42,
    duration: 245,
    shuffle: false,
    repeat: 'off',
    source: 'library',
    remote: null,
    error: null,
  });
  useUi.setState({ playerOpen: false });
});

afterEach(() => {
  useJam.getState().reset();
});

describe('mini player', () => {
  it('has previous and next next to play', async () => {
    mockApi(streams);
    renderApp(<MiniPlayer />);
    // A stand-in remote records what the transport asked for.
    const next = vi.fn();
    const previous = vi.fn();
    usePlayer.setState({
      remote: { next, previous, toggle: vi.fn(), seek: vi.fn(), ended: vi.fn(), enqueue: vi.fn(), play: vi.fn() },
    });

    await userEvent.click(screen.getByRole('button', { name: 'آهنگ بعدی' }));
    await userEvent.click(screen.getByRole('button', { name: 'آهنگ قبلی' }));
    expect(next).toHaveBeenCalledTimes(1);
    expect(previous).toHaveBeenCalledTimes(1);
    // Tapping a transport button must not also open the full player.
    expect(useUi.getState().playerOpen).toBe(false);
  });
});

describe('jam sync', () => {
  it('starts a jam seeded with what is playing and follows the room', async () => {
    const { calls } = mockApi({ ...streams, 'POST /v1/jams': jamState() });
    await useJam.getState().start();

    const create = calls.find((c) => c.url.endsWith('/v1/jams'));
    expect(create?.body).toEqual({ track_ids: [1, 2, 3], index: 0, position_s: 42, playing: true });
    // The room is on track 2: the player moves there and hands its transport over.
    await waitFor(() => expect(usePlayer.getState().current?.id).toBe(2));
    expect(usePlayer.getState().remote).not.toBeNull();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/v1/tracks/2/stream'))).toBe(true));
  });

  it('turns the transport into commands for the room', async () => {
    const { calls } = mockApi({
      ...streams,
      'POST /v1/jams': jamState(),
      'POST /v1/jams/abcdef/control': (body: unknown) => {
        const action = (body as { action: string }).action;
        return jamState({ index: action === 'next' ? 2 : 1, items: null });
      },
      'POST /v1/jams/abcdef/queue': jamState({ queue_length: 4 }),
    });
    await useJam.getState().start();

    await usePlayer.getState().next();
    await waitFor(() => expect(useJam.getState().jam?.index).toBe(2));
    const control = calls.filter((c) => c.url.includes('/control'));
    expect(control[0]?.body).toEqual({ action: 'next' });
    // A queue revision we already have means the queue is kept, not dropped.
    expect(useJam.getState().items).toHaveLength(3);

    // The local copy ending asks for a skip that only happens once, however many report it.
    usePlayer.getState().remote?.ended();
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/control'))).toHaveLength(2));
    expect(calls.filter((c) => c.url.includes('/control'))[1]?.body).toEqual({ action: 'next', expected_index: 2 });

    // "Play next" from any list goes into the room's queue.
    usePlayer.getState().enqueue([makeTrack({ id: 9 })], 'next');
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/v1/jams/abcdef/queue'))).toBe(true));
    expect(calls.find((c) => c.url.endsWith('/queue'))?.body).toEqual({ track_ids: [9], position: 'next' });
  });

  it('lets go of the player when the jam ends', async () => {
    mockApi({ ...streams, 'POST /v1/jams': jamState() });
    await useJam.getState().start();
    // The room is gone: the next read says so.
    mockApi(streams);
    await useJam.getState().refresh();
    expect(useJam.getState().jam).toBeNull();
    expect(usePlayer.getState().remote).toBeNull();
  });
});

describe('jam screen', () => {
  it('starts a jam and invites friends', async () => {
    const { calls } = mockApi({ ...streams, 'POST /v1/jams': jamState() });
    renderApp(<JamScreen />, { route: '/jam', path: '/jam' });

    await userEvent.click(await screen.findByRole('button', { name: /شروع جم/ }));
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/v1/jams'))).toBe(true);

    expect(await screen.findByText('Ali')).toBeInTheDocument();
    expect(screen.getByText('۲ نفر در حال گوش دادن')).toBeInTheDocument();
    // The guest's track shows who added it.
    expect(await screen.findByText(/از طرف Ali/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /دعوت دوستان/ }));
    expect(opened[0]).toContain('https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fbot%3Fstartapp%3Djam_abcdef');
  });

  it('joins from an invite link', async () => {
    const { calls } = mockApi({
      ...streams,
      'POST /v1/jams/abcdef/join': jamState({ is_host: false, can_control: true }),
    });
    renderApp(<JamScreen />, { route: '/jam/abcdef', path: '/jam/:code' });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/v1/jams/abcdef/join'))).toBe(true));
    await waitFor(() => expect(useJam.getState().jam?.code).toBe('abcdef'));
  });

  it('says so when the invite is stale', async () => {
    mockApi(streams);
    renderApp(<JamScreen />, { route: '/jam/zzzzzz', path: '/jam/:code' });
    expect(await screen.findByText('این جم تمام شده یا لینکش اشتباه است.')).toBeInTheDocument();
  });
});
