/** AutoMix: what a DJ would decide, and what ends up on the mixer. */
import { describe, expect, it } from 'vitest';

import type { MixAnalysis } from '@/player/analysis';
import { automate, correctedRate, expectedPosition, planMix, powerCurve, tempoRatio } from '@/player/automix';
import type { MixNodes } from '@/store/audio';

function song(bpm: number, key: number | null, extra: Partial<MixAnalysis> = {}): MixAnalysis {
  return { v: 1, bpm, beat: 0.2, downbeat: 0.2, key, intro: 15, outro: 180, confidence: 0.7, ...extra };
}

describe('planner', () => {
  it('blends harmonic songs at close tempos for 16 bars, phrase-aligned at the outro', () => {
    const plan = planMix({ duration: 215, mix: song(124, 7) }, { duration: 200, mix: song(126, 8) });
    expect(plan.style).toBe('blend'); // 8A → 9A: neighbours on the wheel
    expect(plan.bars).toBe(16);
    expect(plan.rate).toBeCloseTo(124 / 126, 5); // the incoming song comes to our tempo
    const bar = (4 * 60) / 124;
    expect(((plan.outAt - 0.2) / bar) % 1).toBeCloseTo(0, 5); // on a bar line
    expect(plan.outAt + plan.seconds).toBeLessThanOrEqual(215);
    expect(plan.inAt).toBe(0.2); // the incoming first downbeat
  });

  it('keeps clashing keys apart with a short bass swap', () => {
    const plan = planMix({ duration: 215, mix: song(124, 7) }, { duration: 200, mix: song(124, 1) });
    expect(plan.style).toBe('swap');
    expect(plan.bars).toBe(8);
    expect(plan.harmonic).toBe(false);
  });

  it('beatmatches half and double time', () => {
    expect(tempoRatio(128, 64)).toBeCloseTo(1);
    expect(tempoRatio(87, 172)).toBeCloseTo(174 / 172, 5);
    const plan = planMix({ duration: 215, mix: song(172, 7) }, { duration: 200, mix: song(87, 7) });
    expect(plan.style).toBe('blend');
  });

  it('echoes out when the tempos are too far apart', () => {
    const plan = planMix({ duration: 215, mix: song(95, 7) }, { duration: 200, mix: song(128, 7) });
    expect(plan.style).toBe('echo');
    expect(plan.rate).toBe(1);
    expect(plan.seconds).toBeCloseTo(8 * (60 / 95));
  });

  it('falls back to filters and fades without a trustworthy grid', () => {
    expect(planMix({ duration: 215, mix: song(124, 7, { confidence: 0.1 }) }, { duration: 200, mix: song(124, 7) }).style).toBe(
      'filter',
    );
    const fade = planMix({ duration: 215, mix: null }, { duration: 200, mix: null });
    expect(fade.style).toBe('fade');
    expect(fade.outAt + fade.seconds).toBeLessThan(215);
  });

  it('never cuts much of a song, nor mixes past its end', () => {
    // The outro is early (a long fade-out): mixing starts no more than 75 s before the end.
    const plan = planMix({ duration: 300, mix: song(124, 7, { outro: 120 }) }, { duration: 200, mix: song(124, 7) });
    expect(plan.outAt).toBeGreaterThanOrEqual(300 - 75 - 0.001);
    // A short song still fits its mix.
    const short = planMix({ duration: 70, mix: song(124, 7, { outro: 60 }) }, { duration: 200, mix: song(124, 7) });
    expect(short.outAt + short.seconds).toBeLessThanOrEqual(70);
  });
});

describe('beat lock', () => {
  const plan = planMix({ duration: 215, mix: song(124, 7) }, { duration: 200, mix: song(126, 8) });

  it('knows where the incoming deck should be', () => {
    expect(expectedPosition(plan, plan.outAt)).toBeCloseTo(plan.inAt);
    expect(expectedPosition(plan, plan.outAt + 10)).toBeCloseTo(plan.inAt + 10 * plan.rate);
  });

  it('rides the pitch, gently: ahead is slowed, behind is pushed, never past 3%', () => {
    expect(correctedRate(plan, 0.02)).toBeLessThan(plan.rate);
    expect(correctedRate(plan, -0.02)).toBeGreaterThan(plan.rate);
    expect(correctedRate(plan, 1)).toBeCloseTo(plan.rate * 0.97);
  });
});

// A recording AudioParam: every automation event, in order.
function param(name: string, log: string[]) {
  return {
    value: 0,
    cancelScheduledValues: (t: number) => log.push(`${name} cancel ${t.toFixed(2)}`),
    setValueAtTime: (v: number, t: number) => log.push(`${name} set ${v} @${t.toFixed(2)}`),
    linearRampToValueAtTime: (v: number, t: number) => log.push(`${name} ramp ${v} @${t.toFixed(2)}`),
    exponentialRampToValueAtTime: (v: number, t: number) => log.push(`${name} exp ${v} @${t.toFixed(2)}`),
    setValueCurveAtTime: (c: Float32Array, t: number, d: number) =>
      log.push(`${name} curve ${c[0]?.toFixed(1)}→${c[c.length - 1]?.toFixed(1)} @${t.toFixed(2)}+${d.toFixed(2)}`),
  };
}
function deck(name: string, log: string[]): MixNodes {
  const biquad = (n: string) => ({ type: 'highpass', gain: param(`${name}.${n}`, log), frequency: param(`${name}.${n}.freq`, log), Q: param(`${name}.${n}.q`, log) });
  return {
    low: biquad('low'),
    mid: biquad('mid'),
    high: biquad('high'),
    filter: biquad('filter'),
    fader: { gain: param(`${name}.fader`, log) },
    send: { gain: param(`${name}.send`, log) },
    delay: { delayTime: param(`${name}.delay`, log) },
    feedback: { gain: param(`${name}.feedback`, log) },
  } as unknown as MixNodes;
}

describe('automation', () => {
  it('a blend: incoming bass killed until the halfway bar line, then the basses swap', () => {
    const log: string[] = [];
    const plan = planMix({ duration: 215, mix: song(120, 7) }, { duration: 200, mix: song(120, 8) });
    automate(plan, deck('out', log), deck('in', log), 100);
    const swap = 100 + 8 * 4 * 0.5; // 16 bars at 120: the swap after 8 bars, 16 s in
    expect(log).toContain('in.low set -30 @100.00');
    expect(log).toContain(`in.low ramp 0 @${(swap + 0.5).toFixed(2)}`);
    expect(log).toContain(`out.low ramp -30 @${(swap + 0.5).toFixed(2)}`);
    expect(log).toContain(`in.fader curve 0.0→1.0 @100.00+${(swap - 100).toFixed(2)}`);
    expect(log).toContain(`out.fader curve 1.0→0.0 @${swap.toFixed(2)}+${(132 - swap).toFixed(2)}`);
    expect(log.some((line) => line.startsWith('out.filter.freq exp 280'))).toBe(true);
  });

  it('an echo out: the beat before the cut goes into a dotted-eighth echo', () => {
    const log: string[] = [];
    const plan = planMix({ duration: 215, mix: song(90, 7) }, { duration: 200, mix: song(128, 7) });
    automate(plan, deck('out', log), deck('in', log), 50);
    const beat = 60 / 90;
    expect(log).toContain(`out.delay set ${beat * 0.75} @${(50 - beat).toFixed(2)}`);
    expect(log).toContain(`out.send ramp 0.8 @${(50 - beat / 2).toFixed(2)}`);
    expect(log).toContain('out.send set 0 @50.00');
    expect(log).toContain('out.fader ramp 0 @50.03');
  });

  it('fades are equal power: the sum never dips', () => {
    const up = powerCurve(true);
    const down = powerCurve(false);
    for (let i = 0; i < up.length; i += 1) expect((up[i] as number) ** 2 + (down[i] as number) ** 2).toBeCloseTo(1, 5);
  });
});
