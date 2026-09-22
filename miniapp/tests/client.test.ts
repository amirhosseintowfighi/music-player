import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, __setAuthForTests, clearTokens, get, login, post, qs } from '@/api/client';

import { mockApi } from './helpers';

const tokenPair = (suffix: string, ttl = 900) => ({
  access_token: `access-${suffix}`,
  access_expires_at: Math.floor(Date.now() / 1000) + ttl,
  refresh_token: `refresh-${suffix}`,
  refresh_expires_at: Math.floor(Date.now() / 1000) + 86400,
  token_type: 'bearer',
  me: {},
});

beforeEach(() => {
  clearTokens();
  __setAuthForTests(null);
  vi.stubGlobal('Telegram', { WebApp: { initData: 'user=%7B%22id%22%3A1%7D&hash=abc' } });
});

describe('auth', () => {
  it('logs in with initData and sends the bearer token', async () => {
    const { calls } = mockApi({
      'POST /v1/auth/telegram': tokenPair('1'),
      'GET /v1/me': { id: 7 },
    });
    await login();
    await get('/v1/me');
    expect(calls[0]?.body).toEqual({ init_data: 'user=%7B%22id%22%3A1%7D&hash=abc' });
    expect(localStorage.getItem('tmusic.refresh')).toBe('refresh-1');
  });

  it('refreshes once when several requests race a 401', async () => {
    let refreshes = 0;
    let accepted = 'access-old';
    __setAuthForTests('access-old');
    localStorage.setItem('tmusic.refresh', 'refresh-old');
    mockApi({
      'POST /v1/auth/refresh': () => {
        refreshes += 1;
        accepted = 'access-new';
        return tokenPair('new');
      },
      'GET /v1/library/tracks': () =>
        accepted === 'access-new'
          ? { items: [], next_cursor: null }
          : new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'expired' } }), { status: 401 }),
    });

    const results = await Promise.all([
      get('/v1/library/tracks'),
      get('/v1/library/tracks'),
      get('/v1/library/tracks'),
    ]);
    expect(results).toHaveLength(3);
    expect(refreshes).toBe(1);
  });

  it('falls back to initData login when the refresh token was revoked', async () => {
    __setAuthForTests(null);
    localStorage.setItem('tmusic.refresh', 'stolen');
    const { calls } = mockApi({
      'POST /v1/auth/refresh': new Response(
        JSON.stringify({ error: { code: 'unauthorized', message: 'reuse' } }),
        { status: 401 },
      ),
      'POST /v1/auth/telegram': tokenPair('fresh'),
      'GET /v1/me': { id: 7 },
    });
    await get('/v1/me');
    expect(calls.map((c) => c.url)).toEqual([
      '/v1/auth/refresh',
      '/v1/auth/telegram',
      '/v1/me',
    ]);
    expect(localStorage.getItem('tmusic.refresh')).toBe('refresh-fresh');
  });

  it('surfaces plan limits as ApiError with details', async () => {
    __setAuthForTests('access');
    mockApi({
      'POST /v1/library/channels': new Response(
        JSON.stringify({
          error: { code: 'plan_limit', message: 'limit', details: { kind: 'channels', limit: 3 } },
        }),
        { status: 402 },
      ),
    });
    const error = await post('/v1/library/channels', { ref: '@x' }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).isPlanLimit).toBe(true);
    expect((error as ApiError).details).toEqual({ kind: 'channels', limit: 3 });
  });

  it('works when localStorage throws (private mode)', async () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error('denied');
    };
    mockApi({ 'POST /v1/auth/telegram': tokenPair('nostore'), 'GET /v1/me': { id: 1 } });
    await expect(get('/v1/me')).resolves.toEqual({ id: 1 });
    Storage.prototype.setItem = original;
  });
});

describe('qs', () => {
  it('drops empty values and encodes the rest', () => {
    expect(qs({ q: 'معین', limit: 20, cursor: null, scope: undefined, empty: '' })).toBe(
      '?q=%D9%85%D8%B9%DB%8C%D9%86&limit=20',
    );
    expect(qs({})).toBe('');
  });
});

describe('opening the Mini App in the wrong place', () => {
  it('reports "no init data" rather than a network failure', async () => {
    const { login, ApiError } = await import('@/api/client');
    vi.stubGlobal('Telegram', undefined);

    await expect(login()).rejects.toMatchObject({ code: 'no_init_data', status: 401 });
    await expect(login()).rejects.toBeInstanceOf(ApiError);
  });
});
