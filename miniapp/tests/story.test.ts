/** Share to a Telegram story: card up to the API, then Telegram's story editor. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { shareTrackToStory } from '@/lib/share';
import { canShareToStory } from '@/lib/telegram';

import { makeTrack, mockApi } from './helpers';

vi.mock('@/lib/shareCard', () => ({
  drawCard: async () => ({
    toBlob: (done: (blob: Blob) => void) => done(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 1, 2, 3])])),
  }),
}));

const shared: { url: string; params: unknown }[] = [];

function telegram(version: string | null): void {
  vi.stubGlobal('Telegram', {
    WebApp: {
      initData: 'query_id=1',
      ...(version === null
        ? {}
        : {
            isVersionAtLeast: (v: string) => Number(version) >= Number(v),
            shareToStory: (url: string, params: unknown) => shared.push({ url, params }),
          }),
    },
  });
}

beforeEach(() => {
  shared.length = 0;
  __setAuthForTests('token');
});
afterEach(() => vi.unstubAllGlobals());

describe('share to story', () => {
  it('is offered only where Telegram has the story editor', () => {
    telegram(null);
    expect(canShareToStory()).toBe(false);
    telegram('7.7');
    expect(canShareToStory()).toBe(false);
    telegram('8.0');
    expect(canShareToStory()).toBe(true);
  });

  it('uploads the card and opens the editor with a link back to the song', async () => {
    telegram('8.0');
    const api = mockApi({ 'POST /v1/share/story': { url: 'https://api.test/v1/share/story/abc', expires_in: 3600 } });
    const ok = await shareTrackToStory(makeTrack({ id: 42, title: 'Khaneh' }));
    expect(ok).toBe(true);
    const upload = api.calls.find((c) => c.url.endsWith('/v1/share/story'));
    expect((upload?.body as { image: string }).image).toBe(btoa(String.fromCharCode(0xff, 0xd8, 0xff, 1, 2, 3)));
    expect(shared[0]?.url).toBe('https://api.test/v1/share/story/abc');
    expect(JSON.stringify(shared[0]?.params)).toContain('startapp=tr_42');
  });
});
