/**
 * Jam: listening together.
 *
 * The server holds the queue and one playhead, stored as "where the music was, and
 * when" (see backend services/jams). Every listener — the host included — is a
 * follower: this store polls that state, and puts the local player where the room
 * is. The transport buttons stop driving the local queue and ask the room instead
 * (``usePlayer.remote``), so a tap on "next" moves everybody.
 *
 * Latency is taken out of the position rather than ignored: a response says where
 * the music was when it was built, and we add the time since we received it.
 */
import { create } from 'zustand';

import { ApiError, del, get as apiGet, patch, post, type Track } from '@/api/client';
import type { components } from '@/api/schema';
import { translate, type Key } from '@/i18n';
import { haptic } from '@/lib/telegram';
import { audio } from '@/player/engine';
import { usePlayer, type Remote } from '@/store/player';
import { useUi } from '@/store/ui';

export type JamState = components['schemas']['JamOut'];
export type JamItem = components['schemas']['JamItemOut'];
export type JamMember = components['schemas']['JamMemberOut'];

const POLL_MS = 2500;
const HIDDEN_POLL_MS = 8000;
/** How far a listener may drift before being pulled back in step. */
const DRIFT_S = 2;

type Action = 'play' | 'pause' | 'seek' | 'next' | 'previous' | 'jump';

interface JamStore {
  jam: JamState | null;
  items: JamItem[];
  /** performance.now() when ``jam`` arrived: the anchor for its ``position_s``. */
  receivedAt: number;
  joining: boolean;

  /** Where the room's music is right now. */
  positionNow: () => number;
  start: () => Promise<JamState>;
  join: (code: string) => Promise<JamState>;
  resume: () => Promise<void>;
  /** Read the room now (after coming back to the app, say). */
  refresh: () => Promise<void>;
  leave: () => Promise<void>;
  end: () => Promise<void>;
  control: (action: Action, extra?: { position_s?: number; index?: number; expected_index?: number }) => Promise<void>;
  add: (tracks: Track[], position?: 'next' | 'end' | 'now') => Promise<void>;
  remove: (index: number) => Promise<void>;
  setGuestsCanControl: (on: boolean) => Promise<void>;
  /** Drop the jam locally (it ended, or we left). */
  reset: () => void;
}

/** The toast already said what went wrong. */
const quiet = (promise: Promise<void>): Promise<void> => promise.catch(() => undefined);

let timer: ReturnType<typeof setTimeout> | null = null;
let polling = false;

function stopPolling(): void {
  if (timer) clearTimeout(timer);
  timer = null;
}

function toast(key: Key, kind: 'info' | 'error' | 'success' = 'info'): void {
  const ui = useUi.getState();
  ui.toast(translate(ui.lang, key), kind);
}

export const useJam = create<JamStore>((set, get) => {
  /** Take a server state in, and move the local player to match it. */
  const apply = (jam: JamState): void => {
    const previous = get().jam;
    const items = jam.items ?? get().items;
    set({ jam, items, receivedAt: performance.now() });
    usePlayer.setState({ remote });
    follow(previous, jam, items);
  };

  const follow = (previous: JamState | null, jam: JamState, items: JamItem[]): void => {
    const player = usePlayer.getState();
    const target = items[jam.index]?.track;
    const queue = items.map((item) => item.track);
    if (!target) {
      if (player.isPlaying) audio().pause();
      return;
    }
    const expected = get().positionNow();
    const sameTrack =
      player.current?.id === target.id && player.index === jam.index && player.source === 'shared';
    if (!sameTrack) {
      void player.follow(queue, jam.index, () => get().positionNow(), jam.playing);
      return;
    }
    if (jam.items) usePlayer.setState({ queue });
    if (player.isLoading) return;
    const el = audio();
    if (jam.playing && el.paused) {
      // The local copy just ended and the room has not moved on yet: wait for it.
      if (target.duration > 0 && expected >= target.duration - 0.5) return;
      // Only a change in the room restarts the music: a listener who paused their
      // own copy stays paused until something happens.
      if (!previous || previous.rev !== jam.rev) {
        el.currentTime = expected;
        void el.play().catch(() => undefined);
      }
      return;
    }
    if (!jam.playing && !el.paused) el.pause();
    if (Math.abs(el.currentTime - expected) > DRIFT_S) el.currentTime = expected;
  };

  const schedule = (): void => {
    stopPolling();
    const delay = typeof document !== 'undefined' && document.visibilityState === 'hidden' ? HIDDEN_POLL_MS : POLL_MS;
    timer = setTimeout(() => void poll(), delay);
  };

  const poll = async (): Promise<void> => {
    const jam = get().jam;
    if (!jam || polling) return;
    polling = true;
    try {
      apply(await apiGet<JamState>(`/v1/jams/${jam.code}?qrev=${jam.qrev}`));
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        get().reset();
        toast('jam.ended');
        return;
      }
      // Anything else is a bad moment on the network; the next poll tries again.
    } finally {
      polling = false;
    }
    if (get().jam) schedule();
  };

  /** Commands answer with the new state, so the room moves without waiting a poll. */
  const send = async (request: () => Promise<JamState>): Promise<void> => {
    try {
      apply(await request());
      schedule();
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) toast('jam.hostOnly', 'error');
      else if (error instanceof ApiError && error.status === 404) {
        get().reset();
        toast('jam.ended');
      } else toast('app.error', 'error');
      throw error;
    }
  };

  const code = (): string => {
    const jam = get().jam;
    if (!jam) throw new Error('not in a jam');
    return jam.code;
  };

  const remote: Remote = {
    toggle() {
      const jam = get().jam;
      if (!jam) return;
      const el = audio();
      // Someone who joined while the room was playing only needs sound, not a command.
      if (jam.playing && el.paused) {
        el.currentTime = get().positionNow();
        void el.play().catch(() => undefined);
        return;
      }
      if (!jam.can_control) {
        if (!el.paused) el.pause();
        return;
      }
      if (jam.playing) el.pause();
      else void el.play().catch(() => undefined);
      void get().control(jam.playing ? 'pause' : 'play');
    },
    next() {
      void get().control('next');
    },
    previous() {
      void get().control('previous');
    },
    seek(seconds) {
      audio().currentTime = seconds;
      void get().control('seek', { position_s: seconds });
    },
    ended() {
      const jam = get().jam;
      if (jam) void get().control('next', { expected_index: jam.index });
    },
    enqueue(tracks, position) {
      void get().add(tracks, position);
    },
    play({ queue, index }) {
      const track = queue[index];
      if (!track) return;
      const jam = get().jam;
      void get().add([track], jam?.can_control ? 'now' : 'next');
    },
  };

  return {
    jam: null,
    items: [],
    receivedAt: 0,
    joining: false,

    positionNow() {
      const { jam, receivedAt } = get();
      if (!jam) return 0;
      if (!jam.playing) return jam.position_s;
      return jam.position_s + (performance.now() - receivedAt) / 1000;
    },

    async start() {
      const player = usePlayer.getState();
      const seed = player.current
        ? {
            track_ids: player.queue.map((track) => track.id),
            index: Math.max(0, player.queue.findIndex((track, i) => i >= player.index && track.id === player.current?.id)),
            position_s: player.position,
            playing: player.isPlaying,
          }
        : {};
      const jam = await post<JamState>('/v1/jams', seed);
      haptic('success');
      apply(jam);
      schedule();
      return jam;
    },

    async join(joinCode) {
      set({ joining: true });
      try {
        const jam = await post<JamState>(`/v1/jams/${joinCode}/join`, {});
        haptic('success');
        apply(jam);
        schedule();
        return jam;
      } finally {
        set({ joining: false });
      }
    },

    refresh() {
      stopPolling();
      return poll();
    },

    async resume() {
      try {
        const jam = await apiGet<JamState | null>('/v1/jams/current');
        if (!jam) return;
        apply(jam);
        schedule();
      } catch {
        /* no jam to go back to */
      }
    },

    async leave() {
      const jam = get().jam;
      get().reset();
      if (jam) await post(`/v1/jams/${jam.code}/leave`, {}).catch(() => undefined);
    },

    async end() {
      const jam = get().jam;
      get().reset();
      if (jam) await del(`/v1/jams/${jam.code}`).catch(() => undefined);
    },

    control(action, extra = {}) {
      return quiet(send(() => post<JamState>(`/v1/jams/${code()}/control?qrev=${get().jam?.qrev ?? 0}`, { action, ...extra })));
    },

    async add(tracks, position = 'end') {
      try {
        await send(() =>
          post<JamState>(`/v1/jams/${code()}/queue`, { track_ids: tracks.map((track) => track.id), position }),
        );
      } catch {
        return;
      }
      haptic('success');
      if (position !== 'now') toast('jam.added', 'success');
    },

    remove(index) {
      return quiet(send(() => del<JamState>(`/v1/jams/${code()}/queue/${index}`)));
    },

    setGuestsCanControl(on) {
      return quiet(
        send(() => patch<JamState>(`/v1/jams/${code()}?qrev=${get().jam?.qrev ?? 0}`, { guests_can_control: on })),
      );
    },

    reset() {
      stopPolling();
      set({ jam: null, items: [], receivedAt: 0 });
      usePlayer.setState({ remote: null });
    },
  };
});

if (typeof document !== 'undefined') {
  // Coming back to the app: catch up at once instead of after a slow hidden poll.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && useJam.getState().jam) void useJam.getState().refresh();
  });
}
