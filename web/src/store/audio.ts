/**
 * How the music sounds: crossfade, gapless, volume normalisation and the equaliser.
 *
 * Kept on the device (like Music's own playback settings), because what sounds right
 * depends on the headphones in this listener's ears, not on their account.
 */
import { create } from 'zustand';

import { put, type Track } from '@/api/client';
import { fragileWebView } from '@/lib/perf';
import { onElement, setSaverCheck } from '@/player/engine';

export type EqPreset = 'flat' | 'bass' | 'treble' | 'vocal' | 'acoustic' | 'electronic' | 'night';

/** Gains in dB for the five bands (60 Hz, 230 Hz, 910 Hz, 3.6 kHz, 14 kHz). */
export const EQ_PRESETS: Record<EqPreset, readonly [number, number, number, number, number]> = {
  flat: [0, 0, 0, 0, 0],
  bass: [6, 4, 0, 0, 0],
  treble: [0, 0, 0, 3, 6],
  vocal: [-2, -1, 3, 4, 1],
  acoustic: [3, 2, 1, 2, 3],
  electronic: [5, 2, -1, 2, 4],
  // Late night: keep the low end and the peaks down so nothing jumps out.
  night: [-3, -1, 1, 1, -2],
};
const BANDS = [60, 230, 910, 3600, 14000] as const;

/** Data saver: never, only on mobile data, or always. */
export type SaverMode = 'off' | 'cellular' | 'always';

export interface AudioSettings {
  /** Seconds the end of one track overlaps the start of the next (0 = off). */
  crossfade: number;
  gapless: boolean;
  normalize: boolean;
  eq: EqPreset;
  /** Animated artwork behind the full player (Canvas). */
  canvas: boolean;
  saver: SaverMode;
  /** AutoMix: songs mixed into each other like a DJ would (player/automix). */
  automix: boolean;
}

const KEY = 'tmusic.audio';
const DEFAULTS: AudioSettings = {
  crossfade: 0,
  gapless: true,
  normalize: false,
  eq: 'flat',
  canvas: true,
  saver: 'cellular',
  automix: false,
};

function read(): AudioSettings {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<AudioSettings>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

interface AudioStore extends AudioSettings {
  set: (patch: Partial<AudioSettings>) => void;
}

export const useAudioSettings = create<AudioStore>((set, get) => ({
  ...read(),
  set(patch) {
    set(patch);
    const { crossfade, gapless, normalize, eq, canvas, saver, automix } = get();
    try {
      localStorage.setItem(KEY, JSON.stringify({ crossfade, gapless, normalize, eq, canvas, saver, automix }));
    } catch {
      /* settings for this session only */
    }
    applyEffects();
  },
}));

// ── data saver ────────────────────────────────────────────────────────────────

interface NetworkInfo {
  type?: string;
  saveData?: boolean;
  effectiveType?: string;
}

/** Mobile data, a slow link, or the browser's own "save data" switch. */
export function onMeteredNetwork(): boolean {
  const connection = (navigator as Navigator & { connection?: NetworkInfo }).connection;
  if (!connection) return false;
  return (
    connection.saveData === true ||
    connection.type === 'cellular' ||
    connection.effectiveType === 'slow-2g' ||
    connection.effectiveType === '2g' ||
    connection.effectiveType === '3g'
  );
}

/**
 * Whether to spend as little data as possible right now: the smallest copy of each
 * song (the server picks it), no warming up of the next one, no moving artwork.
 */
export function saverActive(): boolean {
  const { saver } = useAudioSettings.getState();
  return saver === 'always' || (saver === 'cellular' && onMeteredNetwork());
}

setSaverCheck(saverActive);

// ── the effects chain ─────────────────────────────────────────────────────────
//
// Built only once somebody turns an effect on: routing an element through Web Audio
// cannot be undone, and on some phones a suspended AudioContext stops playback in
// the background. Turning everything off again leaves the chain in place but neutral.

/**
 * One deck's channel strip, as on a DJ mixer: a three-band EQ, a sweepable filter,
 * the fader, and an echo send. Neutral unless AutoMix is moving it.
 */
export interface MixNodes {
  low: BiquadFilterNode;
  mid: BiquadFilterNode;
  high: BiquadFilterNode;
  filter: BiquadFilterNode;
  fader: GainNode;
  send: GainNode;
  delay: DelayNode;
  feedback: GainNode;
}

interface Routed {
  /** This element's own level: what evens one song out against the next. */
  gain: GainNode;
  /** Taps the song before any processing, to measure how loud it is. */
  analyser: AnalyserNode;
  mix: MixNodes;
}

/** Puts a channel strip back to flat: full fader, no EQ, no filter, no echo. */
export function neutral(nodes: MixNodes, ctx: BaseAudioContext): void {
  const now = ctx.currentTime;
  for (const param of [
    nodes.low.gain,
    nodes.mid.gain,
    nodes.high.gain,
    nodes.filter.frequency,
    nodes.fader.gain,
    nodes.send.gain,
  ]) {
    param.cancelScheduledValues(now);
  }
  nodes.low.gain.setValueAtTime(0, now);
  nodes.mid.gain.setValueAtTime(0, now);
  nodes.high.gain.setValueAtTime(0, now);
  nodes.filter.type = 'highpass';
  nodes.filter.frequency.setValueAtTime(10, now);
  nodes.filter.Q.value = 0.7;
  nodes.fader.gain.setValueAtTime(1, now);
  nodes.send.gain.setValueAtTime(0, now);
}

function strip(ctx: AudioContext, out: AudioNode): { input: AudioNode; nodes: MixNodes } {
  const low = ctx.createBiquadFilter();
  low.type = 'lowshelf';
  low.frequency.value = 180;
  const mid = ctx.createBiquadFilter();
  mid.type = 'peaking';
  mid.frequency.value = 1000;
  mid.Q.value = 0.8;
  const high = ctx.createBiquadFilter();
  high.type = 'highshelf';
  high.frequency.value = 5000;
  const filter = ctx.createBiquadFilter();
  const fader = ctx.createGain();
  // Echo: a send off the filter (before the fader, so a song can echo out after its
  // fader is down), a delay that feeds back into itself, darkened on each repeat.
  const send = ctx.createGain();
  const delay = ctx.createDelay(2);
  const feedback = ctx.createGain();
  const tone = ctx.createBiquadFilter();
  tone.type = 'lowpass';
  tone.frequency.value = 3200;
  low.connect(mid);
  mid.connect(high);
  high.connect(filter);
  filter.connect(fader);
  fader.connect(out);
  filter.connect(send);
  send.connect(delay);
  delay.connect(tone);
  tone.connect(feedback);
  feedback.connect(delay);
  tone.connect(out);
  const nodes: MixNodes = { low, mid, high, filter, fader, send, delay, feedback };
  neutral(nodes, ctx);
  return { input: low, nodes };
}

interface Chain {
  ctx: AudioContext;
  input: AudioNode;
  filters: BiquadFilterNode[];
  compressor: DynamicsCompressorNode;
  output: AudioNode;
  connected: WeakMap<HTMLAudioElement, Routed>;
}

/** Linear to 0.8, then a smooth knee that never reaches 1. */
export function softClipCurve(points = 2048): Float32Array<ArrayBuffer> {
  const curve = new Float32Array(new ArrayBuffer(points * 4));
  const knee = 0.8;
  for (let i = 0; i < points; i += 1) {
    const x = (i / (points - 1)) * 2 - 1;
    const a = Math.abs(x);
    const y = a <= knee ? a : knee + (1 - knee) * Math.tanh((a - knee) / (1 - knee)) * 0.95;
    curve[i] = Math.sign(x) * y;
  }
  return curve;
}

let chain: Chain | null = null;

function wanted(): boolean {
  // WebKitGTK (Telegram Desktop on Linux) crashes routing a media element through Web
  // Audio: there the music plays unprocessed rather than not at all.
  if (fragileWebView()) return false;
  const { normalize, eq, automix } = useAudioSettings.getState();
  return normalize || eq !== 'flat' || automix;
}

function build(): Chain | null {
  const Ctx =
    (globalThis as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext })
      .AudioContext ??
    (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctx) return null;
  try {
    const ctx = new Ctx();
    const filters = BANDS.map((frequency, index) => {
      const filter = ctx.createBiquadFilter();
      filter.type = index === 0 ? 'lowshelf' : index === BANDS.length - 1 ? 'highshelf' : 'peaking';
      filter.frequency.value = frequency;
      filter.Q.value = 1;
      return filter;
    });
    const compressor = ctx.createDynamicsCompressor();
    for (let i = 0; i < filters.length - 1; i += 1) filters[i]?.connect(filters[i + 1] as AudioNode);
    filters[filters.length - 1]?.connect(compressor);
    // The last stage: a soft clipper. The compressor adds make-up gain and lets the
    // first milliseconds of a kick through, so on its own it is no limiter; this
    // keeps every sample under full scale, transparently below 0.8.
    const clipper = ctx.createWaveShaper();
    clipper.curve = softClipCurve();
    clipper.oversample = '2x';
    compressor.connect(clipper);
    clipper.connect(ctx.destination);
    const built: Chain = {
      ctx,
      input: filters[0] as AudioNode,
      filters,
      compressor,
      output: clipper,
      connected: new WeakMap(),
    };
    onElement((el) => {
      if (built.connected.has(el)) return;
      try {
        const source = ctx.createMediaElementSource(el);
        const gain = ctx.createGain();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 2048;
        const deck = strip(ctx, built.input);
        source.connect(gain);
        gain.connect(deck.input);
        source.connect(analyser);
        built.connected.set(el, { gain, analyser, mix: deck.nodes });
      } catch {
        /* already routed, or the browser refuses: it simply plays unprocessed */
      }
    });
    return built;
  } catch {
    return null;
  }
}

export function applyEffects(): void {
  if (!chain && wanted()) chain = build();
  if (!chain) return;
  const { normalize, eq } = useAudioSettings.getState();
  const gains = EQ_PRESETS[eq];
  chain.filters.forEach((filter, index) => {
    filter.gain.value = gains[index] ?? 0;
  });
  // Normalising here means evening out loud and quiet tracks as they play: a gentle
  // compressor with make-up headroom, not a per-track loudness scan we cannot do.
  const c = chain.compressor;
  if (normalize) {
    c.threshold.value = -24;
    c.knee.value = 30;
    c.ratio.value = 4;
    c.attack.value = 0.01;
    c.release.value = 0.3;
  } else if (useAudioSettings.getState().automix) {
    // Two songs overlap in every mix: a limiter keeps the sum from ever clipping.
    c.threshold.value = -3;
    c.knee.value = 3;
    c.ratio.value = 20;
    c.attack.value = 0.003;
    c.release.value = 0.15;
  } else {
    c.threshold.value = 0;
    c.knee.value = 0;
    c.ratio.value = 1;
  }
}

/** The end of the chain (after the limiter), to listen in on what plays: diagnostics. */
export function masterOutput(): AudioNode | null {
  return chain?.output ?? null;
}

/** The audio graph's clock and an element's channel strip, for AutoMix. */
export function mixDeck(el: HTMLAudioElement): { ctx: AudioContext; nodes: MixNodes } | null {
  const routed = chain?.connected.get(el);
  return routed && chain ? { ctx: chain.ctx, nodes: routed.mix } : null;
}

// ── per-song levelling ────────────────────────────────────────────────────────
//
// Every song is measured by the first few listeners who play it with levelling on
// (the server never touches audio). Once a level is known, every later listener
// gets that song turned towards one common level from its first second; until
// then the compressor above evens it out as it plays.

/** Where every song is turned towards: the RMS level of typical modern masters. */
export const TARGET_DB = -16;
const MAX_CUT_DB = -9;
const MAX_BOOST_DB = 6;
/** Seconds of sound to measure before reporting; the start alone is not the song. */
export const MEASURE_SECONDS = 40;
const SILENCE_DB = -60;

export function gainFor(loudness: number | null | undefined): number {
  if (loudness === null || loudness === undefined) return 0;
  return Math.max(MAX_CUT_DB, Math.min(MAX_BOOST_DB, TARGET_DB - loudness));
}

/** RMS level of one block of samples, in dBFS. */
export function levelDb(samples: Float32Array): number {
  let power = 0;
  for (const v of samples) power += v * v;
  const mean = power / Math.max(1, samples.length);
  return mean > 0 ? 10 * Math.log10(mean) : -Infinity;
}

let measuring: { timer: ReturnType<typeof setInterval>; trackId: number } | null = null;

function stopMeasuring(): void {
  if (measuring) clearInterval(measuring.timer);
  measuring = null;
}

/**
 * Called by the player when ``track`` starts on ``el``: sets that element's gain
 * from the song's known level, or measures the song when nobody has yet.
 */
export function levelTrack(el: HTMLAudioElement, track: Track): void {
  stopMeasuring();
  const routed = chain?.connected.get(el);
  if (!routed || !chain) return;
  const { normalize } = useAudioSettings.getState();
  const db = normalize ? gainFor(track.loudness) : 0;
  routed.gain.gain.value = 10 ** (db / 20);
  if (!normalize || (track.loudness !== null && track.loudness !== undefined)) return;
  const samples = new Float32Array(routed.analyser.fftSize);
  let power = 0;
  let seconds = 0;
  const timer = setInterval(() => {
    // Only real listening counts: not paused, not mid-fade, not silence.
    if (el.paused || el.volume < 0.95) return;
    routed.analyser.getFloatTimeDomainData(samples);
    const level = levelDb(samples);
    if (level < SILENCE_DB) return;
    power += 10 ** (level / 10);
    seconds += 1;
    if (seconds < MEASURE_SECONDS) return;
    stopMeasuring();
    const measured = Math.round(10 * Math.log10(power / seconds) * 10) / 10;
    void put(`/v1/tracks/${track.id}/loudness`, { db: measured, seconds }).catch(() => undefined);
  }, 1000);
  measuring = { timer, trackId: track.id };
}

/** Browsers start an AudioContext suspended until a gesture; every play is one. */
export function resumeEffects(): void {
  if (chain?.ctx.state === 'suspended') void chain.ctx.resume().catch(() => undefined);
}
