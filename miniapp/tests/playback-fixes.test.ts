/**
 * The playback bugs listeners hit on phones: the next song not starting, the music
 * stopping halfway, a skip that left everything silent, and the lock-screen controls.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { allAudio, audio, preloadNext, resetForTests, unlockAudio } from '@/player/engine';
import { hiRes } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

import { makeTrack, mockApi } from './helpers';

const one = makeTrack({ id: 1, title: 'One', duration: 200 });
const two = makeTrack({ id: 2, title: 'Two', duration: 200 });

let issued = 0;
function ticket(id: number) {
  issued += 1;
  return {
    url: `https://cdn.test/s/${id}?n=${issued}`,
    thumb_url: `https://cdn.test/t/${id}?t=x`,
    expires_at: Math.floor(Date.now() / 1000) + 600,
    size: 1,
    mime: 'audio/mpeg',
  };
}

const handlers: Record<string, ((details: Record<string, unknown>) => void) | null> = {};

beforeEach(() => {
  resetForTests();
  issued = 0;
  __setAuthForTests('token');
  for (const key of Object.keys(handlers)) delete handlers[key];
  Object.defineProperty(navigator, 'mediaSession', {
    configurable: true,
    value: {
      metadata: null,
      playbackState: 'none',
      setActionHandler: (action: string, handler: ((d: Record<string, unknown>) => void) | null) => {
        handlers[action] = handler;
      },
      setPositionState: () => undefined,
    },
  });
  vi.stubGlobal(
    'MediaMetadata',
    class {
      constructor(readonly init: Record<string, unknown>) {}
    },
  );
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
    volume: 1,
    shuffle: false,
    repeat: 'off',
    autoplay: false,
  });
  mockApi({
    'POST /v1/tracks/1/stream': () => ticket(1),
    'POST /v1/tracks/2/stream': () => ticket(2),
    'POST /v1/telemetry/playback': {},
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('a song that fails halfway', () => {
  it('gets a fresh link and carries on from the same second', async () => {
    const detach = usePlayer.getState().attach();
    await usePlayer.getState().play({ queue: [one, two], index: 0, source: 'library' });
    const el = audio();
    expect(el.src).toContain('n=1');
    usePlayer.setState({ isPlaying: true });
    el.currentTime = 95;
    el.dispatchEvent(new Event('error'));
    await vi.waitFor(() => expect(el.src).toContain('n=2'));
    expect(el.currentTime).toBe(95);
    expect(usePlayer.getState().current?.id).toBe(1);
    expect(usePlayer.getState().error).toBeNull();
    detach();
  });

  it('gives up after a few tries and says so', async () => {
    const detach = usePlayer.getState().attach();
    await usePlayer.getState().play({ queue: [one], index: 0, source: 'library' });
    const el = audio();
    for (let i = 0; i < 4; i += 1) {
      el.dispatchEvent(new Event('error'));
      await vi.waitFor(() => expect(usePlayer.getState().isLoading).toBe(false));
    }
    expect(usePlayer.getState().error?.kind).toBe('network');
    detach();
  });
});

describe('the next song', () => {
  it('starts on the element that was playing when the spare is refused', async () => {
    const detach = usePlayer.getState().attach();
    await usePlayer.getState().play({ queue: [one, two], index: 0, source: 'library' });
    const first = audio();
    await preloadNext(two);
    const spare = allAudio().find((el) => el !== first) as HTMLAudioElement;
    // iOS: the spare was never tapped, so it may not make sound.
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
      return this === spare ? Promise.reject(new DOMException('no gesture', 'NotAllowedError')) : Promise.resolve();
    });
    await usePlayer.getState().next(true);
    expect(audio()).toBe(first);
    expect(first.src).toContain('/s/2');
    expect(usePlayer.getState()).toMatchObject({ error: null });
    expect(usePlayer.getState().current?.id).toBe(2);
    play.mockRestore();
    detach();
  });

  it('never leaves the player silent after a skip that failed', async () => {
    await usePlayer.getState().play({ queue: [one, two], index: 0, source: 'library' });
    mockApi({
      'POST /v1/tracks/2/stream': () => new Response(JSON.stringify({ error: { code: 'unavailable' } }), { status: 503 }),
      'POST /v1/telemetry/playback': {},
    });
    await usePlayer.getState().next();
    expect(usePlayer.getState().error?.kind).toBe('unavailable');
    expect(audio().volume).toBe(1);
  });
});

describe('unlocking audio on the first tap', () => {
  it('plays a moment of silence on every idle element, muted', async () => {
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:silence' });
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play');
    const elements = allAudio();
    expect(unlockAudio()).toBe(false);
    expect(play).toHaveBeenCalledTimes(2);
    await vi.waitFor(() => expect(elements.every((el) => el.muted === false)).toBe(true));
    expect(unlockAudio()).toBe(true);
  });
});

describe('lock screen and notification', () => {
  it('shows the song at once and wires explicit controls', async () => {
    await usePlayer.getState().play({ queue: [one, two], index: 0, source: 'library' });
    const metadata = navigator.mediaSession.metadata as unknown as { init: { title: string; artwork: { src: string }[] } };
    expect(metadata.init.title).toBe('One');
    await vi.waitFor(() =>
      expect((navigator.mediaSession.metadata as unknown as { init: { artwork: { src: string }[] } }).init.artwork[0]?.src).toBe(
        'https://cdn.test/t/1?t=x&hi=1',
      ),
    );

    const toggle = vi.fn(async () => undefined);
    usePlayer.setState({ toggle, isPlaying: true });
    handlers.play?.({});
    expect(toggle).not.toHaveBeenCalled(); // already playing: "play" must not pause
    handlers.pause?.({});
    expect(toggle).toHaveBeenCalledTimes(1);
    expect(handlers.nexttrack).toBeTypeOf('function');
    expect(handlers.seekforward).toBeTypeOf('function');
  });
});

describe('artwork', () => {
  it('asks the edge for the full-size cover', () => {
    expect(hiRes('https://e/t/1?t=abc')).toBe('https://e/t/1?t=abc&hi=1');
    expect(hiRes('https://e/t/1?t=abc&hi=1')).toBe('https://e/t/1?t=abc&hi=1');
    expect(hiRes(undefined)).toBeUndefined();
  });
});
