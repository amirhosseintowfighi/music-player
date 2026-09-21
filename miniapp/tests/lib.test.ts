import { describe, expect, it, vi } from 'vitest';

import { formatNumber, translate } from '@/i18n';
import { DEFAULT_PALETTE, applyPalette, paletteFromImageData, parsePalette } from '@/lib/color';
import { artistNames, coverColors, duration, relativeTime } from '@/lib/format';
import { deviceLooksSlow, watchFrameRate } from '@/lib/perf';
import { getInitData, haptic, initTelegram } from '@/lib/telegram';

describe('formatting', () => {
  it('formats durations in both scripts', () => {
    expect(duration(245, 'en')).toBe('4:05');
    expect(duration(3725, 'en')).toBe('1:02:05');
    expect(duration(0, 'en')).toBe('0:00');
    expect(duration(-5, 'en')).toBe('0:00');
    expect(duration(245, 'fa')).toBe('۴:۰۵');
  });

  it('formats relative time', () => {
    const now = Date.parse('2026-09-18T12:00:00Z');
    expect(relativeTime('2026-09-18T11:59:30Z', 'en', now)).toBe('just now');
    expect(relativeTime('2026-09-18T11:30:00Z', 'en', now)).toBe('30 minutes ago');
    expect(relativeTime('2026-09-18T06:00:00Z', 'en', now)).toBe('6 hours ago');
    expect(relativeTime('2026-09-17T09:00:00Z', 'en', now)).toBe('yesterday');
    expect(relativeTime('2026-09-10T09:00:00Z', 'en', now)).toBe('8 days ago');
    expect(relativeTime(null, 'en', now)).toBe('');
    expect(relativeTime('nonsense', 'en', now)).toBe('');
  });

  it('keeps cover colours stable per track', () => {
    expect(coverColors(5)).toEqual(coverColors(5));
    expect(coverColors('abc')).toEqual(coverColors('abc'));
    expect(coverColors(5)).not.toEqual(coverColors(6));
  });

  it('joins artist names', () => {
    expect(artistNames({ artists: [{ name: 'معین' }, { name: 'هایده' }] })).toBe('معین، هایده');
    expect(artistNames({ artists: [] })).toBe('');
  });
});

describe('i18n', () => {
  it('interpolates and localises numbers', () => {
    expect(translate('en', 'home.summary', { channels: 4, tracks: 1240 })).toBe('4 channels · 1,240 tracks');
    expect(translate('fa', 'channel.status.indexing', { percent: 62 })).toBe('در حال ایندکس · ۶۲٪');
    expect(formatNumber(1240, 'fa')).toBe('۱٬۲۴۰');
  });

  it('falls back to English for a missing Persian string', () => {
    expect(translate('fa', 'tab.home')).toBe('خانه');
    expect(translate('en', 'tab.home')).toBe('Home');
  });

  it('leaves unknown placeholders untouched', () => {
    expect(translate('en', 'home.summary', { channels: 1 })).toContain('{tracks}');
  });
});

describe('colour extraction', () => {
  it('finds the dominant colours of an image', () => {
    const pixels = new Uint8ClampedArray(16 * 4);
    for (let i = 0; i < 12; i += 1) {
      pixels.set([200, 30, 30, 255], i * 4); // mostly red
    }
    for (let i = 12; i < 16; i += 1) {
      pixels.set([20, 20, 200, 255], i * 4); // some blue
    }
    const palette = paletteFromImageData(pixels);
    expect(palette.c1).toMatch(/^#[0-9a-f]{6}$/);
    expect(palette.c1).not.toBe(palette.c2);
  });

  it('ignores transparent pixels and falls back', () => {
    const palette = paletteFromImageData(new Uint8ClampedArray(64));
    expect(palette).toEqual(DEFAULT_PALETTE);
  });

  it('writes the palette to CSS variables', () => {
    applyPalette({ c1: '#111111', c2: '#222222', c3: '#333333' });
    expect(document.documentElement.style.getPropertyValue('--art-1')).toBe('#111111');
  });
});

describe('performance guard', () => {
  it('treats low-memory devices as slow', () => {
    const nav = navigator as Navigator & { deviceMemory?: number };
    const original = nav.deviceMemory;
    Object.defineProperty(nav, 'deviceMemory', { configurable: true, value: 2 });
    expect(deviceLooksSlow()).toBe(true);
    Object.defineProperty(nav, 'deviceMemory', { configurable: true, value: 8 });
    Object.defineProperty(nav, 'hardwareConcurrency', { configurable: true, value: 8 });
    expect(deviceLooksSlow()).toBe(false);
    Object.defineProperty(nav, 'deviceMemory', { configurable: true, value: original });
  });

  it('reports a sustained low frame rate once', async () => {
    const onSlow = vi.fn();
    let now = performance.now();
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.push(cb);
      return frames.length;
    });
    vi.stubGlobal('cancelAnimationFrame', () => undefined);
    const stop = watchFrameRate(onSlow);
    // 10 frames spread over 2.5 seconds ≈ 4 fps.
    for (let i = 0; i < 10; i += 1) {
      now += 250;
      frames.shift()?.(now);
    }
    expect(onSlow).toHaveBeenCalledTimes(1);
    stop();
    vi.unstubAllGlobals();
  });
});

describe('telegram bridge', () => {
  it('reads initData from the WebApp object', () => {
    vi.stubGlobal('Telegram', { WebApp: { initData: 'user=1&hash=2', colorScheme: 'light' } });
    expect(getInitData()).toBe('user=1&hash=2');
    expect(initTelegram().colorScheme).toBe('light');
    vi.unstubAllGlobals();
  });

  it('degrades gracefully outside Telegram', () => {
    vi.stubGlobal('Telegram', undefined);
    expect(getInitData()).toBe('');
    expect(initTelegram().colorScheme).toBe('dark');
    expect(() => haptic('success')).not.toThrow();
    vi.unstubAllGlobals();
  });
});

describe('stored palettes', () => {
  it('reads a palette the server already knows', () => {
    expect(parsePalette('#2bd9c4,#4a6bff,#b02bd9')).toEqual({
      c1: '#2bd9c4',
      c2: '#4a6bff',
      c3: '#b02bd9',
    });
  });

  it('refuses anything that is not three hex colours', () => {
    for (const bad of ['', null, undefined, '#fff,#fff,#fff', 'red,green,blue', '#2bd9c4,#4a6bff']) {
      expect(parsePalette(bad)).toBeNull();
    }
  });
});

describe('crediting a track', () => {
  const base = { artists: [{ name: 'معین' }] };

  it('names the artists when there are any', () => {
    expect(artistNames({ ...base, artists: [{ name: 'معین' }, { name: 'هایده' }] })).toBe('معین، هایده');
  });

  it('falls back to the source channel instead of "Unknown artist"', () => {
    expect(artistNames({ artists: [], channel: { title: 'Persian Hits', username: 'ph' } })).toBe(
      'Persian Hits',
    );
    expect(artistNames({ artists: [], channel: { title: null, username: 'ph' } })).toBe('@ph');
  });

  it('says nothing rather than something wrong when even that is missing', () => {
    expect(artistNames({ artists: [] })).toBe('');
    expect(artistNames({ artists: [], channel: null })).toBe('');
  });
});
