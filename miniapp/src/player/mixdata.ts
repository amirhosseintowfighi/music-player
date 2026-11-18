/**
 * Where AutoMix gets to know a song: from the server when any listener has measured
 * it before, otherwise by measuring it here — the file decoded at 11 kHz mono, the
 * analyser in a Web Worker — and telling the server, so the next listener does not
 * have to. Loaded only when AutoMix is on.
 */
import { put, type Track } from '@/api/client';
import type { MixAnalysis } from '@/player/analysis';
import { offlineUrl, ticketFor } from '@/player/engine';

export const ANALYSIS_RATE = 11025;
/** Bigger than this is a mix, a podcast or a lossless file: not worth decoding here. */
const MAX_BYTES = 25 * 1024 * 1024;
const STORE_KEY = 'tmusic.mix';
const STORE_MAX = 400;

const known = new Map<number, MixAnalysis | null>();
const inflight = new Map<number, Promise<MixAnalysis | null>>();

function stored(): Record<string, MixAnalysis> {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) ?? '{}') as Record<string, MixAnalysis>;
  } catch {
    return {};
  }
}

function remember(id: number, analysis: MixAnalysis): void {
  known.set(id, analysis);
  try {
    const all = stored();
    all[String(id)] = analysis;
    const ids = Object.keys(all);
    for (const old of ids.slice(0, Math.max(0, ids.length - STORE_MAX))) delete all[old];
    localStorage.setItem(STORE_KEY, JSON.stringify(all));
  } catch {
    /* this session only */
  }
}

/** What is known about ``track`` without downloading anything. */
export function knownMix(track: Track): MixAnalysis | null {
  if (track.mix) return track.mix as MixAnalysis;
  if (known.has(track.id)) return known.get(track.id) ?? null;
  const local = stored()[String(track.id)];
  if (local) known.set(track.id, local);
  return local ?? null;
}

let worker: Worker | null = null;
let jobs = 0;
const waiting = new Map<number, { resolve: (a: MixAnalysis) => void; reject: (e: Error) => void }>();

async function runAnalysis(samples: Float32Array): Promise<MixAnalysis> {
  if (typeof Worker === 'undefined') {
    const { analyse } = await import('@/player/analysis');
    return analyse(samples, ANALYSIS_RATE);
  }
  if (!worker) {
    worker = new Worker(new URL('./analysis.worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<{ id: number; result?: MixAnalysis; error?: string }>) => {
      const job = waiting.get(event.data.id);
      waiting.delete(event.data.id);
      if (!job) return;
      if (event.data.result) job.resolve(event.data.result);
      else job.reject(new Error(event.data.error ?? 'analysis failed'));
    };
  }
  jobs += 1;
  const id = jobs;
  return new Promise<MixAnalysis>((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    // Transferred, not copied: a song is a few megabytes of samples.
    worker?.postMessage({ id, samples, sr: ANALYSIS_RATE }, [samples.buffer]);
  });
}

/** Decodes a file to mono at the analysis rate. */
export async function decodeMono(bytes: ArrayBuffer): Promise<Float32Array> {
  const Offline =
    (globalThis as { OfflineAudioContext?: typeof OfflineAudioContext }).OfflineAudioContext ??
    (globalThis as { webkitOfflineAudioContext?: typeof OfflineAudioContext }).webkitOfflineAudioContext;
  if (!Offline) throw new Error('no OfflineAudioContext');
  const ctx = new Offline(1, ANALYSIS_RATE, ANALYSIS_RATE);
  const buffer = await ctx.decodeAudioData(bytes);
  const mono = new Float32Array(buffer.length);
  for (let c = 0; c < buffer.numberOfChannels; c += 1) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < data.length; i += 1) mono[i] = (mono[i] as number) + (data[i] as number) / buffer.numberOfChannels;
  }
  return mono;
}

/**
 * The song's analysis, measuring it here if needed and allowed. Never throws: a
 * song AutoMix could not study is mixed with a plain smart fade instead.
 */
export function requestMix(track: Track, allowDownload: boolean): Promise<MixAnalysis | null> {
  const ready = knownMix(track);
  if (ready) return Promise.resolve(ready);
  if (!allowDownload) return Promise.resolve(null);
  const running = inflight.get(track.id);
  if (running) return running;
  const job = (async () => {
    try {
      const url = (await offlineUrl(track.id)) ?? (await ticketFor(track.id)).url;
      const response = await fetch(url);
      if (!response.ok) return null;
      const size = Number(response.headers.get('Content-Length') ?? 0);
      if (size > MAX_BYTES) return null;
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength > MAX_BYTES) return null;
      const analysis = await runAnalysis(await decodeMono(bytes));
      remember(track.id, analysis);
      void put(`/v1/tracks/${track.id}/mix`, analysis).catch(() => undefined);
      return analysis;
    } catch {
      known.set(track.id, null);
      return null;
    } finally {
      inflight.delete(track.id);
    }
  })();
  inflight.set(track.id, job);
  return job;
}

export function resetMixDataForTests(): void {
  known.clear();
  inflight.clear();
}
