/**
 * API client for the standalone Web App.
 * Auth is the Telegram Login Widget (browser), not Mini App initData.
 * Same token rotation as miniapp, without the initData fallback.
 */

import type { components } from './schema';

export type Track = components['schemas']['TrackOut'];
export type Channel = components['schemas']['ChannelOut'];
export type UserChannel = components['schemas']['UserChannelOut'];
export type Artist = components['schemas']['ArtistOut'];
export type ArtistPage = components['schemas']['ArtistPageOut'];
export type AlbumPage = components['schemas']['AlbumPageOut'];
export type Album = components['schemas']['AlbumOut'];
export type Category = components['schemas']['CategoryOut'];
export type Me = components['schemas']['MeOut'];
export type StreamTicket = components['schemas']['StreamOut'];
export type SearchResult = components['schemas']['SearchOut'];
export type Suggestions = components['schemas']['SuggestOut'];
export type TokenPair = components['schemas']['TokenOut'];
export type Playlist = components['schemas']['PlaylistOut'];
export type PlaylistDetail = components['schemas']['PlaylistDetailOut'];
export type PlaybackState = components['schemas']['PlaybackStateOut'];
export type LikeState = components['schemas']['LikeOut'];

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** Plan limit hit — the UI shows the upsell sheet. */
  get isPlanLimit(): boolean {
    return this.status === 402;
  }
}

const REFRESH_KEY = 'tmusic.refresh';
const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

let accessToken: string | null = null;
let accessExpiresAt = 0;
let inflightAuth: Promise<void> | null = null;
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

function readRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null; // private mode / storage disabled
  }
}

function storeTokens(pair: TokenPair): void {
  accessToken = pair.access_token;
  accessExpiresAt = pair.access_expires_at * 1000;
  try {
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  } catch {
    /* session-only auth is fine */
  }
}

/** The current access token, if any — for diagnostics that go out on their own. */
export function currentAccessToken(): string | null {
  return accessToken;
}

export function clearTokens(): void {
  accessToken = null;
  accessExpiresAt = 0;
  try {
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* ignore */
  }
}

async function parse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    // Almost always one thing: the app was built without VITE_API_URL, so the
    // request went to its own static host and came back as index.html. Saying so
    // beats "network error" by a mile when you are the one running the server.
    throw new ApiError(
      response.status,
      'bad_response',
      `Expected JSON from ${response.url || 'the API'}, got ${text.slice(0, 40)}…`,
    );
  }
}

async function raw<T>(path: string, init: RequestInit = {}, token?: string | null): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  const body = (await parse(response)) as { error?: { code: string; message: string; details?: Record<string, unknown> } };
  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(
      response.status,
      error?.code ?? 'network',
      error?.message ?? response.statusText,
      error?.details ?? {},
    );
  }
  return body as T;
}

async function refreshTokens(): Promise<void> {
  const refresh = readRefresh();
  if (!refresh) throw new ApiError(401, 'no_refresh', 'Not authenticated');
  try {
    storeTokens(
      await raw<TokenPair>('/v1/auth/refresh', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refresh }),
      }),
    );
  } catch (error) {
    if (error instanceof ApiError && error.status < 500) clearTokens();
    throw error;
  }
}

export async function loginWithWidget(payload: Record<string, unknown>): Promise<TokenPair> {
  const pair = await raw<TokenPair>('/v1/auth/widget', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  storeTokens(pair);
  return pair;
}

/** Ensures a usable access token, refreshing at most once concurrently. */
async function authorize(force = false): Promise<void> {
  if (!force && accessToken && Date.now() < accessExpiresAt - 30_000) return;
  inflightAuth ??= refreshTokens().finally(() => {
    inflightAuth = null;
  });
  await inflightAuth;
}

export function hasRefreshToken(): boolean {
  return Boolean(readRefresh());
}

export async function login(): Promise<TokenPair> {
  await refreshTokens();
  // after refreshTokens, accessToken is set in memory; fetch me to return shape
  // callers that need TokenPair after widget login should use loginWithWidget
  throw new ApiError(401, 'use_widget', 'Widget login required');
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  await authorize();
  try {
    return await raw<T>(path, init, accessToken);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      try {
        await authorize(true);
      } catch {
        onUnauthorized?.();
        throw error;
      }
      return raw<T>(path, init, accessToken);
    }
    if (error instanceof ApiError && error.status === 403 && error.code === 'forbidden') {
      onUnauthorized?.();
    }
    throw error;
  }
}

export const get = <T>(path: string): Promise<T> => api<T>(path);
export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown): Promise<T> =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
export const del = <T>(path: string): Promise<T> => api<T>(path, { method: 'DELETE' });

/** Builds a query string, dropping empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

/** Test seam: lets tests start from a known auth state. */
export function __setAuthForTests(token: string | null, expiresAt = Date.now() + 60_000): void {
  accessToken = token;
  accessExpiresAt = expiresAt;
  inflightAuth = null;
}
