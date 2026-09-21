/**
 * Playback state and queue.
 *
 * The store owns the queue and talks to the single <audio> element in player/engine.
 * Components subscribe to slices, so a position tick only re-renders the progress bar.
 *
 * Two queues, like every music app people already know (ADR-003 phase 11):
 *
 * - `queue` is the *source*: the playlist, album or search result being played, in
 *   the order it is being played (shuffled or not).
 * - `manual` is what the user explicitly asked for next. It always wins, and once it
 *   is empty the source continues from exactly where it was.
 *
 * `current` is therefore not always `queue[index]` — while a manual track plays,
 * `index` still marks the place the source will resume from.
 */
import { create } from 'zustand';

import type { Track } from '@/api/client';
import { fetchRadio } from '@/api/discover';
import { haptic } from '@/lib/telegram';
import {
  audio,
  cancelFade,
  dropTicket,
  fadeTo,
  FADE_IN_MS,
  FADE_OUT_MS,
  offlineUrl,
  prefetch,
  report,
  retryDelay,
  setMediaSession,
  setPlaybackState,
  setPositionState,
  ticketFor,
  toPlaybackError,
  type PlaybackError,
} from '@/player/engine';

export type RepeatMode = 'off' | 'all' | 'one';
export type PlaySource = 'library' | 'search' | 'playlist' | 'channel' | 'discover' | 'mix' | 'radio' | 'trending' | 'shared';

export interface PlayRequest {
  queue: Track[];
  index: number;
  source: PlaySource;
  sourceId?: number | null;
}

interface PlayerState {
  queue: Track[];
  /** What the user queued by hand; played before the source continues. */
  manual: Track[];
  /** The source order before shuffle, so turning shuffle off restores it. */
  unshuffled: Track[] | null;
  /** True while a manually queued track is playing (``index`` is where we resume). */
  playingManual: boolean;
  index: number;
  source: PlaySource;
  sourceId: number | null;
  current: Track | null;
  isPlaying: boolean;
  isLoading: boolean;
  position: number;
  duration: number;
  buffered: number;
  shuffle: boolean;
  repeat: RepeatMode;
  speed: number;
  volume: number;
  /** Keep going with a station when the source runs out. */
  autoplay: boolean;
  sleepAt: number | null;
  sleepEndOfTrack: boolean;
  error: PlaybackError | null;
  /** Set by the app; used to report finished plays (phase 5). */
  onPlayed: ((track: Track, playedSeconds: number, completed: boolean, source: PlaySource, sourceId: number | null) => void) | null;

  play: (request: PlayRequest) => Promise<void>;
  toggle: () => Promise<void>;
  next: (auto?: boolean) => Promise<void>;
  previous: () => Promise<void>;
  seek: (seconds: number) => void;
  setSpeed: (speed: number) => void;
  setVolume: (volume: number) => void;
  setAutoplay: (on: boolean) => void;
  setShuffle: (on: boolean) => void;
  cycleRepeat: () => void;
  setSleep: (minutes: number | null, endOfTrack?: boolean) => void;
  enqueue: (tracks: Track[], position?: 'next' | 'end') => void;
  removeFromQueue: (index: number) => void;
  removeManual: (index: number) => void;
  moveInQueue: (from: number, to: number) => void;
  moveManual: (from: number, to: number) => void;
  /** What plays next, manual queue first — this is what the queue sheet shows. */
  upcoming: () => Track[];
  clearError: () => void;
  /** Reports the play that just finished (>= 5s listened) to the history hook. */
  reportPlayed: (completed?: boolean) => void;
  attach: () => () => void;
}

function shuffled(tracks: Track[], keepIndex: number): { queue: Track[]; index: number } {
  const current = tracks[keepIndex];
  const rest = tracks.filter((_, i) => i !== keepIndex);
  for (let i = rest.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [rest[i], rest[j]] = [rest[j] as Track, rest[i] as Track];
  }
  return current ? { queue: [current, ...rest], index: 0 } : { queue: rest, index: 0 };
}

/** A station to keep playing with, or nothing — never an error the user sees. */
async function radioFor(trackId: number): Promise<Track[]> {
  try {
    return await fetchRadio(trackId);
  } catch {
    return [];
  }
}

let playedSeconds = 0;
let lastTick = 0;
/** Set when the user asks for a track; cleared when sound actually starts. */
let startedAt = 0;
let stalled = false;

export const usePlayer = create<PlayerState>((set, get) => ({
  queue: [],
  manual: [],
  unshuffled: null,
  playingManual: false,
  index: 0,
  source: 'library',
  sourceId: null,
  current: null,
  isPlaying: false,
  isLoading: false,
  position: 0,
  duration: 0,
  buffered: 0,
  shuffle: false,
  repeat: 'off',
  speed: 1,
  volume: 1,
  autoplay: true,
  sleepAt: null,
  sleepEndOfTrack: false,
  error: null,
  onPlayed: null,

  async play({ queue, index, source, sourceId = null }) {
    startedAt = Date.now();
    const state = get();
    const prepared = state.shuffle ? shuffled(queue, index) : { queue, index };
    const track = prepared.queue[prepared.index];
    if (!track) return;
    state.reportPlayed();
    set({
      queue: prepared.queue,
      // The original order is what "shuffle off" goes back to.
      unshuffled: state.shuffle ? queue : null,
      index: prepared.index,
      playingManual: false,
      source,
      sourceId,
      current: track,
      isLoading: true,
      error: null,
      position: 0,
      duration: track.duration,
    });
    await load(track, set, get);
  },

  async toggle() {
    const el = audio();
    const { current, isPlaying } = get();
    if (!current) return;
    haptic('light');
    if (isPlaying) {
      el.pause();
      return;
    }
    try {
      await el.play();
    } catch {
      // Autoplay refused (no gesture) or the ticket expired while paused.
      dropTicket(current.id);
      await load(current, set, get);
    }
  },

  async next(auto = false) {
    const { queue, manual, index, repeat, autoplay, current } = get();
    if (repeat === 'one' && auto) {
      const el = audio();
      el.currentTime = 0;
      await el.play().catch(() => undefined);
      return;
    }
    if (!auto) await fadeTo(0, FADE_OUT_MS);
    startedAt = Date.now();

    // 1. Whatever the user asked for by hand.
    if (manual.length > 0) {
      const [track, ...rest] = manual;
      if (!track) return;
      get().reportPlayed();
      set({
        manual: rest,
        playingManual: true,
        current: track,
        isLoading: true,
        position: 0,
        duration: track.duration,
        error: null,
      });
      await load(track, set, get);
      return;
    }

    // 2. The source continues. `index` is the track that was interrupted and has
    //    already been heard, so continuing means the one after it — exactly the
    //    normal advance. (Going *back* from a manual track is the case that needs
    //    `index` itself; see `previous`.)
    const last = index >= queue.length - 1;
    if (last && repeat !== 'all') {
      // 3. Nothing left: keep the music going with a station built from this track.
      if (autoplay && current) {
        const station = await radioFor(current.id);
        if (station.length > 0) {
          get().reportPlayed();
          const track = station[0] as Track;
          set({
            queue: [...queue, ...station],
            unshuffled: null,
            index: queue.length,
            playingManual: false,
            source: 'radio',
            sourceId: current.id,
            current: track,
            isLoading: true,
            position: 0,
            duration: track.duration,
            error: null,
          });
          await load(track, set, get);
          return;
        }
      }
      if (auto) {
        audio().pause();
        set({ isPlaying: false, position: 0, playingManual: false });
      }
      await fadeTo(get().volume, 0);
      return;
    }
    const nextIndex = last ? 0 : index + 1;
    const track = queue[nextIndex];
    if (!track) return;
    get().reportPlayed();
    set({
      index: nextIndex,
      playingManual: false,
      current: track,
      isLoading: true,
      position: 0,
      duration: track.duration,
      error: null,
    });
    await load(track, set, get);
  },

  async previous() {
    const { index, queue, position, playingManual } = get();
    if (position > 3) {
      audio().currentTime = 0;
      return;
    }
    await fadeTo(0, FADE_OUT_MS);
    startedAt = Date.now();
    if (playingManual) {
      // Back out of the manual queue: the source is still sitting on `index`.
      const track = queue[index];
      if (!track) return;
      get().reportPlayed();
      set({
        playingManual: false,
        current: track,
        isLoading: true,
        position: 0,
        duration: track.duration,
        error: null,
      });
      await load(track, set, get);
      return;
    }
    const prevIndex = index > 0 ? index - 1 : queue.length - 1;
    const track = queue[prevIndex];
    if (!track) return;
    get().reportPlayed();
    set({
      index: prevIndex,
      playingManual: false,
      current: track,
      isLoading: true,
      position: 0,
      duration: track.duration,
      error: null,
    });
    await load(track, set, get);
  },

  seek(seconds) {
    const el = audio();
    if (Number.isFinite(el.duration)) el.currentTime = Math.max(0, Math.min(seconds, el.duration));
    else el.currentTime = Math.max(0, seconds);
    set({ position: el.currentTime });
  },

  setSpeed(speed) {
    audio().playbackRate = speed;
    set({ speed });
  },

  setShuffle(on) {
    const { queue, index, unshuffled, current } = get();
    if (on && queue.length > 1) {
      const prepared = shuffled(queue, index);
      // Remember what the order was, or turning shuffle off would lose it forever.
      set({ shuffle: true, unshuffled: queue, queue: prepared.queue, index: prepared.index });
    } else if (!on && unshuffled) {
      const restored = Math.max(
        0,
        unshuffled.findIndex((track) => track.id === current?.id),
      );
      set({ shuffle: false, queue: unshuffled, unshuffled: null, index: restored });
    } else {
      set({ shuffle: on });
    }
    haptic('select');
  },

  setVolume(volume) {
    const clamped = Math.max(0, Math.min(1, volume));
    cancelFade();
    audio().volume = clamped;
    set({ volume: clamped });
  },

  setAutoplay(on) {
    set({ autoplay: on });
  },

  cycleRepeat() {
    const order: RepeatMode[] = ['off', 'all', 'one'];
    const current = order.indexOf(get().repeat);
    set({ repeat: order[(current + 1) % order.length] as RepeatMode });
    haptic('select');
  },

  setSleep(minutes, endOfTrack = false) {
    set({
      sleepAt: minutes === null ? null : Date.now() + minutes * 60_000,
      sleepEndOfTrack: endOfTrack,
    });
  },

  enqueue(tracks, position = 'end') {
    // Always the manual queue: "play next" must not rewrite the album being played.
    const { manual } = get();
    set({ manual: position === 'next' ? [...tracks, ...manual] : [...manual, ...tracks] });
  },

  removeManual(target) {
    set({ manual: get().manual.filter((_, i) => i !== target) });
  },

  moveManual(from, to) {
    const moved = [...get().manual];
    const [item] = moved.splice(from, 1);
    if (!item) return;
    moved.splice(to, 0, item);
    set({ manual: moved });
  },

  upcoming() {
    const { queue, manual, index } = get();
    return [...manual, ...queue.slice(index + 1)];
  },

  removeFromQueue(target) {
    const { queue, index } = get();
    if (target === index) return;
    set({
      queue: queue.filter((_, i) => i !== target),
      index: target < index ? index - 1 : index,
    });
  },

  moveInQueue(from, to) {
    const { queue, index } = get();
    const moved = [...queue];
    const [item] = moved.splice(from, 1);
    if (!item) return;
    moved.splice(to, 0, item);
    const current = get().current;
    set({ queue: moved, index: current ? moved.findIndex((t) => t.id === current.id) : index });
  },

  clearError() {
    set({ error: null });
  },

  reportPlayed(completed = false) {
    const { current, onPlayed, source, sourceId } = get();
    if (current && onPlayed && playedSeconds >= 5) {
      const finished = completed || playedSeconds >= Math.min(30, current.duration * 0.5);
      onPlayed(current, Math.round(playedSeconds), finished, source, sourceId);
    }
    playedSeconds = 0;
  },

  /** Wires the <audio> element to the store; returns an unsubscribe function. */
  attach() {
    const el = audio();
    const onTime = () => {
      const now = Date.now();
      if (lastTick && el.paused === false) playedSeconds += Math.min((now - lastTick) / 1000, 2);
      lastTick = now;
      set({ position: el.currentTime, duration: Number.isFinite(el.duration) ? el.duration : get().duration });
      setPositionState(el.currentTime, el.duration, el.playbackRate);
      const { sleepAt, sleepEndOfTrack } = get();
      if (sleepAt && now >= sleepAt && !sleepEndOfTrack) {
        el.pause();
        set({ sleepAt: null });
      }
    };
    const onPlay = () => {
      lastTick = Date.now();
      set({ isPlaying: true, isLoading: false });
      setPlaybackState('playing');
    };
    const onPlaying = () => {
      // The first sound: this is the number the acceptance criterion is about.
      if (startedAt) {
        report('start', { ms: Date.now() - startedAt });
        startedAt = 0;
      }
      if (stalled) stalled = false;
      // Warm whatever actually plays next — manual queue included.
      const [upcoming] = get().upcoming();
      if (upcoming) void prefetch(upcoming.id);
    };
    const onWaiting = () => {
      // Only a stall *during* playback is an underrun; the initial load is not.
      if (!get().isPlaying || stalled) return;
      stalled = true;
      report('underrun');
    };
    const onPause = () => {
      lastTick = 0;
      set({ isPlaying: false });
      setPlaybackState('paused');
    };
    const onEnded = () => {
      get().reportPlayed(true);
      const { sleepEndOfTrack } = get();
      if (sleepEndOfTrack) {
        set({ sleepEndOfTrack: false, sleepAt: null, isPlaying: false });
        return;
      }
      void get().next(true);
    };
    const onProgress = () => {
      const ranges = el.buffered;
      set({ buffered: ranges.length ? ranges.end(ranges.length - 1) : 0 });
    };
    const onError = () => {
      const track = get().current;
      if (track) dropTicket(track.id);
      report('error', { reason: 'decode' });
      set({ isLoading: false, isPlaying: false });
    };
    el.addEventListener('timeupdate', onTime);
    el.addEventListener('play', onPlay);
    el.addEventListener('playing', onPlaying);
    el.addEventListener('waiting', onWaiting);
    el.addEventListener('pause', onPause);
    el.addEventListener('ended', onEnded);
    el.addEventListener('progress', onProgress);
    el.addEventListener('error', onError);
    return () => {
      el.removeEventListener('timeupdate', onTime);
      el.removeEventListener('play', onPlay);
      el.removeEventListener('playing', onPlaying);
      el.removeEventListener('waiting', onWaiting);
      el.removeEventListener('pause', onPause);
      el.removeEventListener('ended', onEnded);
      el.removeEventListener('progress', onProgress);
      el.removeEventListener('error', onError);
    };
  },
}));

async function load(
  track: Track,
  set: (partial: Partial<PlayerState>) => void,
  get: () => PlayerState,
  attempt = 0,
): Promise<void> {
  const el = audio();
  try {
    const offline = await offlineUrl(track.id);
    const src = offline ?? (await ticketFor(track.id)).url;
    el.src = src;
    el.playbackRate = get().speed;
    // Start silent and ramp up: without this every track begins with a click.
    cancelFade();
    el.volume = 0;
    await el.play();
    void fadeTo(get().volume, FADE_IN_MS);
    set({ isLoading: false, error: null });
  } catch (error) {
    const playbackError = toPlaybackError(error);
    // A flaky connection deserves another try; a plan limit or a missing source does
    // not — retrying those would only spin and say nothing useful to the user.
    const delay = playbackError.kind === 'network' ? retryDelay(attempt) : null;
    if (delay !== null && get().current?.id === track.id) {
      dropTicket(track.id);
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (get().current?.id !== track.id) return; // the user moved on
      return load(track, set, get, attempt + 1);
    }
    report('error', { reason: playbackError.kind });
    set({ isLoading: false, isPlaying: false, error: playbackError });
    return;
  }
  const artwork = track.has_thumb ? (await ticketFor(track.id)).thumb_url : null;
  setMediaSession(track, artwork ?? null, {
    play: () => void get().toggle(),
    pause: () => void get().toggle(),
    nexttrack: () => void get().next(),
    previoustrack: () => void get().previous(),
    seekto: (details?: MediaSessionActionDetails) => {
      if (details?.seekTime !== undefined) get().seek(details.seekTime);
    },
  });
}
