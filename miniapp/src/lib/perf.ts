/**
 * Performance guard for the glass material.
 *
 * backdrop-filter is expensive on mid-range Android. We start from the device hints
 * and then watch real frame times: two seconds under ~45 fps and the app switches to
 * solid panels (ARCHITECTURE §7). The user can force either mode in settings.
 */

export type PerfMode = 'auto' | 'high' | 'low';

const STORAGE_KEY = 'tmusic.perf';
const LOW_FPS = 45;
const SAMPLE_MS = 2000;

export function deviceLooksSlow(): boolean {
  const nav = navigator as Navigator & { deviceMemory?: number; hardwareConcurrency?: number };
  if (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 4) return true;
  if (typeof nav.hardwareConcurrency === 'number' && nav.hardwareConcurrency <= 4) return true;
  return matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function readPerfPreference(): PerfMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'high' || stored === 'low' ? stored : 'auto';
  } catch {
    return 'auto';
  }
}

export function savePerfPreference(mode: PerfMode): void {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
}

export function applyPerf(low: boolean): void {
  document.documentElement.dataset.perf = low ? 'low' : 'high';
}

/**
 * Watches frame rate and calls back once when it stays low.
 * Returns a stop function.
 */
export function watchFrameRate(onSlow: () => void): () => void {
  let frames = 0;
  let start = performance.now();
  let raf = 0;
  let stopped = false;

  const tick = (now: number) => {
    frames += 1;
    if (now - start >= SAMPLE_MS) {
      const fps = (frames * 1000) / (now - start);
      if (fps < LOW_FPS) {
        onSlow();
        stopped = true;
        return;
      }
      frames = 0;
      start = now;
    }
    if (!stopped) raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
  return () => {
    stopped = true;
    cancelAnimationFrame(raf);
  };
}
