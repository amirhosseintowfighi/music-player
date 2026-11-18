/** Even loudness and data saver in the web app (shared with the Mini App). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { prefetch, resetForTests, ticketFor } from '@/player/engine';
import { gainFor, saverActive, TARGET_DB, useAudioSettings } from '@/store/audio';

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
  Object.defineProperty(navigator, 'connection', { configurable: true, value: undefined });
});
afterEach(() => vi.unstubAllGlobals());

describe('web playback extras', () => {
  it('levels songs towards one target', () => {
    expect(gainFor(TARGET_DB)).toBe(0);
    expect(gainFor(-10)).toBe(-6);
    expect(gainFor(null)).toBe(0);
  });

  it('asks for the small copy with data saver on and skips warming up', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return new Response(JSON.stringify(ticket), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    useAudioSettings.getState().set({ saver: 'always' });
    expect(saverActive()).toBe(true);
    await ticketFor(5);
    await prefetch(6);
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain('/v1/tracks/5/stream?saver=1');
    useAudioSettings.getState().set({ saver: 'cellular' });
  });
});

describe('install as an app', () => {
  it('keeps the browser prompt and uses it once', async () => {
    const { listenForInstall, useInstall } = await import('@/lib/install');
    listenForInstall();
    const prompt = vi.fn(async () => undefined);
    const event = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
      prompt,
      userChoice: Promise.resolve({ outcome: 'accepted' as const }),
    });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true); // no mini-infobar: our button asks
    expect(useInstall.getState().prompt).toBe(event);
    expect(await useInstall.getState().install()).toBe(true);
    expect(prompt).toHaveBeenCalledOnce();
    expect(useInstall.getState()).toMatchObject({ prompt: null, installed: true });
  });
});
