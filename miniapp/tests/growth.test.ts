/**
 * Even loudness and data saver: what the player does with a song's measured level,
 * and how little it downloads on mobile data.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { prefetch, resetForTests, ticketFor } from '@/player/engine';
import { gainFor, levelDb, onMeteredNetwork, saverActive, TARGET_DB, useAudioSettings } from '@/store/audio';

import { mockApi } from './helpers';

function connection(value: Record<string, unknown> | undefined): void {
  Object.defineProperty(navigator, 'connection', { configurable: true, value });
}

const ticket = {
  url: 'https://cdn.test/s/5?t=x',
  thumb_url: null,
  expires_at: Math.floor(Date.now() / 1000) + 600,
  size: 1,
  mime: 'audio/mpeg',
};

beforeEach(() => {
  resetForTests();
  __setAuthForTests('token');
  connection(undefined);
  useAudioSettings.getState().set({ saver: 'cellular', normalize: false });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('levelling', () => {
  it('turns every song towards one level, within bounds', () => {
    expect(gainFor(null)).toBe(0);
    expect(gainFor(TARGET_DB)).toBe(0);
    expect(gainFor(-10)).toBe(-6); // loud master: down
    expect(gainFor(-19)).toBe(3); // quiet one: up
    expect(gainFor(0)).toBe(-9); // never cut more than 9 dB
    expect(gainFor(-50)).toBe(6); // never boost more than 6 dB
  });

  it('measures RMS in dBFS', () => {
    expect(levelDb(new Float32Array(512).fill(1))).toBeCloseTo(0);
    expect(levelDb(new Float32Array(512).fill(0.1))).toBeCloseTo(-20);
    expect(levelDb(new Float32Array(512))).toBe(-Infinity);
  });
});

describe('data saver', () => {
  it('is on for mobile data by default, and follows the setting', () => {
    expect(saverActive()).toBe(false);
    connection({ type: 'cellular' });
    expect(onMeteredNetwork()).toBe(true);
    expect(saverActive()).toBe(true);
    connection({ type: 'wifi', saveData: true });
    expect(saverActive()).toBe(true); // the browser's own "save data"
    useAudioSettings.getState().set({ saver: 'off' });
    expect(saverActive()).toBe(false);
    connection(undefined);
    useAudioSettings.getState().set({ saver: 'always' });
    expect(saverActive()).toBe(true);
  });

  it('asks for the small copy and skips warming up the next song', async () => {
    const api = mockApi({ 'POST /v1/tracks/5/stream': ticket, 'POST /v1/tracks/6/stream': ticket });
    useAudioSettings.getState().set({ saver: 'always' });
    await ticketFor(5);
    await prefetch(6);
    expect(api.calls.map((c) => c.url)).toEqual(['/v1/tracks/5/stream?saver=1']);

    useAudioSettings.getState().set({ saver: 'off' });
    resetForTests();
    await ticketFor(5);
    expect(api.calls.at(-1)?.url).toBe('/v1/tracks/5/stream');
  });
});
