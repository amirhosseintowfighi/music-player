/**
 * AutoMix: the next song mixed in the way a DJ would do it.
 *
 * The planner reads both songs' analyses (tempo, beat grid, key, intro and outro)
 * and decides where and how to mix:
 *
 * - **blend** — tempos within reach and keys in harmony (Camelot neighbours): the
 *   incoming song is beatmatched (time-stretched, pitch kept) and phrase-aligned,
 *   brought in under the outgoing one with its bass cut, the basses swapped on a bar
 *   line halfway through, the outgoing one filtered out over its last bars. 16 bars.
 * - **swap** — tempos match but keys clash: the same, in 8 bars, with the incoming
 *   mids held down until the swap so the two melodies never fight.
 * - **echo** — tempos too far apart to beatmatch: the outgoing song throws its last
 *   beat into a dotted-eighth echo and drops out on the bar line, the incoming one
 *   lands on its first downbeat under the echo tail.
 * - **filter** — a grid not worth trusting on one side: a high-pass sweep out, a
 *   low-pass sweep in, over eight seconds.
 * - **fade** — nothing known about one song: an equal-power fade with the incoming
 *   bass held back, which already sounds better than a plain crossfade.
 *
 * The runtime then drives the two decks' channel strips (store/audio.ts) with Web
 * Audio automation, keeps the beats locked while they overlap (phase is measured
 * every 100 ms and corrected by nudging the incoming deck's tempo, as a DJ rides
 * the pitch fader), and after the mix glides the new song back to its own tempo.
 */
import type { Track } from '@/api/client';
import { camelotDistance, type MixAnalysis } from '@/player/analysis';
import { mixDeck, neutral, type MixNodes } from '@/store/audio';

export type MixStyle = 'blend' | 'swap' | 'echo' | 'filter' | 'fade';

export interface MixPlan {
  style: MixStyle;
  /** Seconds into the outgoing song when the incoming one starts. */
  outAt: number;
  /** Seconds into the incoming song where it starts playing. */
  inAt: number;
  /** How long the two overlap, in real seconds. */
  seconds: number;
  /** The incoming deck's playback rate during the mix (1 = its own tempo). */
  rate: number;
  /** One beat of the outgoing song, in seconds (echo time, automation grid). */
  beat: number;
  bars: number;
  fromBpm: number | null;
  toBpm: number | null;
  fromKey: number | null;
  toKey: number | null;
  harmonic: boolean;
}

/** Below this the beat grid is not trusted for beatmatching. */
const MIN_CONFIDENCE = 0.3;
/** How far a deck may be stretched; past ~8% stretching is audible. */
const MAX_STRETCH = 0.08;
/** AutoMix never cuts more than this off the end of a song. */
const MAX_CUT_S = 75;
/** Nor mixes so long that it stops being a transition. */
const MAX_MIX_S = 34;

function usable(mix: MixAnalysis | null): mix is MixAnalysis {
  return Boolean(mix && mix.bpm > 0 && mix.confidence >= MIN_CONFIDENCE);
}

/** The tempo ratio to play ``next`` at so it matches ``cur`` (half/double time folded). */
export function tempoRatio(curBpm: number, nextBpm: number): number {
  const ratio = curBpm / nextBpm;
  return [ratio, ratio * 2, ratio / 2].reduce((best, r) => (Math.abs(r - 1) < Math.abs(best - 1) ? r : best));
}

/** The latest bar line of ``mix`` at or before ``t`` (on its grid). */
function barAtOrBefore(mix: MixAnalysis, t: number): number {
  const bar = (4 * 60) / mix.bpm;
  return mix.downbeat + Math.floor((t - mix.downbeat) / bar) * bar;
}

/**
 * Where to start mixing out of ``cur``: on a phrase (8 bars from the first downbeat)
 * at the outro, where the song itself is winding down — late enough to have heard
 * the song, early enough for the whole mix to fit before it ends.
 */
function mixOutPoint(cur: MixAnalysis, duration: number, seconds: number): number {
  const bar = (4 * 60) / cur.bpm;
  const phrase = 8 * bar;
  const latest = barAtOrBefore(cur, duration - seconds - 0.25);
  const earliest = Math.max(duration - MAX_CUT_S, duration * 0.5);
  // The phrase the outro starts in, or the bar the outro starts on.
  const atPhrase = cur.downbeat + Math.round((cur.outro - cur.downbeat) / phrase) * phrase;
  const atBar = barAtOrBefore(cur, cur.outro);
  for (const candidate of [atPhrase, atBar]) {
    if (candidate >= earliest && candidate <= latest) return candidate;
  }
  return Math.max(earliest, latest);
}

export function planMix(
  cur: { duration: number; mix: MixAnalysis | null },
  next: { duration: number; mix: MixAnalysis | null },
): MixPlan {
  const a = cur.mix;
  const b = next.mix;
  const base = {
    fromBpm: a?.bpm ?? null,
    toBpm: b?.bpm ?? null,
    fromKey: a?.key ?? null,
    toKey: b?.key ?? null,
    harmonic: camelotDistance(a?.key ?? null, b?.key ?? null) <= 1,
  };
  // The incoming song starts on its first downbeat: no dead air, no half bar.
  const inAt = b ? Math.max(0, b.downbeat) : 0;

  if (usable(a) && usable(b)) {
    const rate = tempoRatio(a.bpm, b.bpm);
    const beat = 60 / a.bpm;
    if (Math.abs(rate - 1) <= MAX_STRETCH) {
      const style: MixStyle = base.harmonic ? 'blend' : 'swap';
      // Phrase matching, the way a DJ counts it: the mix should start where the
      // outgoing song's outro does and last as long as the incoming song's intro, so
      // the new song's body arrives exactly as the old one ends. Of the lengths that
      // fit, the one closest to that wins; on a tie, the longer one.
      const introBars = Math.max(0, (b.intro - inAt) / ((4 * 60) / b.bpm));
      let chosen: MixPlan | null = null;
      let bestMiss = Infinity;
      for (const bars of style === 'blend' ? [16, 8, 4] : [8, 4]) {
        const seconds = bars * 4 * beat;
        if (seconds > MAX_MIX_S && bars > 4) continue;
        const outAt = mixOutPoint(a, cur.duration, seconds);
        const fits = outAt + seconds <= cur.duration - 0.2 && inAt + seconds * rate <= next.duration * 0.6;
        if (!fits) continue;
        const barOut = 4 * beat;
        const miss = Math.abs(outAt - a.outro) / barOut + (introBars > 0 ? Math.abs(bars - introBars) : 0);
        if (miss < bestMiss - 0.5) {
          bestMiss = miss;
          chosen = { ...base, style, outAt, inAt, seconds, rate, beat, bars };
        }
      }
      if (chosen) return chosen;
    }
    // Too far apart to beatmatch: echo out on a bar line, land on the downbeat.
    const seconds = 2 * 4 * beat;
    const outAt = mixOutPoint(a, cur.duration, seconds);
    if (outAt + 1 <= cur.duration) return { ...base, style: 'echo', outAt, inAt, seconds, rate: 1, beat, bars: 2 };
  }

  const seconds = Math.min(8, Math.max(3, cur.duration * 0.05));
  // Without a grid, the outro (if known) is still the place to leave.
  const tail = cur.duration - seconds - 0.5;
  const outAt = a ? Math.min(tail, Math.max(cur.duration - MAX_CUT_S, a.outro - seconds / 2)) : tail;
  return {
    ...base,
    style: a || b ? 'filter' : 'fade',
    outAt: Math.max(0, outAt),
    inAt,
    seconds,
    rate: 1,
    beat: a ? 60 / a.bpm : 0.5,
    bars: 0,
  };
}

// ── automation ────────────────────────────────────────────────────────────────

const KILL_DB = -30;

/** Equal-power fade curve: ``up`` from silence to full, or down. */
export function powerCurve(up: boolean, points = 64): Float32Array {
  const curve = new Float32Array(points);
  for (let i = 0; i < points; i += 1) {
    const x = i / (points - 1);
    curve[i] = up ? Math.sin((x * Math.PI) / 2) : Math.cos((x * Math.PI) / 2);
  }
  return curve;
}

function hold(param: AudioParam, value: number, at: number): void {
  param.cancelScheduledValues(at);
  param.setValueAtTime(value, at);
}

function ramp(param: AudioParam, from: number, to: number, start: number, end: number): void {
  param.setValueAtTime(from, start);
  param.linearRampToValueAtTime(to, Math.max(start + 0.01, end));
}

/** Frequency sweeps sound even only on a log scale. */
function sweep(param: AudioParam, from: number, to: number, start: number, end: number): void {
  param.setValueAtTime(from, start);
  param.exponentialRampToValueAtTime(to, Math.max(start + 0.01, end));
}

/**
 * Writes one transition onto the two channel strips. ``t0`` is when the incoming
 * deck starts, on the audio clock. Pure scheduling: easy to read, easy to test.
 */
export function automate(plan: MixPlan, out: MixNodes, inn: MixNodes, t0: number): void {
  const L = plan.seconds;
  const beat = plan.beat;
  // The swap happens on a bar line halfway through: L is whole bars, so is L / 2.
  const swap = t0 + (plan.bars >= 2 ? Math.round(plan.bars / 2) * 4 * beat : L / 2);
  const end = t0 + L;
  for (const node of [inn, out]) {
    for (const param of [node.low.gain, node.mid.gain, node.high.gain, node.fader.gain, node.send.gain]) {
      param.cancelScheduledValues(t0 - 2 * beat);
    }
  }
  inn.filter.type = 'highpass';
  out.filter.type = 'highpass';

  switch (plan.style) {
    case 'blend':
    case 'swap': {
      // In: fader up over the first half, bass killed until the swap, highs eased in.
      hold(inn.fader.gain, 0, t0);
      inn.fader.gain.setValueCurveAtTime(powerCurve(true), t0, swap - t0);
      hold(inn.low.gain, KILL_DB, t0);
      ramp(inn.low.gain, KILL_DB, 0, swap, swap + beat);
      ramp(inn.high.gain, -12, 0, t0, swap);
      if (plan.style === 'swap') ramp(inn.mid.gain, -9, 0, swap, swap + 2 * beat);
      // Out: the bass goes as the new one arrives, then the fader and the top end.
      hold(out.low.gain, 0, t0);
      ramp(out.low.gain, 0, KILL_DB, swap, swap + beat);
      if (plan.style === 'swap') ramp(out.mid.gain, 0, -9, swap, swap + 2 * beat);
      ramp(out.high.gain, 0, -8, swap, end);
      hold(out.fader.gain, 1, swap);
      out.fader.gain.setValueCurveAtTime(powerCurve(false), swap, end - swap);
      sweep(out.filter.frequency, 10, 280, swap + (end - swap) / 2, end);
      break;
    }
    case 'echo': {
      // The last beat goes into a dotted-eighth echo; the dry signal drops on the one.
      out.delay.delayTime.setValueAtTime(beat * 0.75, t0 - beat);
      out.feedback.gain.setValueAtTime(0.55, t0 - beat);
      ramp(out.send.gain, 0, 0.8, t0 - beat, t0 - beat / 2);
      hold(out.send.gain, 0.8, t0 - beat / 2);
      out.send.gain.setValueAtTime(0, t0);
      ramp(out.fader.gain, 1, 0, t0 - 0.02, t0 + 0.03);
      // The feedback dies away over the next bars.
      out.feedback.gain.setValueAtTime(0.55, t0 + 2 * beat);
      out.feedback.gain.linearRampToValueAtTime(0, end);
      // In: on the downbeat, fast, with the bass kept back for one bar under the tail.
      hold(inn.fader.gain, 0, t0);
      inn.fader.gain.setValueCurveAtTime(powerCurve(true), t0, beat);
      hold(inn.low.gain, -18, t0);
      ramp(inn.low.gain, -18, 0, t0 + 3 * beat, t0 + 4 * beat);
      break;
    }
    case 'filter': {
      // Out: a high-pass sweep thins the song away; In: a low-pass opens the new one up.
      sweep(out.filter.frequency, 10, 1400, t0, end);
      hold(out.fader.gain, 1, t0);
      out.fader.gain.setValueCurveAtTime(powerCurve(false), t0 + L / 2, L / 2);
      inn.filter.type = 'lowpass';
      sweep(inn.filter.frequency, 350, 20000, t0, end);
      hold(inn.fader.gain, 0, t0);
      inn.fader.gain.setValueCurveAtTime(powerCurve(true), t0, L / 2);
      break;
    }
    default: {
      // Fade: equal power, with the incoming bass held back so the lows never pile up.
      hold(inn.fader.gain, 0, t0);
      inn.fader.gain.setValueCurveAtTime(powerCurve(true), t0, L);
      hold(out.fader.gain, 1, t0);
      out.fader.gain.setValueCurveAtTime(powerCurve(false), t0, L);
      hold(inn.low.gain, -18, t0);
      ramp(inn.low.gain, -18, 0, t0 + L / 2, end);
    }
  }
}

// ── beat lock ─────────────────────────────────────────────────────────────────

/** Where the incoming deck should be, given where the outgoing one is. */
export function expectedPosition(plan: MixPlan, outPosition: number): number {
  return plan.inAt + (outPosition - plan.outAt) * plan.rate;
}

/**
 * The incoming deck's rate that pulls it back onto the grid: ahead plays slower,
 * behind plays faster, never more than 3% off the planned rate — the way a DJ rides
 * the pitch fader rather than jumping the record.
 */
export function correctedRate(plan: MixPlan, error: number): number {
  const nudge = Math.max(-0.03, Math.min(0.03, -error * 0.6));
  return plan.rate * (1 + nudge);
}

// ── runtime ───────────────────────────────────────────────────────────────────

export interface MixState {
  style: MixStyle;
  fromBpm: number | null;
  toBpm: number | null;
  fromKey: number | null;
  toKey: number | null;
  /** Wall-clock milliseconds: when the mix started and when it will be over. */
  started: number;
  until: number;
}

interface Armed {
  out: HTMLAudioElement;
  inn: HTMLAudioElement;
  plan: MixPlan;
  /** When the incoming deck starts, on the audio clock. */
  t0: number;
}

interface Running extends Armed {
  timers: ReturnType<typeof setTimeout>[];
}

let armed: Armed | null = null;
let running: Running | null = null;
let onState: (state: MixState | null) => void = () => undefined;

export function onMixState(listener: (state: MixState | null) => void): void {
  onState = listener;
}

export function isMixing(): boolean {
  return running !== null;
}

/**
 * Writes the whole transition onto both decks ahead of time, ``secondsUntil`` from
 * now: the echo needs the beat *before* the cut, and the audio clock is far more
 * precise than any timer. Returns false where there is no audio graph to drive.
 */
export function armMix(out: HTMLAudioElement, inn: HTMLAudioElement, plan: MixPlan, secondsUntil: number): boolean {
  disarm();
  const outDeck = mixDeck(out);
  const inDeck = mixDeck(inn);
  if (!outDeck || !inDeck || out === inn) return false;
  const t0 = outDeck.ctx.currentTime + Math.max(0, secondsUntil);
  automate(plan, outDeck.nodes, inDeck.nodes, t0);
  armed = { out, inn, plan, t0 };
  return true;
}

/** Takes back an armed transition that will not happen (a skip, a pause, a seek). */
export function disarm(): void {
  const was = armed;
  armed = null;
  if (!was || running) return;
  for (const el of [was.out, was.inn]) {
    const deck = mixDeck(el);
    if (deck) neutral(deck.nodes, deck.ctx);
  }
}

/** Stops a mix at once: the outgoing deck stops, the incoming one is left at full. */
export function abortMix(): void {
  disarm();
  const current = running;
  if (!current) return;
  running = null;
  for (const timer of current.timers) clearTimeout(timer);
  current.out.pause();
  for (const el of [current.out, current.inn]) {
    const deck = mixDeck(el);
    if (deck) neutral(deck.nodes, deck.ctx);
  }
  current.inn.playbackRate = 1;
  onState(null);
}

/** Puts the incoming deck on its in point at the planned rate, pitch kept. */
export function prepareIncoming(inn: HTMLAudioElement, plan: MixPlan): void {
  try {
    inn.preservesPitch = true;
  } catch {
    /* older engines keep pitch anyway */
  }
  inn.currentTime = plan.inAt;
  inn.playbackRate = plan.rate;
}

/** Called once the incoming deck is playing: keeps the beats locked, then hands over. */
export function runMix(out: HTMLAudioElement, inn: HTMLAudioElement, plan: MixPlan): void {
  const outDeck = mixDeck(out);
  const inDeck = mixDeck(inn);
  if (!outDeck || !inDeck) {
    // No audio graph (fragile WebView, a browser that refused): a plain handover.
    disarm();
    out.pause();
    return;
  }
  // Armed for these decks: the automation is already on the clock. Otherwise (a
  // late start) it is written now.
  let t0 = inDeck.ctx.currentTime;
  if (armed && armed.out === out && armed.inn === inn) t0 = armed.t0;
  else automate(plan, outDeck.nodes, inDeck.nodes, t0);
  armed = null;
  // Beatmatched styles stay locked for the whole overlap; an echo out is locked too,
  // so the new song lands exactly on its downbeat under the echo tail.
  const beatmatched = plan.style === 'blend' || plan.style === 'swap' || plan.style === 'echo';
  const tail = plan.style === 'echo' ? plan.seconds + 4 * plan.beat : plan.seconds;
  const remaining = Math.max(0.1, t0 + tail - inDeck.ctx.currentTime);
  const timers: Running['timers'] = [];
  const current: Running = { out, inn, plan, t0, timers };
  running = current;
  onState({
    style: plan.style,
    fromBpm: plan.fromBpm,
    toBpm: plan.toBpm,
    fromKey: plan.fromKey,
    toKey: plan.toKey,
    started: Date.now(),
    until: Date.now() + remaining * 1000,
  });
  if (beatmatched) {
    // Beat lock: while the two overlap, measure the phase and correct it.
    timers.push(
      setInterval(() => {
        if (out.paused || inn.paused) return;
        const error = inn.currentTime - expectedPosition(plan, out.currentTime);
        const silent = inDeck.nodes.fader.gain.value < 0.2;
        if (Math.abs(error) > 0.12 && silent) {
          // Not audible yet: jump straight onto the grid.
          inn.currentTime = expectedPosition(plan, out.currentTime);
          inn.playbackRate = plan.rate;
        } else {
          inn.playbackRate = Math.abs(error) < 0.004 ? plan.rate : correctedRate(plan, error);
        }
      }, 100) as unknown as ReturnType<typeof setTimeout>,
    );
  }
  timers.push(
    setTimeout(() => {
      if (running !== current) return;
      running = null;
      for (const timer of timers) clearTimeout(timer);
      out.pause();
      neutral(outDeck.nodes, outDeck.ctx);
      neutral(inDeck.nodes, inDeck.ctx);
      onState(null);
      // Back to the new song's own tempo, gently, over about eight bars.
      if (inn.playbackRate !== 1) glideToOwnTempo(inn, plan);
    }, remaining * 1000 + 50),
  );
}

function glideToOwnTempo(inn: HTMLAudioElement, plan: MixPlan): void {
  const start = inn.playbackRate;
  const steps = 32;
  const every = (8 * 4 * (60 / ((plan.toBpm ?? 120) * start)) * 1000) / steps;
  let step = 0;
  const timer = setInterval(() => {
    step += 1;
    if (inn.paused || step >= steps) {
      inn.playbackRate = 1;
      clearInterval(timer);
      return;
    }
    inn.playbackRate = start + ((1 - start) * step) / steps;
  }, every);
}

// ── deciding when ─────────────────────────────────────────────────────────────

/** The pair a plan was made for, so it is made once. */
let planned: { from: number; to: number; plan: MixPlan } | null = null;

export function planFor(current: Track, next: Track, a: MixAnalysis | null, b: MixAnalysis | null): MixPlan {
  if (planned && planned.from === current.id && planned.to === next.id) return planned.plan;
  const plan = planMix({ duration: current.duration, mix: a }, { duration: next.duration, mix: b });
  planned = { from: current.id, to: next.id, plan };
  return plan;
}

/** Forget a plan whose analyses got better (a measurement just arrived). */
export function replan(): void {
  planned = null;
}
