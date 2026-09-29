/**
 * What the app saw on this device, reported home: its boots, crashes and errors.
 *
 * A web view that dies ("Webview crashed") takes its own logs with it, and the
 * listener who saw it rarely knows their GPU or Telegram version. So:
 *
 * - **boot**: sent first thing, with a beacon (it leaves even if the page dies a
 *   moment later): the user agent, Telegram's platform and version;
 * - **crash**: each start leaves a note of how far it got (boot → render → shell →
 *   ready). If the next start finds a note that never reached "ready" and was not
 *   closed normally, the web view died on the way, and the stage says where;
 * - **error**: uncaught script errors and rejections, with the stack, a few per run.
 *
 * Imported before everything else in main.tsx so it runs before any of the heavy
 * modules do. Everything here is best-effort and must never throw.
 */
import { currentAccessToken } from '@/api/client';

type Stage = 'boot' | 'render' | 'shell' | 'ready' | 'closed';

const NOTE = 'tmusic.lastBoot';
const READY_AFTER_MS = 10_000;
const MAX_ERRORS = 8;
const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';
const URL_PATH = `${BASE}/v1/telemetry/client`;

interface Note {
  session: string;
  stage: Stage;
  at: number;
  path: string;
}

interface TelegramInfo {
  platform?: string;
  version?: string;
}

function telegram(): TelegramInfo {
  return (globalThis as { Telegram?: { WebApp?: TelegramInfo } }).Telegram?.WebApp ?? {};
}

function randomId(): string {
  const bytes = new Uint8Array(8);
  try {
    globalThis.crypto.getRandomValues(bytes);
  } catch {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export const session = randomId();
let errorsSent = 0;
let stage: Stage = 'boot';

function readNote(): Note | null {
  try {
    const raw = localStorage.getItem(NOTE);
    return raw ? (JSON.parse(raw) as Note) : null;
  } catch {
    return null;
  }
}

function writeNote(next: Stage): void {
  stage = next;
  try {
    localStorage.setItem(NOTE, JSON.stringify({ session, stage: next, at: Date.now(), path: location.hash }));
  } catch {
    /* no storage: no crash detection, the rest still works */
  }
}

export interface ClientEvent {
  kind: 'boot' | 'crash' | 'error';
  stage?: string;
  message?: string;
  stack?: string;
  session?: string;
}

function payload(event: ClientEvent): string {
  const tg = telegram();
  return JSON.stringify({
    kind: event.kind,
    session: event.session ?? session,
    platform: (tg.platform ?? '').slice(0, 40),
    tg_version: (tg.version ?? '').slice(0, 20),
    ua: navigator.userAgent.slice(0, 400),
    app_version: String(import.meta.env.VITE_APP_VERSION ?? '').slice(0, 40),
    stage: (event.stage ?? stage).slice(0, 40),
    message: (event.message ?? '').slice(0, 1000),
    stack: (event.stack ?? '').slice(0, 4000),
    path: location.hash.slice(0, 200),
  });
}

/** Sends one event. A beacon when it must survive the page; fetch when it can carry a token. */
export function send(event: ClientEvent, beacon = false): void {
  try {
    const body = payload(event);
    const token = currentAccessToken();
    if ((beacon || !token) && typeof navigator.sendBeacon === 'function') {
      // text/plain keeps a cross-origin beacon a "simple" request (no preflight).
      if (navigator.sendBeacon(URL_PATH, new Blob([body], { type: 'text/plain' }))) return;
    }
    void fetch(URL_PATH, {
      method: 'POST',
      body,
      keepalive: true,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).catch(() => undefined);
  } catch {
    /* diagnostics must never become the problem */
  }
}

/** How far this start got; "ready" is written by itself once the app has held up for a while. */
export function markStage(next: Stage): void {
  if (stage === 'ready' || stage === 'closed') return;
  writeNote(next);
  if (next === 'shell') setTimeout(() => writeNote('ready'), READY_AFTER_MS);
}

export function reportError(error: unknown, where = 'window'): void {
  if (errorsSent >= MAX_ERRORS) return;
  errorsSent += 1;
  const err = error instanceof Error ? error : new Error(String(error));
  send({ kind: 'error', message: `${where}: ${err.name}: ${err.message}`, stack: err.stack ?? '' });
}

/** Runs once, at import: the crash check for the previous start, then this boot. */
export function startDiagnostics(): void {
  const previous = readNote();
  if (previous && previous.stage !== 'ready' && previous.stage !== 'closed' && Date.now() - previous.at < 86_400_000) {
    send({
      kind: 'crash',
      stage: previous.stage,
      message: `previous start (${previous.session}) stopped at "${previous.stage}" on ${previous.path || '/'}`,
    }, true);
  }
  writeNote('boot');
  send({ kind: 'boot' }, true);
  window.addEventListener('error', (event) => reportError(event.error ?? event.message, 'error'));
  window.addEventListener('unhandledrejection', (event) => reportError(event.reason, 'promise'));
  // Closing the app normally is not a crash, even in its first seconds.
  window.addEventListener('pagehide', () => {
    if (stage !== 'ready') writeNote('closed');
  });
}

if (typeof window !== 'undefined' && import.meta.env.MODE !== 'test') startDiagnostics();
