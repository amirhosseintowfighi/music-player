import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { CreditsSheet } from '@/components/Credits';
import { DevicesSheet } from '@/components/Devices';
import { PinButton } from '@/components/PinButton';
import { TrackActions } from '@/components/TrackActions';
import { CARD_H, CARD_W, drawCard } from '@/lib/shareCard';
import { resetForTests } from '@/player/engine';
import { deviceId, useConnect } from '@/store/connect';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { makeTrack, mockApi, renderApp } from './helpers';

vi.mock('@/lib/telegram', async () => {
  const actual = await vi.importActual<typeof import('@/lib/telegram')>('@/lib/telegram');
  return { ...actual, openTelegramLink: () => undefined, haptic: () => undefined };
});

const song = makeTrack({ id: 7, title: 'Pol' });
const next = makeTrack({ id: 8, title: 'Khaneh' });

beforeEach(() => {
  resetForTests();
  __setAuthForTests('token');
  useConnect.setState({ devices: [], running: false });
  usePlayer.setState({
    queue: [],
    manual: [],
    index: 0,
    current: null,
    isPlaying: false,
    isLoading: false,
    remote: null,
    error: null,
    position: 0,
  });
  useUi.setState({ toasts: [] });
});

describe('snooze', () => {
  it('asks for thirty days and takes the song out of what is coming', async () => {
    const api = mockApi({
      'GET /v1/me/hidden': [],
      'GET /v1/tracks/7/similar': { items: [], next_cursor: null },
      'GET /v1/playlists': [],
      'PUT /v1/tracks/7/hide': { track_id: 7, until: '2026-10-28T00:00:00Z' },
    });
    usePlayer.setState({ queue: [makeTrack({ id: 1 }), song], index: 0, current: makeTrack({ id: 1 }) });
    renderApp(<TrackActions track={song} open onClose={() => undefined} />);
    await userEvent.click(await screen.findByText('۳۰ روز نشانم نده'));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT' && c.url.endsWith('/hide'))).toBe(true));
    expect(api.calls.find((c) => c.method === 'PUT')?.body).toEqual({ snooze_days: 30 });
    await waitFor(() => expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1]));
  });
});

describe('pins', () => {
  it('pins and tells the listener when the four places are full', async () => {
    mockApi({
      'GET /v1/me/pins': [],
      'PUT /v1/me/pins/artist/3': () =>
        new Response(JSON.stringify({ error: { code: 'conflict', message: 'full' } }), { status: 409 }),
    });
    renderApp(<PinButton kind="artist" refId={3} />);
    const button = await screen.findByRole('button', { name: 'سنجاق کردن' });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    await waitFor(() => expect(useUi.getState().toasts.at(-1)?.text).toContain('حداکثر'));
  });
});

describe('credits', () => {
  it('lists people by role and the channels that carry the song', async () => {
    const credited = makeTrack({
      id: 7,
      title: 'Pol',
      artists: [
        { id: 2, name: 'Googoosh', role: 'primary' },
        { id: 9, name: 'Varoujan', role: 'composer' },
      ],
    });
    mockApi({
      'GET /v1/tracks/7/credits': {
        track: credited,
        genre: 'Pop',
        file_name: 'pol.mp3',
        mime_type: 'audio/mpeg',
        file_size: 5 * 1024 * 1024,
        first_posted_at: '2020-01-01T00:00:00Z',
        channels: 3,
        sources: [
          { channel_id: 1, username: 'oldies', title: 'Oldies', subscribers_count: 1200, posted_at: '2020-01-01T00:00:00Z' },
        ],
      },
    });
    renderApp(<CreditsSheet track={credited} open onClose={() => undefined} />);
    expect(await screen.findByText('Varoujan')).toBeInTheDocument();
    expect(screen.getByText('آهنگساز')).toBeInTheDocument();
    expect(await screen.findByText('Oldies')).toBeInTheDocument();
    expect(screen.getByText('MPEG · 5.0 MB')).toBeInTheDocument();
  });
});

describe('connect', () => {
  it('keeps one id per device', () => {
    expect(deviceId()).toMatch(/^[A-Za-z0-9_-]{6,64}$/);
    expect(deviceId()).toBe(deviceId());
  });

  it('plays what another device hands over, from where it was', async () => {
    const follow = vi.fn(async () => undefined);
    usePlayer.setState({ follow });
    await useConnect.getState().apply({
      action: 'transfer',
      sender: 'phone-1',
      index: 1,
      position_s: 42,
      playing: true,
      items: [song, next],
    });
    expect(follow).toHaveBeenCalledWith([song, next], 1, expect.any(Function), true);
    const positionAt = (follow.mock.calls[0] as unknown as [unknown, unknown, () => number])[2];
    expect(positionAt()).toBe(42);
  });

  it('ignores commands while in a Jam', async () => {
    const follow = vi.fn(async () => undefined);
    usePlayer.setState({
      follow,
      remote: { seek: vi.fn(), next: vi.fn(), previous: vi.fn(), toggle: vi.fn(), ended: vi.fn(), enqueue: vi.fn(), play: vi.fn() },
    });
    await useConnect.getState().apply({ action: 'transfer', sender: 'x', index: 0, position_s: 0, playing: true, items: [song] });
    expect(follow).not.toHaveBeenCalled();
  });

  it('hands the music to another device and stops here', async () => {
    const api = mockApi({ 'POST /v1/connect/command': {}, 'POST /v1/connect/heartbeat': { devices: [], commands: [] } });
    const toggle = vi.fn(async () => {
      usePlayer.setState({ isPlaying: false });
    });
    usePlayer.setState({ current: song, queue: [song, next], index: 0, isPlaying: true, position: 61.4, toggle });
    useConnect.setState({
      devices: [{ id: 'laptop-1', name: 'Telegram Desktop', kind: 'desktop', playing: false, position_s: 0, track: null }],
    });
    renderApp(<DevicesSheet open onClose={() => undefined} />);
    await userEvent.click(await screen.findByText('Telegram Desktop'));
    await waitFor(() => expect(toggle).toHaveBeenCalled());
    const sent = api.calls.find((c) => c.url.endsWith('/v1/connect/command'))?.body as Record<string, unknown>;
    expect(sent).toMatchObject({ target: 'laptop-1', action: 'transfer', track_ids: [7, 8], position_s: 61 });
  });

  it('says so when nothing else is open', async () => {
    renderApp(<DevicesSheet open onClose={() => undefined} />);
    expect(await screen.findByText('دستگاه دیگری پیدا نشد')).toBeInTheDocument();
  });
});

describe('share card', () => {
  it('draws a story-sized card', async () => {
    const draw = {
      fillRect: vi.fn(),
      fillText: vi.fn(),
      measureText: () => ({ width: 10 }),
      createLinearGradient: () => ({ addColorStop: vi.fn() }),
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      arcTo: vi.fn(),
      closePath: vi.fn(),
      fill: vi.fn(),
      clip: vi.fn(),
      drawImage: vi.fn(),
    };
    const spy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(draw as unknown as CanvasRenderingContext2D);
    const canvas = await drawCard(makeTrack({ id: 7, title: 'Pol', palette: '#112233,#445566,#778899' }), undefined, 'bot');
    expect(canvas?.width).toBe(CARD_W);
    expect(canvas?.height).toBe(CARD_H);
    expect(draw.fillText).toHaveBeenCalledWith('Pol', CARD_W / 2, expect.any(Number));
    expect(draw.fillText).toHaveBeenCalledWith('♪  @bot', CARD_W / 2, expect.any(Number));
    spy.mockRestore();
  });
});
