/**
 * How the music sounds: crossfade, gapless, volume normalisation and the equaliser.
 *
 * Kept on the device (like Music's own playback settings), because what sounds right
 * depends on the headphones in this listener's ears, not on their account.
 */
import { create } from 'zustand';

import { onElement } from '@/player/engine';

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

export interface AudioSettings {
  /** Seconds the end of one track overlaps the start of the next (0 = off). */
  crossfade: number;
  gapless: boolean;
  normalize: boolean;
  eq: EqPreset;
  /** Animated artwork behind the full player (Canvas). */
  canvas: boolean;
}

const KEY = 'tmusic.audio';
const DEFAULTS: AudioSettings = { crossfade: 0, gapless: true, normalize: false, eq: 'flat', canvas: true };

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
    const { crossfade, gapless, normalize, eq, canvas } = get();
    try {
      localStorage.setItem(KEY, JSON.stringify({ crossfade, gapless, normalize, eq, canvas }));
    } catch {
      /* settings for this session only */
    }
    applyEffects();
  },
}));

// ── the effects chain ─────────────────────────────────────────────────────────
//
// Built only once somebody turns an effect on: routing an element through Web Audio
// cannot be undone, and on some phones a suspended AudioContext stops playback in
// the background. Turning everything off again leaves the chain in place but neutral.

interface Chain {
  ctx: AudioContext;
  input: AudioNode;
  filters: BiquadFilterNode[];
  compressor: DynamicsCompressorNode;
  connected: WeakSet<HTMLAudioElement>;
}

let chain: Chain | null = null;

function wanted(): boolean {
  const { normalize, eq } = useAudioSettings.getState();
  return normalize || eq !== 'flat';
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
    compressor.connect(ctx.destination);
    const built: Chain = { ctx, input: filters[0] as AudioNode, filters, compressor, connected: new WeakSet() };
    onElement((el) => {
      if (built.connected.has(el)) return;
      try {
        ctx.createMediaElementSource(el).connect(built.input);
        built.connected.add(el);
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
  } else {
    c.threshold.value = 0;
    c.knee.value = 0;
    c.ratio.value = 1;
  }
}

/** Browsers start an AudioContext suspended until a gesture; every play is one. */
export function resumeEffects(): void {
  if (chain?.ctx.state === 'suspended') void chain.ctx.resume().catch(() => undefined);
}
