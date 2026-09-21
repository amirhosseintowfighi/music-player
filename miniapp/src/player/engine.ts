/**
 * Audio engine: one <audio> element, stream tickets, MediaSession.
 *
 * Tickets are short-lived signed URLs (ADR-0004), so they are cached per track and
 * re-requested when they are about to expire. The element is created lazily on the
 * first play so that a Mini App that never plays anything costs nothing.
 */
import { ApiError, post, type StreamTicket, type Track } from '@/api/client';

const TICKET_SAFETY_MS = 20_000;

interface CachedTicket extends StreamTicket {
  fetchedAt: number;
  /** A prefetch ticket may only read the head of the file; it cannot play a track. */
  prefetch: boolean;
}

const tickets = new Map<number, CachedTicket>();
let element: HTMLAudioElement | null = null;

export function audio(): HTMLAudioElement {
  if (!element) {
    element = new Audio();
    element.preload = 'metadata';
    element.crossOrigin = 'anonymous';
  }
  return element;
}

export function resetForTests(): void {
  element = null;
  tickets.clear();
}

/**
 * A ticket for this track.
 *
 * `prefetch` asks for the cheap kind: it does not count against the daily limit and
 * the edge will only serve the first few hundred kilobytes with it (ADR-003 §2-3).
 * A cached prefetch ticket is never reused for a real play — playback upgrades to a
 * counted ticket, which is also what keeps the play statistics honest.
 */
export async function ticketFor(trackId: number, prefetch = false): Promise<StreamTicket> {
  const cached = tickets.get(trackId);
  const fresh_enough = cached && cached.expires_at * 1000 - TICKET_SAFETY_MS > Date.now();
  if (fresh_enough && (prefetch || !cached.prefetch)) return cached;
  const fresh = await post<StreamTicket>(
    `/v1/tracks/${trackId}/stream${prefetch ? '?prefetch=1' : ''}`,
  );
  tickets.set(trackId, { ...fresh, fetchedAt: Date.now(), prefetch });
  return fresh;
}

/**
 * Pulls the head of a track so the next one starts instantly.
 *
 * Deliberately fire-and-forget: a failed warm-up is not an error the user should ever
 * see, and the browser cache keeps whatever arrived.
 */
export async function prefetch(trackId: number, bytes = 256 * 1024): Promise<void> {
  try {
    const ticket = await ticketFor(trackId, true);
    await fetch(ticket.url, { headers: { Range: `bytes=0-${bytes - 1}` } });
  } catch {
    /* warming up is best-effort by definition */
  }
}

export function dropTicket(trackId: number): void {
  tickets.delete(trackId);
}

export class PlaybackError extends Error {
  constructor(
    readonly kind: 'plan_limit' | 'unavailable' | 'network',
    readonly details: Record<string, unknown> = {},
  ) {
    super(kind);
  }
}

export function toPlaybackError(error: unknown): PlaybackError {
  if (error instanceof ApiError) {
    if (error.isPlanLimit) return new PlaybackError('plan_limit', error.details);
    if (error.status === 503 || error.status === 404) return new PlaybackError('unavailable', error.details);
  }
  return new PlaybackError('network');
}

/** Offline copies (Pro) are stored in the Cache API and win over the network. */
const OFFLINE_CACHE = 'tmusic-offline-v1';

export async function offlineUrl(trackId: number): Promise<string | null> {
  try {
    const cache = await caches.open(OFFLINE_CACHE);
    const hit = await cache.match(`/offline/${trackId}`);
    if (!hit) return null;
    return URL.createObjectURL(await hit.blob());
  } catch {
    return null;
  }
}

export async function saveOffline(trackId: number, onProgress?: (ratio: number) => void): Promise<void> {
  const ticket = await ticketFor(trackId);
  const response = await fetch(ticket.url);
  if (!response.ok || !response.body) throw new PlaybackError('unavailable');
  const total = ticket.size || Number(response.headers.get('Content-Length') ?? 0);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (total) onProgress?.(received / total);
  }
  const blob = new Blob(chunks as BlobPart[], { type: ticket.mime });
  const cache = await caches.open(OFFLINE_CACHE);
  await cache.put(`/offline/${trackId}`, new Response(blob, { headers: { 'Content-Type': ticket.mime } }));
}

export async function removeOffline(trackId: number): Promise<void> {
  try {
    const cache = await caches.open(OFFLINE_CACHE);
    await cache.delete(`/offline/${trackId}`);
  } catch {
    /* nothing to remove */
  }
}

export async function listOffline(): Promise<number[]> {
  try {
    const cache = await caches.open(OFFLINE_CACHE);
    return (await cache.keys())
      .map((request) => Number(new URL(request.url).pathname.split('/').pop()))
      .filter((id) => Number.isFinite(id));
  } catch {
    return [];
  }
}

export function setMediaSession(
  track: Track | null,
  artwork: string | null,
  handlers: Partial<Record<MediaSessionAction, () => void>>,
): void {
  const session = navigator.mediaSession;
  if (!session) return;
  try {
    session.metadata = track
      ? new MediaMetadata({
          title: track.title,
          artist: track.artists.map((a) => a.name).join(', '),
          album: track.album ?? '',
          artwork: artwork ? [{ src: artwork, sizes: '320x320', type: 'image/jpeg' }] : [],
        })
      : null;
    for (const [action, handler] of Object.entries(handlers)) {
      session.setActionHandler(action as MediaSessionAction, handler ?? null);
    }
  } catch {
    /* MediaSession is best-effort */
  }
}

export function setPlaybackState(state: MediaSessionPlaybackState): void {
  try {
    if (navigator.mediaSession) navigator.mediaSession.playbackState = state;
  } catch {
    /* ignore */
  }
}

export function setPositionState(position: number, duration: number, rate: number): void {
  try {
    if (navigator.mediaSession?.setPositionState && duration > 0 && Number.isFinite(duration)) {
      navigator.mediaSession.setPositionState({
        duration,
        position: Math.min(position, duration),
        playbackRate: rate,
      });
    }
  } catch {
    /* Safari throws on odd values */
  }
}

/**
 * Playback telemetry (ADR-003 phase 10).
 *
 * Time to first sound, stalls and failures are only visible here, so the client is
 * the only thing that can report them. Best-effort and never awaited by the player:
 * a struggling connection must not be made worse by our own bookkeeping.
 */
export function report(kind: 'start' | 'underrun' | 'error', extra: Record<string, unknown> = {}): void {
  void post('/v1/telemetry/playback', { kind, ...extra }).catch(() => undefined);
}

/** Retry delays for a failed load: the network usually comes back within seconds. */
export const RETRY_DELAYS_MS = [400, 1200, 3000];

export function retryDelay(attempt: number): number | null {
  return RETRY_DELAYS_MS[attempt] ?? null;
}

/**
 * Short volume ramps between tracks (ADR-003 phase 11).
 *
 * Not a crossfade: one <audio> element cannot overlap two sources, and a second
 * element would double the bandwidth for an effect nobody asked for. This is the
 * part that is actually audible — the click at the start of a track and the abrupt
 * cut when skipping.
 */
const FADE_STEP_MS = 25;
let fadeTimer: ReturnType<typeof setInterval> | null = null;

export function cancelFade(): void {
  if (fadeTimer) clearInterval(fadeTimer);
  fadeTimer = null;
}

export function fadeTo(target: number, ms: number): Promise<void> {
  const el = audio();
  cancelFade();
  const clamped = Math.max(0, Math.min(1, target));
  const steps = Math.max(1, Math.round(ms / FADE_STEP_MS));
  const delta = (clamped - el.volume) / steps;
  if (ms <= 0 || Math.abs(delta) < 0.001) {
    el.volume = clamped;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let left = steps;
    fadeTimer = setInterval(() => {
      left -= 1;
      el.volume = left <= 0 ? clamped : Math.max(0, Math.min(1, el.volume + delta));
      if (left <= 0) {
        cancelFade();
        resolve();
      }
    }, FADE_STEP_MS);
  });
}

export const FADE_IN_MS = 250;
export const FADE_OUT_MS = 160;
