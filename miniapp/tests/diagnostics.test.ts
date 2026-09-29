import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { markStage, reportError, safePath, session, startDiagnostics } from '@/lib/diagnostics';

const beacons: { url: string; body: Record<string, string> }[] = [];

beforeEach(async () => {
  beacons.length = 0;
  localStorage.clear();
  __setAuthForTests(null);
  Object.defineProperty(navigator, 'sendBeacon', {
    configurable: true,
    value: (url: string, blob: Blob) => {
      void blob.text().then((text) => beacons.push({ url, body: JSON.parse(text) as Record<string, string> }));
      return true;
    },
  });
  (globalThis as { Telegram?: unknown }).Telegram = { WebApp: { platform: 'tdesktop', version: '8.0' } };
});

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('diagnostics', () => {
  it('never sends Telegram launch data, only the app route', () => {
    expect(safePath('#tgWebAppData=user%3D%257B%2522id%2522&hash=abc')).toBe('');
    expect(safePath('#/library?tab=albums')).toBe('#/library');
    expect(safePath('')).toBe('');
  });

  it('reports the boot, with the device, before anything else', async () => {
    startDiagnostics();
    await flush();
    expect(beacons).toHaveLength(1);
    expect(beacons[0]?.url).toBe('/v1/telemetry/client');
    expect(beacons[0]?.body).toMatchObject({ kind: 'boot', platform: 'tdesktop', tg_version: '8.0', session });
    expect(JSON.parse(localStorage.getItem('tmusic.lastBoot') ?? '{}')).toMatchObject({ stage: 'boot' });
  });

  it('reports the last start as a crash when it never got to "ready"', async () => {
    localStorage.setItem('tmusic.lastBoot', JSON.stringify({ session: 'old1', stage: 'render', at: Date.now() - 5000, path: '#/' }));
    startDiagnostics();
    await flush();
    const crash = beacons.find((b) => b.body.kind === 'crash');
    expect(crash?.body).toMatchObject({ stage: 'render' });
    expect(crash?.body.message).toContain('old1');
  });

  it('does not call a normal close or a finished start a crash', async () => {
    for (const stage of ['ready', 'closed']) {
      beacons.length = 0;
      localStorage.setItem('tmusic.lastBoot', JSON.stringify({ session: 'old2', stage, at: Date.now(), path: '' }));
      startDiagnostics();
      await flush();
      expect(beacons.map((b) => b.body.kind)).toEqual(['boot']);
    }
  });

  it('becomes ready once the app has held up for a while', () => {
    vi.useFakeTimers();
    startDiagnostics();
    markStage('shell');
    expect(JSON.parse(localStorage.getItem('tmusic.lastBoot') ?? '{}').stage).toBe('shell');
    vi.advanceTimersByTime(10_000);
    expect(JSON.parse(localStorage.getItem('tmusic.lastBoot') ?? '{}').stage).toBe('ready');
    vi.useRealTimers();
  });

  it('sends script errors with the token when logged in', async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetch);
    __setAuthForTests('tok');
    reportError(new TypeError('boom'));
    expect(fetch).toHaveBeenCalledTimes(1);
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(JSON.parse(String(init.body))).toMatchObject({ kind: 'error', message: 'window: TypeError: boom' });
    vi.unstubAllGlobals();
  });
});


describe('boot.js (before the app bundle)', () => {
  async function runBootScript(hash: string): Promise<void> {
    const { readFileSync } = await import('node:fs');
    const code = readFileSync(`${process.cwd()}/public/boot.js`, 'utf8');
    window.location.hash = hash;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(code)();
  }

  it('reports the boot and a crash of a start that died before the app ran', async () => {
    localStorage.setItem('tmusic.lastBoot', JSON.stringify({ session: 'died', stage: 'html', at: Date.now() - 1000, path: '' }));
    await runBootScript('#tgWebAppData=secret%3D1&tgWebAppVersion=9.6&tgWebAppPlatform=tdesktop');
    await flush();
    const kinds = beacons.map((b) => b.body.kind);
    expect(kinds).toEqual(['crash', 'boot']);
    expect(beacons[0]?.body).toMatchObject({ stage: 'html', platform: 'tdesktop', tg_version: '9.6', path: '' });
    expect(JSON.stringify(beacons)).not.toContain('secret');
    const early = (window as unknown as { __tmBoot: { session: string } }).__tmBoot;
    expect(JSON.parse(localStorage.getItem('tmusic.lastBoot') ?? '{}')).toMatchObject({ stage: 'html', session: early.session });
    delete (window as unknown as { __tmBoot?: unknown }).__tmBoot;
    window.location.hash = '';
  });
});
