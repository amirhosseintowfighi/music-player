import { beforeEach, expect, it, vi } from 'vitest';

/**
 * Telegram opens the Mini App at "#tgWebAppData=…", which HashRouter reads as a
 * route — so the first thing every user saw was the "no such page" screen.
 */
async function boot(hash: string): Promise<string> {
  window.location.hash = hash;
  vi.resetModules();
  vi.doMock('@/App', () => ({ App: () => null }));
  vi.doMock('@/design/tokens.css', () => ({}));
  await import('@/main');
  return window.location.hash;
}

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
});

it('replaces Telegram launch parameters with a real route', async () => {
  expect(await boot('#tgWebAppData=query_id%3DAAA&tgWebAppVersion=7.0')).toBe('#/');
});

it('leaves a real route alone', async () => {
  expect(await boot('#/library')).toBe('#/library');
});

it('survives being opened with no hash at all', async () => {
  expect(await boot('')).toBe('');
});
