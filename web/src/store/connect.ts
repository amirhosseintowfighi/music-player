/**
 * Connect: this listener's other open devices, and moving the music between them.
 *
 * Every few seconds this copy of the app tells the server what it is playing and
 * hears back which other devices are live and whether one of them left a command for
 * us ("play this queue from 1:12", "pause"). A poll, not a socket: it is what a Mini
 * App can keep alive, and a few seconds is quick enough for a handoff.
 */
import { create } from 'zustand';

import type { Track } from '@/api/client';
import {
  forgetDevice,
  sendCommand,
  sendHeartbeat,
  type CommandBody,
  type ConnectCommand,
  type Device,
} from '@/api/listening';
import { telegramPlatform } from '@/lib/telegram';
import { usePlayer } from '@/store/player';

const KEY = 'tmusic.device';
const VISIBLE_MS = 5_000;
const HIDDEN_MS = 15_000;

function newId(): string {
  const bytes = new Uint8Array(12);
  globalThis.crypto?.getRandomValues?.(bytes);
  const random = Array.from(bytes, (b) => b.toString(36).padStart(2, '0')).join('');
  return random.length >= 12 ? random.slice(0, 20) : `d${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

export function deviceId(): string {
  try {
    const known = localStorage.getItem(KEY);
    if (known && /^[A-Za-z0-9_-]{6,64}$/.test(known)) return known;
    const made = newId();
    localStorage.setItem(KEY, made);
    return made;
  } catch {
    // No storage: one id for as long as this page lives.
    fallbackId ??= newId();
    return fallbackId;
  }
}
let fallbackId: string | null = null;

/** What this device is called on the others' lists. */
export function thisDevice(): { name: string; kind: Device['kind'] } {
  const platform = telegramPlatform();
  if (platform === 'ios') {
    const tablet = /iPad/.test(navigator.userAgent);
    return tablet ? { name: 'iPad', kind: 'tablet' } : { name: 'iPhone', kind: 'phone' };
  }
  if (platform === 'android' || platform === 'android_x') return { name: 'Android', kind: 'phone' };
  if (platform === 'tdesktop') return { name: 'Telegram Desktop', kind: 'desktop' };
  if (platform === 'macos') return { name: 'Telegram for Mac', kind: 'desktop' };
  if (platform.startsWith('web')) return { name: 'Telegram Web', kind: 'web' };
  return { name: 'Web', kind: 'web' };
}

interface ConnectStore {
  devices: Device[];
  running: boolean;
  start: () => () => void;
  /** Hand what is playing here to ``target``; this device stops. */
  transfer: (target: string) => Promise<boolean>;
  /** Drive a device that is playing: play / pause / next / previous / seek. */
  control: (target: string, action: Exclude<CommandBody['action'], 'transfer'>, positionS?: number) => Promise<void>;
  /** Handles one command left for this device (exported for tests). */
  apply: (command: ConnectCommand) => Promise<void>;
  beat: () => Promise<void>;
}

let timer: ReturnType<typeof setTimeout> | null = null;

export const useConnect = create<ConnectStore>((set, get) => ({
  devices: [],
  running: false,

  async beat() {
    const player = usePlayer.getState();
    const me = thisDevice();
    try {
      const answer = await sendHeartbeat({
        device_id: deviceId(),
        name: me.name,
        kind: me.kind,
        state: {
          track_id: player.current?.id ?? null,
          position_s: Math.max(0, Math.floor(player.position)),
          playing: player.isPlaying,
        },
      });
      set({ devices: answer.devices });
      for (const command of answer.commands) await get().apply(command);
    } catch {
      /* offline for a moment: the next beat tries again */
    }
  },

  start() {
    if (get().running) return () => undefined;
    set({ running: true });
    const loop = () => {
      void get()
        .beat()
        .finally(() => {
          if (!get().running) return;
          timer = setTimeout(loop, document.visibilityState === 'hidden' ? HIDDEN_MS : VISIBLE_MS);
        });
    };
    loop();
    const leave = () => void forgetDevice(deviceId()).catch(() => undefined);
    window.addEventListener('pagehide', leave);
    return () => {
      set({ running: false, devices: [] });
      if (timer) clearTimeout(timer);
      timer = null;
      window.removeEventListener('pagehide', leave);
    };
  },

  async transfer(target) {
    const player = usePlayer.getState();
    if (!player.current || player.remote) return false;
    // What is left to hear, starting with the song playing now.
    const upcoming = player.upcoming();
    const queue: Track[] = [player.current, ...upcoming.filter((t) => t.id !== player.current?.id)];
    try {
      await sendCommand({
        target,
        sender: deviceId(),
        action: 'transfer',
        track_ids: queue.slice(0, 200).map((t) => t.id),
        index: 0,
        position_s: Math.floor(player.position),
        playing: true,
      });
    } catch {
      return false;
    }
    if (usePlayer.getState().isPlaying) await usePlayer.getState().toggle();
    return true;
  },

  async control(target, action, positionS) {
    await sendCommand({ target, sender: deviceId(), action, position_s: positionS ?? 0 });
    // Show the change without waiting for the next beat.
    void get().beat();
  },

  async apply(command) {
    const player = usePlayer.getState();
    if (player.remote) return; // in a Jam, the room decides what plays
    switch (command.action) {
      case 'transfer': {
        if (command.items.length === 0) return;
        const index = Math.min(command.index, command.items.length - 1);
        await player.follow(command.items, index, () => command.position_s, command.playing);
        return;
      }
      case 'play':
        if (!player.isPlaying) await player.toggle();
        return;
      case 'pause':
        if (player.isPlaying) await player.toggle();
        return;
      case 'next':
        await player.next();
        return;
      case 'previous':
        await player.previous();
        return;
      case 'seek':
        player.seek(command.position_s);
        return;
    }
  },
}));
