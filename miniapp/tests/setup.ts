import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// jsdom has no media element implementation; the player only needs these to exist.
Object.defineProperty(HTMLMediaElement.prototype, 'play', {
  configurable: true,
  value: vi.fn().mockResolvedValue(undefined),
});
Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
  configurable: true,
  value: vi.fn(),
});
Object.defineProperty(HTMLMediaElement.prototype, 'load', { configurable: true, value: vi.fn() });

if (!globalThis.matchMedia) {
  globalThis.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof globalThis.matchMedia;
}

if (!globalThis.IntersectionObserver) {
  globalThis.IntersectionObserver = class {
    observe = vi.fn();
    disconnect = vi.fn();
    unobserve = vi.fn();
    takeRecords = vi.fn(() => []);
    root = null;
    rootMargin = '';
    thresholds = [];
  } as unknown as typeof globalThis.IntersectionObserver;
}

globalThis.requestAnimationFrame ??= ((cb: FrameRequestCallback) =>
  setTimeout(() => cb(performance.now()), 16) as unknown as number) as typeof requestAnimationFrame;
globalThis.cancelAnimationFrame ??= ((id: number) => clearTimeout(id)) as typeof cancelAnimationFrame;

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});
