import { describe, expect, it } from 'vitest';

describe('web smoke', () => {
  it('routes are defined', async () => {
    const mod = await import('@/App');
    expect(mod.App).toBeDefined();
  });
  it('offline engine exports', async () => {
    const eng = await import('@/player/engine');
    expect(eng.saveOffline).toBeDefined();
    expect(eng.offlineUrl).toBeDefined();
    expect(eng.listOffline).toBeDefined();
    expect(eng.removeOffline).toBeDefined();
  });
});
