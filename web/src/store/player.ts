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
import { fetchForTracks, loadProgress, saveProgress } from '@/api/listening';
import { haptic } from '@/lib/telegram';
import { applyEffects, resumeEffects, useAudioSettings } from '@/store/audio';
import {
  allAudio,
  audio,
  cancelFade,
  fadeElement,
  preloadNext,
  spareTrackId,
  takePreloaded,
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
  swapBack,
  ticketFor,
  toPlaybackError,
  unlockAudio,
  type PlaybackError,
} from '@/player/engine';
import { hiRes } from '@/player/thumbs';

export type RepeatMode = 'off' | 'all' | 'one';
export type PlaySource = 'library' | 'search' | 'playlist' | 'channel' | 'discover' | 'mix' | 'radio' | 'trending' | 'shared' | 'offline';

/**
 * While in a Jam, the transport belongs to everybody: the controls ask the Jam to
 * move, and the Jam tells every listener's player where to be (see store/jam). The
 * player itself only follows.
 */
export interface Remote {
  toggle: () => void;
  next: () => void;
  previous: () => void;
  seek: (seconds: number) => void;
  /** The local copy of the current track finished. */
  ended: () => void;
  enqueue: (tracks: Track[], position: 'next' | 'end') => void;
  play: (request: PlayRequest) => void;
}

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
  /** Smart Shuffle: recommendations mixed into what is playing (shuffle stays on). */
  smart: boolean;
  /** The tracks Smart Shuffle added, so they can be marked and taken out again. */
  smartIds: number[];
  repeat: RepeatMode;
  speed: number;
  volume: number;
  /** Keep going with a station when the source runs out. */
  autoplay: boolean;
  sleepAt: number | null;
  sleepEndOfTrack: boolean;
  error: PlaybackError | null;
  /** Set while in a Jam: transport actions go there instead of to the queue. */
  remote: Remote | null;
  /** Set by the app; used to report finished plays (phase 5). */
  onPlayed: ((track: Track, playedSeconds: number, completed: boolean, source: PlaySource, sourceId: number | null) => void) | null;

  play: (request: PlayRequest) => Promise<void>;
  /**
   * Put the player on ``queue[index]`` at wherever ``positionAt`` says the music is
   * *when the audio is ready* (loading takes time, and the room does not wait).
   */
  follow: (queue: Track[], index: number, positionAt: () => number, playing: boolean) => Promise<void>;
  toggle: () => Promise<void>;
  next: (auto?: boolean) => Promise<void>;
  previous: () => Promise<void>;
  seek: (seconds: number) => void;
  setSpeed: (speed: number) => void;
  setVolume: (volume: number) => void;
  setAutoplay: (on: boolean) => void;
  setShuffle: (on: boolean) => void;
  setSmartShuffle: (on: boolean) => Promise<void>;
  /** Off → shuffle → smart shuffle → off, like Spotify's button. */
  cycleShuffle: () => void;
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

/** A track this long is listened to in sittings: its place is remembered. */
export const LONG_FORM_S = 10 * 60;
const PROGRESS_EVERY_MS = 15_000;
let lastProgressSave = 0;

function rememberPlace(track: Track | null, seconds: number, force = false): void {
  if (!track || track.duration < LONG_FORM_S || seconds < 5) return;
  const now = Date.now();
  if (!force && now - lastProgressSave < PROGRESS_EVERY_MS) return;
  lastProgressSave = now;
  void saveProgress(track.id, seconds).catch(() => undefined);
}

/** Set while the next track is fading in over the end of this one. */
let crossfading = false;
/** How long the next load should overlap the outgoing track (0 = no crossfade). */
let pendingCrossfadeMs = 0;

let playedSeconds = 0;
let lastTick = 0;
/** Set when the user asks for a track; cleared when sound actually starts. */
let startedAt = 0;
let stalled = false;

/**
 * Recovering from a failure mid-song. Phones drop what they buffered and ask for the
 * rest again as they go; if that request fails (a link that ran out, a network that
 * blinked) the element stops with an error and, until now, the music simply ended.
 * Now it gets a fresh link and carries on from the same second, a few times per song.
 */
const MAX_RECOVERIES = 3;
let recoveries = { trackId: 0, count: 0 };
/** A stall that does not clear by itself is treated like a failure. */
const STALL_RECOVER_MS = 12_000;
let stallTimer: ReturnType<typeof setTimeout> | null = null;

function clearStallTimer(): void {
  if (stallTimer) clearTimeout(stallTimer);
  stallTimer = null;
}

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
  smart: false,
  smartIds: [],
  repeat: 'off',
  speed: 1,
  volume: 1,
  autoplay: true,
  sleepAt: null,
  sleepEndOfTrack: false,
  error: null,
  remote: null,
  onPlayed: null,

  async play(request) {
    const remote = get().remote;
    if (remote) {
      remote.play(request);
      return;
    }
    const { queue, index, source, sourceId = null } = request;
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
    // Smart Shuffle is a mode, not a one-off: a new source gets its own suggestions.
    if (get().smart) void get().setSmartShuffle(true);
  },

  async follow(queue, index, positionAt, playing) {
    const track = queue[index];
    if (!track) return;
    get().reportPlayed();
    startedAt = Date.now();
    set({
      queue,
      manual: [],
      unshuffled: null,
      shuffle: false,
      index,
      playingManual: false,
      source: 'shared',
      sourceId: null,
      current: track,
      isLoading: true,
      error: null,
      position: positionAt(),
      duration: track.duration,
    });
    await load(track, set, get, 0, { positionAt, autoplay: playing });
  },

  async toggle() {
    const el = audio();
    const { current, isPlaying, remote } = get();
    if (!current) return;
    haptic('light');
    resumeEffects();
    if (remote) {
      remote.toggle();
      return;
    }
    if (isPlaying) {
      el.pause();
      return;
    }
    // A skip that failed can leave the element faded to silence: never resume mute.
    cancelFade();
    el.volume = get().volume;
    try {
      await el.play();
    } catch {
      // Autoplay refused (no gesture) or the ticket expired while paused.
      dropTicket(current.id);
      await load(current, set, get);
    }
  },

  async next(auto = false) {
    const { queue, manual, index, repeat, autoplay, current, remote } = get();
    if (remote) {
      if (auto) remote.ended();
      else remote.next();
      return;
    }
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
    const { index, queue, position, playingManual, remote } = get();
    if (remote) {
      remote.previous();
      return;
    }
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
    const remote = get().remote;
    if (remote) {
      remote.seek(seconds);
      return;
    }
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
      // The original order has no suggestions in it: Smart Shuffle ends with shuffle.
      set({ shuffle: false, smart: false, smartIds: [], queue: unshuffled, unshuffled: null, index: restored });
    } else {
      set(on ? { shuffle: true } : { shuffle: false, smart: false, smartIds: [] });
    }
    haptic('select');
  },

  async setSmartShuffle(on) {
    const strip = () => {
      const { queue, index, smartIds, current } = get();
      if (smartIds.length === 0) return;
      const added = new Set(smartIds);
      const kept = queue.filter((track, i) => i <= index || !added.has(track.id));
      set({
        queue: kept,
        index: Math.max(0, kept.findIndex((track) => track.id === current?.id)),
        smartIds: [],
      });
    };
    if (!on) {
      strip();
      set({ smart: false });
      haptic('select');
      return;
    }
    strip();
    if (!get().shuffle) get().setShuffle(true);
    set({ smart: true });
    const { queue } = get();
    if (queue.length === 0) return;
    const inQueue = new Set(queue.map((track) => track.id));
    let recs: Track[] = [];
    try {
      recs = (await fetchForTracks(queue.map((track) => track.id), 20)).filter((track) => !inQueue.has(track.id));
    } catch {
      return; // no suggestions is still a shuffle
    }
    if (!get().smart || recs.length === 0) return;
    // One suggestion after every three of theirs, in what is still to come.
    const { queue: now, index } = get();
    const head = now.slice(0, index + 1);
    const rest = now.slice(index + 1);
    const mixed: Track[] = [];
    let r = 0;
    rest.forEach((track, i) => {
      mixed.push(track);
      if ((i + 1) % 3 === 0 && r < recs.length) mixed.push(recs[r++] as Track);
    });
    while (r < recs.length && mixed.length < rest.length + Math.ceil(rest.length / 3) + 1) mixed.push(recs[r++] as Track);
    const used = recs.slice(0, r);
    set({ queue: [...head, ...mixed], smartIds: used.map((track) => track.id) });
  },

  cycleShuffle() {
    const { shuffle, smart } = get();
    if (!shuffle) get().setShuffle(true);
    else if (!smart) void get().setSmartShuffle(true);
    else get().setShuffle(false);
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
    const remote = get().remote;
    if (remote) {
      remote.enqueue(tracks, position);
      return;
    }
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
    applyEffects();
    // Unlock both audio elements on the first touch (see engine.unlockAudio). Capture
    // phase, so it runs before the tap's own handler starts loading a track.
    const unlockEvents = ['touchend', 'pointerdown', 'click', 'keydown'] as const;
    const onGesture = () => {
      if (unlockAudio()) for (const name of unlockEvents) document.removeEventListener(name, onGesture, true);
    };
    for (const name of unlockEvents) document.addEventListener(name, onGesture, true);
    /** Gets a fresh link and picks the song up where it stopped. False when out of tries. */
    const recover = (el: HTMLAudioElement, reason: string): boolean => {
      const { current, remote } = get();
      if (!current || remote) return false;
      if (recoveries.trackId !== current.id) recoveries = { trackId: current.id, count: 0 };
      if (recoveries.count >= MAX_RECOVERIES) return false;
      recoveries.count += 1;
      const at = el.currentTime || get().position;
      const wasPlaying = get().isPlaying || get().isLoading;
      clearStallTimer();
      dropTicket(current.id);
      report('error', { reason, recovered: true, attempt: recoveries.count });
      set({ isLoading: true });
      void load(current, set, get, 0, { positionAt: () => at, autoplay: wasPlaying, keepElement: true });
      return true;
    };
    /**
     * Near the end of a track: buffer the next one in the spare element (gapless),
     * and with crossfade on, start it while this one fades out.
     */
    const transitionAhead = (el: HTMLAudioElement) => {
      const { remote, repeat, isPlaying, current } = get();
      if (remote || !isPlaying || !current || repeat === 'one') return;
      if (!Number.isFinite(el.duration) || el.duration <= 0) return;
      const { crossfade, gapless } = useAudioSettings.getState();
      if (!gapless && crossfade <= 0) return;
      const [upcoming] = get().upcoming();
      if (!upcoming) return;
      const left = el.duration - el.currentTime;
      if (left <= Math.max(crossfade + 10, 20) && spareTrackId() !== upcoming.id) void preloadNext(upcoming);
      if (crossfade > 0 && !crossfading && el.duration > crossfade * 3 && left <= crossfade) {
        crossfading = true;
        pendingCrossfadeMs = crossfade * 1000;
        get().reportPlayed(true);
        void get().next(true);
      }
    };
    // Both elements are watched; only the one playing is listened to.
    const cleanups = allAudio().map((el) => {
      const onTime = () => {
        // The spare element (preloading, or fading out) speaks for nobody.
        if (el !== audio()) return;
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
        transitionAhead(el);
        if (!get().remote) rememberPlace(get().current, el.currentTime);
      };
      const onPlay = () => {
        if (el !== audio()) return;
        lastTick = Date.now();
        set({ isPlaying: true, isLoading: false });
        setPlaybackState('playing');
      };
      const onPlaying = () => {
        if (el !== audio()) return;
        // The first sound: this is the number the acceptance criterion is about.
        if (startedAt) {
          report('start', { ms: Date.now() - startedAt });
          startedAt = 0;
        }
        if (stalled) stalled = false;
        clearStallTimer();
        // Warm whatever actually plays next — manual queue included.
        const [upcoming] = get().upcoming();
        if (upcoming) void prefetch(upcoming.id);
      };
      const onWaiting = () => {
        if (el !== audio()) return;
        // Only a stall *during* playback is an underrun; the initial load is not.
        if (!get().isPlaying || stalled) return;
        stalled = true;
        report('underrun');
        // Still stuck after a while: the connection under the element is gone.
        clearStallTimer();
        stallTimer = setTimeout(() => {
          stallTimer = null;
          if (stalled && el === audio() && el.readyState < 3) recover(el, 'stall');
        }, STALL_RECOVER_MS);
      };
      const onPause = () => {
        if (el !== audio()) return;
        lastTick = 0;
        set({ isPlaying: false });
        setPlaybackState('paused');
        if (!get().remote) rememberPlace(get().current, el.currentTime, true);
      };
      const onEnded = () => {
        if (el !== audio()) return;
        get().reportPlayed(true);
        const { sleepEndOfTrack } = get();
        if (sleepEndOfTrack) {
          set({ sleepEndOfTrack: false, sleepAt: null, isPlaying: false });
          return;
        }
        void get().next(true);
      };
      const onProgress = () => {
        if (el !== audio()) return;
        const ranges = el.buffered;
        set({ buffered: ranges.length ? ranges.end(ranges.length - 1) : 0 });
      };
      const onError = () => {
        if (el !== audio()) return;
        const track = get().current;
        if (!track || !el.getAttribute('src')) return;
        if (recover(el, 'media')) return;
        dropTicket(track.id);
        report('error', { reason: 'decode' });
        el.volume = get().volume;
        set({ isLoading: false, isPlaying: false, error: toPlaybackError(new Error('media')) });
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
    });
    return () => {
      for (const cleanup of cleanups) cleanup();
      for (const name of unlockEvents) document.removeEventListener(name, onGesture, true);
      clearStallTimer();
    };
  },
}));

interface LoadOptions {
  /** Where to start, read at the last moment (a Jam keeps moving while we load). */
  positionAt?: () => number;
  autoplay?: boolean;
  /** Recovering the same song: reuse the element, do not swap to the spare. */
  keepElement?: boolean;
}

/** The lock-screen / notification controls, wired to this store. */
function mediaHandlers(get: () => PlayerState): Partial<Record<MediaSessionAction, MediaSessionActionHandler | null>> {
  return {
    // Explicit play and pause: a toggle would do the opposite whenever the store and
    // the element disagree for a moment (which is exactly when people press them).
    play: () => {
      if (!get().isPlaying) void get().toggle();
    },
    pause: () => {
      if (get().isPlaying) void get().toggle();
    },
    stop: () => {
      if (get().isPlaying) void get().toggle();
    },
    nexttrack: () => void get().next(),
    previoustrack: () => void get().previous(),
    seekto: (details) => {
      if (details.seekTime !== undefined) get().seek(details.seekTime);
    },
    seekbackward: (details) => get().seek(Math.max(0, get().position - (details.seekOffset ?? 10))),
    seekforward: (details) => get().seek(get().position + (details.seekOffset ?? 10)),
  };
}

async function load(
  track: Track,
  set: (partial: Partial<PlayerState>) => void,
  get: () => PlayerState,
  attempt = 0,
  options: LoadOptions = {},
): Promise<void> {
  const { positionAt, autoplay = true, keepElement = false } = options;
  const overlapMs = pendingCrossfadeMs;
  pendingCrossfadeMs = 0;
  crossfading = false;
  // The notification and lock screen show the new song straight away.
  if (attempt === 0 && !keepElement) setMediaSession(track, null, mediaHandlers(get));
  // The next track may already be buffered in the spare element: switch to it.
  const outgoing = keepElement ? null : takePreloaded(track.id);
  let el = audio();
  if (outgoing && outgoing !== el) {
    if (overlapMs > 0) void fadeElement(outgoing, 0, overlapMs).then(() => outgoing.pause());
    else outgoing.pause();
  }
  resumeEffects();
  try {
    // The link and (for a long track) the saved place, asked for at the same time.
    const wantsProgress = !positionAt && !get().remote && track.duration >= LONG_FORM_S;
    const [src, saved] = await Promise.all([
      offlineUrl(track.id).then(async (offline) => offline ?? (await ticketFor(track.id)).url),
      wantsProgress ? loadProgress(track.id).catch(() => null) : Promise.resolve(null),
    ]);
    // A long track picks up where this listener left it, on any device.
    const resumeAt = saved && !saved.finished && saved.position_s > 30 ? saved.position_s : 0;
    if (get().current?.id !== track.id) return; // moved on while the ticket loaded
    if (!outgoing || el.src !== src) el.src = src;
    if (resumeAt) el.currentTime = resumeAt;
    el.playbackRate = get().speed;
    if (positionAt) el.currentTime = Math.max(0, positionAt());
    // Start silent and ramp up: without this every track begins with a click.
    cancelFade();
    if (!autoplay) {
      el.volume = get().volume;
      set({ isLoading: false, isPlaying: false, error: null });
      return;
    }
    el.volume = 0;
    try {
      await el.play();
    } catch (error) {
      // The spare had never been allowed to make sound. The element that was playing
      // a moment ago was: put the new song there instead of stopping the music.
      if (!(error instanceof DOMException && error.name === 'NotAllowedError' && outgoing)) throw error;
      const position = el.currentTime;
      el.pause();
      el = swapBack();
      cancelFade();
      el.src = src;
      el.currentTime = position;
      el.playbackRate = get().speed;
      el.volume = 0;
      await el.play();
    }
    // The first bytes took a while; catch up with the room.
    if (positionAt && Math.abs(el.currentTime - positionAt()) > 1) el.currentTime = positionAt();
    void fadeTo(get().volume, overlapMs > 0 ? overlapMs : FADE_IN_MS);
    set({ isLoading: false, error: null });
  } catch (error) {
    // Whatever failed, the element must not be left silent for the next attempt.
    cancelFade();
    el.volume = get().volume;
    if (error instanceof DOMException && error.name === 'NotAllowedError') {
      // The browser wants a tap before it makes sound (a Jam starting on its own).
      // The track is loaded and in place; the play button does the rest.
      set({ isLoading: false, isPlaying: false, error: null });
      return;
    }
    const playbackError = toPlaybackError(error);
    // A flaky connection deserves another try; a plan limit or a missing source does
    // not — retrying those would only spin and say nothing useful to the user.
    const delay = playbackError.kind === 'network' ? retryDelay(attempt) : null;
    if (delay !== null && get().current?.id === track.id) {
      dropTicket(track.id);
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (get().current?.id !== track.id) return; // the user moved on
      return load(track, set, get, attempt + 1, { ...options, keepElement: true });
    }
    report('error', { reason: playbackError.kind });
    set({ isLoading: false, isPlaying: false, error: playbackError });
    return;
  }
  // The big artwork for the lock screen: the file's own cover where it has one.
  try {
    const ticket = await ticketFor(track.id);
    if (get().current?.id === track.id) setMediaSession(track, hiRes(ticket.thumb_url) ?? null, mediaHandlers(get));
  } catch {
    /* no artwork is still a notification */
  }
}
