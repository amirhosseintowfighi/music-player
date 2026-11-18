/**
 * AutoMix analysis: what a DJ hears before mixing a record in.
 *
 * Given the audio of a whole song (mono, resampled low — 11 kHz is plenty for
 * rhythm and harmony), finds:
 *
 * - the tempo, to a tenth of a BPM: an onset envelope (spectral flux) is
 *   autocorrelated for candidates, then a fine comb search keeps the tempo whose
 *   beat grid lines up with the most onsets over the *whole* song;
 * - the beat grid and the downbeats: the grid's phase, and which of the four
 *   beats carries the most kick drum (the "one" of each bar);
 * - the key, as a Camelot position: a chromagram correlated with the
 *   Krumhansl–Kessler major and minor profiles;
 * - the sections: where the intro gives way to the body of the song, and where the
 *   outro starts — from the energy of each bar, snapped to bars.
 *
 * Pure functions, no DOM: it runs in a Web Worker (analysis.worker.ts) so the music
 * never stutters while a song is being studied, and in tests on synthetic audio.
 */

export const ANALYSER_VERSION = 1;

export interface MixAnalysis {
  v: number;
  bpm: number;
  /** First beat, in seconds from the start of the file. */
  beat: number;
  /** First beat of a bar (the "one"). */
  downbeat: number;
  /** Camelot position: 0–11 = 1A–12A (minor), 12–23 = 1B–12B (major). */
  key: number | null;
  /** Where the intro ends and the outro starts, in seconds. */
  intro: number;
  outro: number;
  /** How regular the beat is, 0–1: below ~0.25 there is no grid worth trusting. */
  confidence: number;
}

// ── FFT ───────────────────────────────────────────────────────────────────────

/** In-place radix-2 FFT. ``re.length`` must be a power of two. */
export function fft(re: Float64Array, im: Float64Array): void {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i += 1) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j] as number, re[i] as number];
      [im[i], im[j]] = [im[j] as number, im[i] as number];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = (-2 * Math.PI) / len;
    const wr = Math.cos(angle);
    const wi = Math.sin(angle);
    for (let i = 0; i < n; i += len) {
      let cr = 1;
      let ci = 0;
      for (let k = 0; k < len / 2; k += 1) {
        const a = i + k;
        const b = a + len / 2;
        const tr = (re[b] as number) * cr - (im[b] as number) * ci;
        const ti = (re[b] as number) * ci + (im[b] as number) * cr;
        re[b] = (re[a] as number) - tr;
        im[b] = (im[a] as number) - ti;
        re[a] = (re[a] as number) + tr;
        im[a] = (im[a] as number) + ti;
        const next = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = next;
      }
    }
  }
}

function hann(n: number): Float64Array {
  const w = new Float64Array(n);
  for (let i = 0; i < n; i += 1) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));
  return w;
}

// ── onsets ────────────────────────────────────────────────────────────────────

export const FRAME = 1024;
export const HOP = 256;

export interface Onsets {
  /** Spectral flux per frame: where something new starts sounding. */
  all: Float64Array;
  /** The same below ~150 Hz: the kick drum and the bass. */
  low: Float64Array;
  /** Frames per second. */
  fps: number;
  /** Seconds from the start of the file to the centre of frame 0. */
  offset: number;
  /**
   * How hard the strongest onsets hit, relative to everything sounding at the time
   * (the 98th percentile of flux over the mean log spectrum). Drums: large. A pad,
   * speech, a held chord: small. Independent of how loud the file is.
   */
  attack: number;
}

/** Subtracts a moving average and keeps the positive part: peaks, not loudness. */
function whiten(x: Float64Array, radius: number): Float64Array {
  const out = new Float64Array(x.length);
  let sum = 0;
  let count = 0;
  let lo = 0;
  let hi = 0;
  for (let i = 0; i < x.length; i += 1) {
    while (hi < x.length && hi <= i + radius) sum += x[hi++] as number;
    while (lo < i - radius) sum -= x[lo++] as number;
    count = hi - lo;
    out[i] = Math.max(0, (x[i] as number) - sum / count);
  }
  return out;
}

export function onsets(samples: Float32Array, sr: number): Onsets {
  const frames = Math.max(0, Math.floor((samples.length - FRAME) / HOP) + 1);
  const window = hann(FRAME);
  const re = new Float64Array(FRAME);
  const im = new Float64Array(FRAME);
  const bins = FRAME / 2;
  const lowBins = Math.max(2, Math.round((150 * FRAME) / sr));
  let level = 0;
  let previous = new Float64Array(bins);
  let current = new Float64Array(bins);
  const all = new Float64Array(frames);
  const low = new Float64Array(frames);
  for (let f = 0; f < frames; f += 1) {
    const start = f * HOP;
    for (let i = 0; i < FRAME; i += 1) {
      re[i] = (samples[start + i] ?? 0) * (window[i] as number);
      im[i] = 0;
    }
    fft(re, im);
    let flux = 0;
    let lowFlux = 0;
    for (let k = 1; k < bins; k += 1) {
      // Log compression: a quiet hi-hat counts, a loud chord does not swamp it.
      const mag = Math.log1p(100 * Math.hypot(re[k] as number, im[k] as number));
      current[k] = mag;
      level += mag;
      const rise = mag - (previous[k] as number);
      if (rise > 0) {
        flux += rise;
        if (k <= lowBins) lowFlux += rise;
      }
    }
    all[f] = f === 0 ? 0 : flux;
    low[f] = f === 0 ? 0 : lowFlux;
    [previous, current] = [current, previous];
  }
  const fps = sr / HOP;
  const radius = Math.round(fps * 0.25);
  const sorted = Array.from(all).sort((a, b) => a - b);
  const p98 = sorted[Math.floor(sorted.length * 0.98)] ?? 0;
  const attack = level > 0 ? p98 / (level / Math.max(1, frames)) : 0;
  // A hit shows up as flux once it is three quarters into the window, not at its
  // centre (measured on kicks: half a frame put every beat ~25 ms early).
  return { all: whiten(all, radius), low: whiten(low, radius), fps, offset: (FRAME * 0.75) / sr, attack };
}

/** ``x`` at a fractional index, linearly interpolated (0 outside). */
function at(x: Float64Array, i: number): number {
  const a = Math.floor(i);
  if (a < 0 || a + 1 >= x.length) return a >= 0 && a < x.length ? (x[a] as number) : 0;
  const t = i - a;
  return (x[a] as number) * (1 - t) + (x[a + 1] as number) * t;
}

// ── tempo and beats ───────────────────────────────────────────────────────────

const MIN_BPM = 60;
const MAX_BPM = 200;

/** How strongly a beat grid of ``period`` frames lines up with the onsets, at its best phase. */
function gridScore(o: Float64Array, period: number): { score: number; phase: number; mean: number } {
  const steps = Math.max(8, Math.ceil(period));
  let best = -1;
  let bestPhase = 0;
  let total = 0;
  for (let s = 0; s < steps; s += 1) {
    const phase = (s * period) / steps;
    let sum = 0;
    let n = 0;
    for (let i = phase; i < o.length; i += period) {
      // A beat is a moment: take the strongest frame within ±1 of it.
      sum += Math.max(at(o, i - 1), at(o, i), at(o, i + 1));
      n += 1;
    }
    const score = n ? sum / n : 0;
    total += score;
    if (score > best) {
      best = score;
      bestPhase = phase;
    }
  }
  return { score: best, phase: bestPhase, mean: total / steps };
}

/** The onset envelope rhythm is read from: everything, with the kick and bass weighted up. */
export function rhythm(on: Onsets): Float64Array {
  const max = (x: Float64Array) => x.reduce((m, v) => Math.max(m, v), 0) || 1;
  const a = max(on.all);
  const b = max(on.low);
  return on.all.map((v, i) => v / a + (1.5 * (on.low[i] as number)) / b);
}

/** The highest point near ``i`` (±``radius`` frames), with sub-frame precision. */
function peakNear(o: Float64Array, i: number, radius: number): { at: number; height: number } {
  const lo = Math.max(1, Math.floor(i - radius));
  const hi = Math.min(o.length - 2, Math.ceil(i + radius));
  let best = lo;
  for (let k = lo; k <= hi; k += 1) if ((o[k] as number) > (o[best] as number)) best = k;
  const y0 = o[best - 1] as number;
  const y1 = o[best] as number;
  const y2 = o[best + 1] as number;
  const denom = y0 - 2 * y1 + y2;
  const shift = denom < 0 ? Math.max(-0.5, Math.min(0.5, (0.5 * (y0 - y2)) / denom)) : 0;
  return { at: best + shift, height: y1 };
}

/**
 * Fits the grid to the music itself: around each predicted beat, the real onset
 * peak, then a least-squares line through all of them. Over a whole song that
 * pins the period to a hundredth of a BPM — what keeps a long blend from drifting.
 */
function fitGrid(o: Float64Array, period: number, phase: number): { period: number; phase: number } {
  const points: [number, number][] = [];
  const heights: number[] = [];
  for (let k = 0; phase + k * period < o.length - 2; k += 1) {
    const peak = peakNear(o, phase + k * period, period / 6);
    points.push([k, peak.at]);
    heights.push(peak.height);
  }
  if (points.length < 8) return { period, phase };
  // Only beats that actually sound: a silent bar would pull the line anywhere.
  const floor = [...heights].sort((a, b) => a - b)[Math.floor(heights.length * 0.4)] as number;
  let fitted = { period, phase };
  for (let round = 0; round < 2; round += 1) {
    let n = 0;
    let sk = 0;
    let st = 0;
    let skk = 0;
    let skt = 0;
    points.forEach(([k, t], index) => {
      if ((heights[index] as number) < floor) return;
      // Second round: drop beats far from the first line (fills, swing).
      if (round > 0 && Math.abs(t - (fitted.phase + k * fitted.period)) > period / 10) return;
      n += 1;
      sk += k;
      st += t;
      skk += k * k;
      skt += k * t;
    });
    const denom = n * skk - sk * sk;
    if (n < 8 || denom === 0) break;
    const slope = (n * skt - sk * st) / denom;
    fitted = { period: slope, phase: (st - slope * sk) / n };
  }
  // A fit that wandered off is no fit.
  return Math.abs(fitted.period / period - 1) < 0.03 ? fitted : { period, phase };
}

/**
 * The tempo: autocorrelation picks the region, a prior pulls towards what music is
 * usually written at (DJs think in 85–175), a comb search over the whole song picks
 * the candidate, and the grid fit settles it.
 */
export function tempo(o: Float64Array, fps: number): { bpm: number; confidence: number; phase: number } {
  const n = o.length;
  // Smoothed for the coarse search: with spiky onsets a lag a fraction of a frame
  // off misses every peak, and the right tempo would never be a candidate.
  const smooth = o.map((v, i) => 0.25 * (o[i - 1] ?? v) + 0.5 * v + 0.25 * (o[i + 1] ?? v));
  const scores: { bpm: number; score: number }[] = [];
  for (let bpm = MIN_BPM; bpm <= MAX_BPM; bpm += 0.5) {
    const lag = (fps * 60) / bpm;
    let ac = 0;
    let count = 0;
    for (let i = 0; i + lag < n; i += 1) {
      ac += (smooth[i] as number) * at(smooth, i + lag);
      count += 1;
    }
    ac = count ? ac / count : 0;
    // Log-Gaussian prior around 122 BPM, an octave wide (Ellis 2007).
    const prior = Math.exp(-0.5 * (Math.log2(bpm / 122) / 0.9) ** 2);
    scores.push({ bpm, score: ac * prior });
  }
  // Distinct peaks only: four neighbouring bins of one peak are one candidate.
  const peaks = scores.filter((c, i) => c.score >= (scores[i - 1]?.score ?? -1) && c.score >= (scores[i + 1]?.score ?? -1));
  peaks.sort((a, b) => b.score - a.score);
  // Each peak and its relatives: half, double and 3:2 time are where tempo
  // detectors go wrong, so every one of them gets a fair hearing on the whole song.
  const direct = new Set<number>(peaks.slice(0, 5).map((p) => p.bpm));
  const relatives = new Set<number>();
  for (const bpm0 of direct) {
    for (const factor of [2, 0.5, 1.5, 2 / 3]) {
      const bpm = Math.round(bpm0 * factor * 2) / 2;
      if (bpm >= MIN_BPM && bpm <= MAX_BPM && !direct.has(bpm)) relatives.add(bpm);
    }
  }
  // Beats are judged present or absent, not loud or soft: saturating the envelope
  // at the level of a typical strong hit stops a grid on just the accented "one"s
  // (half time) from beating the grid on every beat.
  const heights: number[] = [];
  for (let i = 1; i < n - 1; i += 1) {
    const v = o[i] as number;
    if (v > 0 && v >= (o[i - 1] as number) && v >= (o[i + 1] as number)) heights.push(v);
  }
  heights.sort((a, b) => a - b);
  const strong = heights[Math.floor(heights.length * 0.95)] ?? 1;
  const sat = o.map((v) => Math.min(1, v / strong));
  const refine = (centre: number) => {
    let found = { bpm: centre, score: -1, confidence: 0, phase: 0, raw: 0 };
    for (let bpm = centre - 1.5; bpm <= centre + 1.5; bpm += 0.05) {
      const grid = gridScore(sat, (fps * 60) / bpm);
      const prior = Math.exp(-0.5 * (Math.log2(bpm / 122) / 0.9) ** 2);
      const score = grid.score * (0.6 + 0.4 * prior);
      if (score > found.score) {
        const confidence = grid.score > 0 ? Math.max(0, Math.min(1, (grid.score - grid.mean) / grid.score)) : 0;
        found = { bpm, score, confidence, phase: grid.phase, raw: grid.score };
      }
    }
    return found;
  };
  let best = { bpm: 120, score: -1, confidence: 0, phase: 0, raw: 0 };
  for (const candidate of direct) {
    const found = refine(candidate);
    if (found.score > best.score) best = found;
  }
  // A relative has to be clearly better to overrule what the periodicity said.
  const base = best.score;
  for (const candidate of relatives) {
    const found = refine(candidate);
    if (found.score > base * 1.1 && found.score > best.score) best = found;
  }
  // Twice as fast and just as full (every in-between point is a real beat too):
  // that is the pulse. Below the range DJs count in (78), give it more benefit.
  while (best.bpm * 2 <= MAX_BPM) {
    const double = refine(best.bpm * 2);
    if (double.raw < best.raw * (best.bpm < 78 ? 0.6 : 0.88)) break;
    best = double;
  }
  if (best.bpm > 185) {
    const half = refine(best.bpm / 2);
    if (half.raw >= best.raw * 0.9) best = half;
  }
  // Progressively: the first stretch of beats sets the line, each wider window
  // corrects it, so an early error never compounds into a grid off by a beat.
  // Pulse clarity: how much of the onset energy repeats at the beat period. A pad
  // or a speech track has onsets too, but they do not come back on a grid.
  let energy = 0;
  for (const v of smooth) energy += v * v;
  const lag = (fps * 60) / best.bpm;
  let repeat = 0;
  for (let i = 0; i + lag < n; i += 1) repeat += (smooth[i] as number) * at(smooth, i + lag);
  const clarity = energy > 0 ? Math.max(0, Math.min(1, (repeat / energy) * 1.6)) : 0;
  best.confidence = Math.min(best.confidence, clarity);
  let fit = { period: (fps * 60) / best.bpm, phase: best.phase };
  for (const span of [0.25, 0.5, 1]) {
    fit = fitGrid(o.subarray(0, Math.max(Math.round(fit.period * 12), Math.round(o.length * span))), fit.period, fit.phase);
  }
  const bpm = (fps * 60) / fit.period;
  return {
    bpm: Math.round(bpm * 100) / 100,
    confidence: Math.round(best.confidence * 100) / 100,
    phase: ((fit.phase % fit.period) + fit.period) % fit.period,
  };
}

/** The beat grid's first beat, on the kick, and which beat of the bar is the "one". */
export function beats(on: Onsets, bpm: number, phase: number): { beat: number; downbeat: number } {
  const period = (on.fps * 60) / bpm;
  const kickAt = (start: number, step: number) => {
    let sum = 0;
    for (let i = start; i < on.low.length; i += step) sum += Math.max(at(on.low, i - 1), at(on.low, i), at(on.low, i + 1));
    return sum;
  };
  // A grid on the hi-hats is half a beat off: the beat is where the kick is.
  let first = phase;
  if (kickAt((phase + period / 2) % period, period) > kickAt(phase, period) * 1.15) first = (phase + period / 2) % period;
  // Of the four beats in a bar, the one the kick lands on hardest is the downbeat.
  let bestShift = 0;
  let bestKick = -1;
  for (let shift = 0; shift < 4; shift += 1) {
    const kick = kickAt(first + shift * period, 4 * period);
    if (kick > bestKick) {
      bestKick = kick;
      bestShift = shift;
    }
  }
  const beat = on.offset + first / on.fps;
  return { beat: round3(beat), downbeat: round3(beat + (bestShift * 60) / bpm) };
}

function round3(x: number): number {
  return Math.round(x * 1000) / 1000;
}

// ── key ───────────────────────────────────────────────────────────────────────

// Krumhansl–Kessler key profiles, tonic first.
const MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const MINOR = [6.33, 2.68, 3.52, 5.38, 2.6, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];

/** Camelot index for a key: ``pc`` is the tonic's pitch class (C = 0). */
export function camelot(pc: number, major: boolean): number {
  // Relative majors share a number: A minor (8A) sits with C major (8B).
  const majorPc = major ? pc : (pc + 3) % 12;
  const number = (((majorPc * 7) % 12) + 7) % 12; // 0-based: C major → 7 → "8B"
  return number + (major ? 12 : 0);
}

/** "8A", "11B"… */
export function camelotName(key: number | null): string {
  if (key === null) return '';
  return `${(key % 12) + 1}${key >= 12 ? 'B' : 'A'}`;
}

/** Steps around the Camelot wheel: 0 same key, 1 a perfect neighbour (harmonic). */
export function camelotDistance(a: number | null, b: number | null): number {
  if (a === null || b === null) return 3;
  const na = a % 12;
  const nb = b % 12;
  const around = Math.min((na - nb + 12) % 12, (nb - na + 12) % 12);
  const sameMode = a >= 12 === b >= 12;
  return around + (sameMode ? 0 : 1);
}

function correlate(a: number[], b: number[]): number {
  const n = a.length;
  const ma = a.reduce((s, v) => s + v, 0) / n;
  const mb = b.reduce((s, v) => s + v, 0) / n;
  let num = 0;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i += 1) {
    const x = (a[i] as number) - ma;
    const y = (b[i] as number) - mb;
    num += x * y;
    da += x * x;
    db += y * y;
  }
  return da && db ? num / Math.sqrt(da * db) : 0;
}

export function key(samples: Float32Array, sr: number): number | null {
  const size = 4096;
  const window = hann(size);
  const re = new Float64Array(size);
  const im = new Float64Array(size);
  const chroma = new Array<number>(12).fill(0);
  const loBin = Math.ceil((65 * size) / sr);
  const hiBin = Math.min(size / 2 - 1, Math.floor((2000 * size) / sr));
  for (let start = 0; start + size <= samples.length; start += size) {
    for (let i = 0; i < size; i += 1) {
      re[i] = (samples[start + i] ?? 0) * (window[i] as number);
      im[i] = 0;
    }
    fft(re, im);
    for (let k = loBin; k <= hiBin; k += 1) {
      const freq = (k * sr) / size;
      const midi = 69 + 12 * Math.log2(freq / 440);
      // Only bins close to a semitone: between two notes says nothing about either.
      if (Math.abs(midi - Math.round(midi)) > 0.3) continue;
      const pc = ((Math.round(midi) % 12) + 12) % 12;
      chroma[pc] = (chroma[pc] as number) + Math.hypot(re[k] as number, im[k] as number);
    }
  }
  if (chroma.every((v) => v === 0)) return null;
  let best = { r: -2, pc: 0, major: true };
  for (let pc = 0; pc < 12; pc += 1) {
    const rotated = chroma.slice(pc).concat(chroma.slice(0, pc));
    for (const [profile, major] of [
      [MAJOR, true],
      [MINOR, false],
    ] as const) {
      const r = correlate(rotated, profile);
      if (r > best.r) best = { r, pc, major };
    }
  }
  // A weak fit is no key at all: better "unknown" than a wrong clash avoided.
  return best.r < 0.45 ? null : camelot(best.pc, best.major);
}

// ── sections ──────────────────────────────────────────────────────────────────

/**
 * Intro and outro from the energy of each bar: the intro ends at the first bar
 * that reaches the body of the song, the outro starts after the last one that does.
 */
export function sections(
  samples: Float32Array,
  sr: number,
  bpm: number,
  downbeat: number,
): { intro: number; outro: number } {
  const duration = samples.length / sr;
  const bar = (4 * 60) / bpm;
  const energies: { start: number; rms: number }[] = [];
  for (let start = downbeat; start + bar <= duration; start += bar) {
    const a = Math.floor(start * sr);
    const b = Math.floor((start + bar) * sr);
    let sum = 0;
    for (let i = a; i < b; i += 4) sum += (samples[i] as number) ** 2;
    energies.push({ start, rms: Math.sqrt(sum / Math.max(1, (b - a) / 4)) });
  }
  if (energies.length < 4) return { intro: round3(downbeat), outro: round3(duration) };
  const sorted = energies.map((e) => e.rms).sort((x, y) => x - y);
  const body = sorted[Math.floor(sorted.length * 0.8)] as number;
  const threshold = body * 0.7;
  const first = energies.findIndex((e) => e.rms >= threshold);
  let last = energies.length - 1;
  while (last > 0 && (energies[last] as { rms: number }).rms < threshold) last -= 1;
  const intro = Math.min(duration / 2, (energies[Math.max(0, first)] as { start: number }).start);
  const outroBar = energies[last + 1];
  const outro = Math.max(duration / 2, outroBar ? outroBar.start : duration);
  return { intro: round3(intro), outro: round3(Math.max(intro, outro)) };
}

// ── all of it ─────────────────────────────────────────────────────────────────

export function analyse(samples: Float32Array, sr: number): MixAnalysis {
  const on = onsets(samples, sr);
  const t = tempo(rhythm(on), on.fps);
  const b = beats(on, t.bpm, t.phase);
  const s = sections(samples, sr, t.bpm, b.downbeat);
  return {
    v: ANALYSER_VERSION,
    bpm: t.bpm,
    beat: b.beat,
    downbeat: b.downbeat,
    key: key(samples, sr),
    intro: s.intro,
    outro: s.outro,
    // No real attacks (a pad, speech, a held chord): no grid to trust, whatever the
    // autocorrelation found in the noise.
    confidence: Math.round(Math.min(t.confidence, Math.max(0, Math.min(1, (on.attack - 0.1) / 0.4))) * 100) / 100,
  };
}
