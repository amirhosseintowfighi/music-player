/**
 * Admin API client.
 *
 * The admin token is kept in localStorage because the panel is a plain web app with
 * no Telegram session to fall back on; it expires in 8 hours and a 401 sends the
 * operator back to the login screen rather than silently retrying.
 */
import type { components } from './schema';

export type AdminMe = components['schemas']['AdminMeOut'];
export type AdminToken = components['schemas']['AdminTokenOut'];
export type Overview = components['schemas']['OverviewOut'];
export type UserRow = components['schemas']['UserRowOut'];
export type UsersPage = components['schemas']['UsersPageOut'];
export type UserDetail = components['schemas']['UserDetailOut'];
export type PendingPayment = components['schemas']['PendingPaymentOut'];
export type Report = components['schemas']['ReportOut'];
export type Broadcast = components['schemas']['AdminBroadcastOut'];
export type AuditEntry = components['schemas']['AuditEntryOut'];
export type Health = components['schemas']['HealthOut'];
export type Candidate = components['schemas']['CandidateOut'];
export type CandidatePage = components['schemas']['CandidatePageOut'];
export type ImportResult = components['schemas']['ImportChannelsOut'];
export type CrawlerHealth = components['schemas']['CrawlerHealthOut'];
export type CrawlChannel = components['schemas']['CrawlChannelOut'];
export type ParserHealth = components['schemas']['ParserHealthOut'];
export type ResolverStatus = components['schemas']['ResolverStatusOut'];
export type MetadataRow = components['schemas']['MetadataReviewOut'];

export interface MetricPoint {
  day: string;
  value: number;
}

const TOKEN_KEY = 'tmusic.admin.token';
const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* a private window still works, it just forgets on reload */
  }
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
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = readToken();
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

  if (response.status === 401) {
    saveToken(null);
    onUnauthorized?.();
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown> };
    };
    throw new ApiError(
      response.status,
      payload.error?.code ?? 'error',
      payload.error?.message ?? response.statusText,
      payload.error?.details ?? {},
    );
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T; // CSV and other plain bodies
  }
}

export const get = <T>(path: string) => request<T>('GET', path);
export const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body);
export const put = <T>(path: string, body?: unknown) => request<T>('PUT', path, body);
export const patch = <T>(path: string, body?: unknown) => request<T>('PATCH', path, body);

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

export async function loginWithWidget(payload: Record<string, unknown>): Promise<AdminToken> {
  const token = await post<AdminToken>('/admin/login', payload);
  saveToken(token.access_token);
  return token;
}

/** The Telegram-free door: works on any device, needs no bot and no widget. */
export async function loginWithPassword(username: string, password: string): Promise<AdminToken> {
  const token = await post<AdminToken>('/admin/login/password', { username, password });
  saveToken(token.access_token);
  return token;
}

/** `null` when nobody is signed in — the router shows the login screen. */
export async function fetchMe(): Promise<AdminMe | null> {
  if (!readToken()) return null;
  try {
    return await get<AdminMe>('/admin/me');
  } catch {
    return null;
  }
}
