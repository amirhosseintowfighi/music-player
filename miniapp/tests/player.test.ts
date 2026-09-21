import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { audio, resetForTests, ticketFor } from '@/player/engine';
import { usePlayer } from '@/store/player';

import { makeTrack, mockApi } from './helpers';

const ticket = (id: number, ttl = 300) => ({
  url: `https://cdn.test/s/${id}?t=token-${id}`,
  thumb_url: null,
  expires_at: Math.floor(Date.now() / 1000) + ttl,
  size: 4_000_000,
  mime: 'audio/mpeg',
});

function streamRoutes() {
  return {
    'POST /v1/tracks/1/stream': ticket(1),
    'POST /v1/tracks/2/stream': ticket(2),
    'POST /v1/tracks/3/stream': ticket(3),
  };
}

beforeEach(() => {
  resetForTests();
  __setAuthForTests('token');
  usePlayer.setState({
    queue: [],
    index: 0,
    current: null,
    isPlaying: false,
    isLoading: false,
    position: 0,
    duration: 0,
    shuffle: false,
    repeat: 'off',
    speed: 1,
    sleepAt: null,
    sleepEndOfTrack: false,
    error: null,
    onPlayed: null,
  });
});

const tracks = [makeTrack({ id: 1 }), makeTrack({ id: 2, title: 'پل' }), makeTrack({ id: 3, title: 'خانه' })];

describe('queue', () => {
  it('plays a track and loads its signed url', async () => {
    mockApi(streamRoutes());
    await usePlayer.getState().play({ queue: tracks, index: 1, source: 'library' });
    expect(usePlayer.getState().current?.id).toBe(2);
    expect(audio().src).toContain('/s/2');
  });

  it('advances, wraps only with repeat all, and repeats one in place', async () => {
    mockApi(streamRoutes());
    await usePlayer.getState().play({ queue: tracks, index: 2, source: 'library' });

    await usePlayer.getState().next(true); // last track, repeat off → stops
    expect(usePlayer.getState().index).toBe(2);
    expect(usePlayer.getState().isPlaying).toBe(false);

    usePlayer.setState({ repeat: 'all' });
    await usePlayer.getState().next(true);
    expect(usePlayer.getState().index).toBe(0);

    usePlayer.setState({ repeat: 'one' });
    const element = audio();
    element.currentTime = 42;
    await usePlayer.getState().next(true);
    expect(usePlayer.getState().index).toBe(0);
    expect(element.currentTime).toBe(0);
  });

  it('previous restarts the track after 3 seconds, otherwise steps back', async () => {
    mockApi(streamRoutes());
    await usePlayer.getState().play({ queue: tracks, index: 1, source: 'library' });
    usePlayer.setState({ position: 10 });
    await usePlayer.getState().previous();
    expect(usePlayer.getState().index).toBe(1);

    usePlayer.setState({ position: 1 });
    await usePlayer.getState().previous();
    expect(usePlayer.getState().index).toBe(0);
  });

  it('shuffle keeps the current track first', async () => {
    mockApi(streamRoutes());
    await usePlayer.getState().play({ queue: tracks, index: 2, source: 'library' });
    usePlayer.getState().setShuffle(true);
    const state = usePlayer.getState();
    expect(state.queue[0]?.id).toBe(3);
    expect(state.index).toBe(0);
    expect([...state.queue].map((t) => t.id).sort()).toEqual([1, 2, 3]);
  });

  it('edits the queue without losing the current track', async () => {
    mockApi(streamRoutes());
    await usePlayer.getState().play({ queue: tracks, index: 0, source: 'library' });
    // Queueing by hand never rewrites the album being played (ADR-003 phase 11).
    usePlayer.getState().enqueue([makeTrack({ id: 9 })], 'next');
    expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1, 2, 3]);
    expect(usePlayer.getState().manual.map((t) => t.id)).toEqual([9]);
    expect(usePlayer.getState().upcoming().map((t) => t.id)).toEqual([9, 2, 3]);

    usePlayer.getState().removeManual(0);
    expect(usePlayer.getState().manual).toEqual([]);
    usePlayer.getState().removeFromQueue(0); // the playing track is protected
    expect(usePlayer.getState().queue).toHaveLength(3);
    usePlayer.getState().moveInQueue(0, 2);
    expect(usePlayer.getState().index).toBe(2);
  });

  it('reports plays longer than 5 seconds to the history hook', async () => {
    mockApi(streamRoutes());
    const played = vi.fn();
    usePlayer.setState({ onPlayed: played });
    await usePlayer.getState().play({ queue: tracks, index: 0, source: 'search' });

    usePlayer.getState().reportPlayed(); // nothing listened yet
    expect(played).not.toHaveBeenCalled();

    // Simulate 40 seconds of playback through the element's timeupdate handler.
    const detach = usePlayer.getState().attach();
    const element = audio();
    Object.defineProperty(element, 'paused', { configurable: true, value: false });
    vi.useFakeTimers();
    element.dispatchEvent(new Event('timeupdate')); // first tick only sets the baseline
    for (let i = 0; i < 4; i += 1) {
      vi.advanceTimersByTime(2000);
      element.dispatchEvent(new Event('timeupdate'));
    }
    vi.useRealTimers();
    usePlayer.getState().reportPlayed(true);
    expect(played).toHaveBeenCalledWith(tracks[0], expect.any(Number), true, 'search', null);
    detach();
  });
});

describe('tickets', () => {
  it('caches a ticket until it is close to expiry', async () => {
    const { calls } = mockApi(streamRoutes());
    await ticketFor(1);
    await ticketFor(1);
    expect(calls.filter((c) => c.url.includes('/stream'))).toHaveLength(1);
  });

  it('re-requests a ticket that is about to expire', async () => {
    const { calls } = mockApi({ 'POST /v1/tracks/1/stream': ticket(1, 5) });
    await ticketFor(1);
    await ticketFor(1);
    expect(calls.filter((c) => c.url.includes('/stream'))).toHaveLength(2);
  });
});

describe('phase 10: warming up, telemetry and retries', () => {
  it('asks for a cheap ticket when warming the next track', async () => {
    const api = mockApi({
      ...streamRoutes(),
      'POST /v1/telemetry/playback': {},
    });
    const { prefetch } = await import('@/player/engine');

    await prefetch(2, 1024);

    const call = api.calls.find((entry) => entry.url.includes('/v1/tracks/2/stream'));
    expect(call?.url).toContain('prefetch=1');
    // And it only pulls the head of the file.
    const range = api.calls.find((entry) => entry.url.startsWith('https://cdn.test/s/2'));
    expect(range).toBeDefined();
  });

  it('never plays a track with a prefetch ticket', async () => {
    const api = mockApi({ ...streamRoutes(), 'POST /v1/telemetry/playback': {} });
    const { prefetch, ticketFor: get } = await import('@/player/engine');

    await prefetch(1, 512);
    await get(1); // the real play

    const stream = api.calls.filter((entry) => entry.url.includes('/v1/tracks/1/stream'));
    expect(stream.map((entry) => entry.url.includes('prefetch=1'))).toEqual([true, false]);
  });

  it('reports time to first sound, stalls and failures', async () => {
    const api = mockApi({ ...streamRoutes(), 'POST /v1/telemetry/playback': {} });
    const detach = usePlayer.getState().attach();
    await usePlayer.getState().play({ queue: tracks, index: 0, source: 'library' });

    audio().dispatchEvent(new Event('playing'));
    usePlayer.setState({ isPlaying: true });
    audio().dispatchEvent(new Event('waiting'));
    audio().dispatchEvent(new Event('error'));
    await new Promise((resolve) => setTimeout(resolve, 0)); // reports are fire-and-forget
    detach();

    const reported = api.calls
      .filter((entry) => entry.url.endsWith('/v1/telemetry/playback'))
      .map((entry) => (entry.body as { kind: string }).kind);
    expect(reported).toContain('start');
    expect(reported).toContain('underrun');
    expect(reported).toContain('error');
  });

  it('retries a network failure with backoff, then gives up with a clear error', async () => {
    vi.useFakeTimers();
    let attempts = 0;
    mockApi({
      'POST /v1/telemetry/playback': {},
      'POST /v1/tracks/1/stream': () => {
        attempts += 1;
        return new Response(JSON.stringify({ error: { code: 'error', message: 'boom' } }), {
          status: 500,
        });
      },
    });

    const playing = usePlayer.getState().play({ queue: tracks, index: 0, source: 'library' });
    await vi.runAllTimersAsync();
    await playing;
    vi.useRealTimers();

    expect(attempts).toBe(4); // the first try plus three retries
    expect(usePlayer.getState().error?.kind).toBe('network');
    expect(usePlayer.getState().isLoading).toBe(false); // never stuck on a spinner
  });

  it('does not retry a plan limit — it tells the user', async () => {
    let attempts = 0;
    mockApi({
      'POST /v1/telemetry/playback': {},
      'POST /v1/tracks/1/stream': () => {
        attempts += 1;
        return new Response(
          JSON.stringify({
            error: { code: 'plan_limit', message: 'no', details: { kind: 'daily_plays', limit: 5 } },
          }),
          { status: 402 },
        );
      },
    });

    await usePlayer.getState().play({ queue: tracks, index: 0, source: 'library' });

    expect(attempts).toBe(1);
    expect(usePlayer.getState().error?.kind).toBe('plan_limit');
  });
});

describe('phase 11: two queues, shuffle, and never stopping dead', () => {
  const radio = (ids: number[]) => ({ items: ids.map((id) => makeTrack({ id })), next_cursor: null });

  it('plays the manual queue before the source continues', async () => {
    mockApi({
      ...streamRoutes(),
      'POST /v1/tracks/9/stream': ticket(9),
      'POST /v1/telemetry/playback': {},
    });
    const player = usePlayer.getState();
    await player.play({ queue: tracks, index: 0, source: 'library' });
    player.enqueue([makeTrack({ id: 9 })], 'next');

    await usePlayer.getState().next();
    expect(usePlayer.getState().current?.id).toBe(9);
    expect(usePlayer.getState().playingManual).toBe(true);
    // The source did not move: it is still sitting on track 1.
    expect(usePlayer.getState().index).toBe(0);

    // With the manual queue empty, the source picks up where it left off.
    await usePlayer.getState().next();
    expect(usePlayer.getState().current?.id).toBe(2);
    expect(usePlayer.getState().playingManual).toBe(false);
    expect(usePlayer.getState().index).toBe(1);
  });

  it('goes back from a manual track to the source track it interrupted', async () => {
    mockApi({ ...streamRoutes(), 'POST /v1/tracks/9/stream': ticket(9), 'POST /v1/telemetry/playback': {} });
    await usePlayer.getState().play({ queue: tracks, index: 1, source: 'library' });
    usePlayer.getState().enqueue([makeTrack({ id: 9 })], 'next');
    await usePlayer.getState().next();
    expect(usePlayer.getState().current?.id).toBe(9);

    usePlayer.setState({ position: 0 });
    await usePlayer.getState().previous();
    expect(usePlayer.getState().current?.id).toBe(2); // where the source was
    expect(usePlayer.getState().playingManual).toBe(false);
  });

  it('restores the original order when shuffle is turned off', async () => {
    mockApi(streamRoutes());
    const long = [1, 2, 3, 4, 5, 6, 7, 8].map((id) => makeTrack({ id }));
    mockApi({
      ...streamRoutes(),
      ...Object.fromEntries(long.map((t) => [`POST /v1/tracks/${t.id}/stream`, ticket(t.id)])),
      'POST /v1/telemetry/playback': {},
    });
    await usePlayer.getState().play({ queue: long, index: 3, source: 'playlist' });

    usePlayer.getState().setShuffle(true);
    const shuffledIds = usePlayer.getState().queue.map((t) => t.id);
    expect(shuffledIds[0]).toBe(4); // the playing track stays put
    expect(shuffledIds).not.toEqual([1, 2, 3, 4, 5, 6, 7, 8]);

    usePlayer.getState().setShuffle(false);
    expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    // And it still knows which track is playing inside the restored order.
    expect(usePlayer.getState().index).toBe(3);
    expect(usePlayer.getState().current?.id).toBe(4);
  });

  it('keeps playing with a station when the queue runs out', async () => {
    mockApi({
      ...streamRoutes(),
      'GET /v1/tracks/3/radio': radio([41, 42]),
      'POST /v1/tracks/41/stream': ticket(41),
      'POST /v1/telemetry/playback': {},
    });
    await usePlayer.getState().play({ queue: tracks, index: 2, source: 'library' });

    await usePlayer.getState().next(true);

    expect(usePlayer.getState().current?.id).toBe(41);
    expect(usePlayer.getState().source).toBe('radio');
    expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1, 2, 3, 41, 42]);
  });

  it('stops cleanly when autoplay is off or there is no station', async () => {
    mockApi({
      ...streamRoutes(),
      'GET /v1/tracks/3/radio': { items: [], next_cursor: null },
      'POST /v1/telemetry/playback': {},
    });
    await usePlayer.getState().play({ queue: tracks, index: 2, source: 'library' });

    await usePlayer.getState().next(true);
    expect(usePlayer.getState().current?.id).toBe(3); // the last track, not a crash
    expect(usePlayer.getState().isPlaying).toBe(false);

    usePlayer.getState().setAutoplay(false);
    await usePlayer.getState().play({ queue: tracks, index: 2, source: 'library' });
    await usePlayer.getState().next(true);
    expect(usePlayer.getState().current?.id).toBe(3);
  });

  it('fades in instead of starting at full volume', async () => {
    vi.useFakeTimers();
    mockApi({ ...streamRoutes(), 'POST /v1/telemetry/playback': {} });
    usePlayer.getState().setVolume(0.8);

    const playing = usePlayer.getState().play({ queue: tracks, index: 0, source: 'library' });
    await vi.advanceTimersByTimeAsync(0);
    await playing;
    expect(audio().volume).toBeLessThan(0.8); // ramping, not slammed on

    await vi.advanceTimersByTimeAsync(400);
    expect(audio().volume).toBeCloseTo(0.8, 2); // and it lands on the user's volume
    vi.useRealTimers();
  });

  it('prefetches whatever actually plays next, manual queue included', async () => {
    const api = mockApi({
      ...streamRoutes(),
      'POST /v1/tracks/9/stream': ticket(9),
      'POST /v1/telemetry/playback': {},
    });
    const detach = usePlayer.getState().attach();
    await usePlayer.getState().play({ queue: tracks, index: 0, source: 'library' });
    usePlayer.getState().enqueue([makeTrack({ id: 9 })], 'next');

    audio().dispatchEvent(new Event('playing'));
    await new Promise((resolve) => setTimeout(resolve, 0));
    detach();

    const warmed = api.calls.find((call) => call.url.includes('prefetch=1'));
    expect(warmed?.url).toContain('/v1/tracks/9/stream'); // not track 2
  });
});
