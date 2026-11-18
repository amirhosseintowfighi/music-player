/**
 * The AutoMix analyser on audio where the right answer is known: a drum pattern
 * at a set tempo with a stronger kick on the one, chords in a set key, and a quiet
 * intro and outro around a loud body.
 */
import { describe, expect, it } from 'vitest';

import { analyse, camelot, camelotDistance, camelotName } from '@/player/analysis';

const SR = 11025;

/** A track: kick on every beat (louder on the one), hats in between, a chord pad. */
function song({
  bpm,
  seconds,
  start = 0.5,
  chord = [440, 523.25, 659.25], // A minor: A4 C5 E5
  quiet = [0, 0],
}: {
  bpm: number;
  seconds: number;
  start?: number;
  chord?: number[];
  quiet?: [number, number];
}): Float32Array {
  const out = new Float32Array(Math.floor(seconds * SR));
  const beat = 60 / bpm;
  let seed = 7;
  const noise = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed / 2147483647) * 2 - 1;
  };
  for (let n = 0; start + n * beat < seconds; n += 1) {
    const at = Math.floor((start + n * beat) * SR);
    const one = n % 4 === 0;
    // Kick: a falling sine, 0.12 s.
    for (let i = 0; i < 0.12 * SR && at + i < out.length; i += 1) {
      const t = i / SR;
      const f = 50 + 90 * Math.exp(-t * 30);
      out[at + i] = (out[at + i] as number) + (one ? 0.9 : 0.55) * Math.exp(-t * 18) * Math.sin(2 * Math.PI * f * t);
    }
    // Hat on the off-beat: short noise.
    const off = at + Math.floor((beat / 2) * SR);
    for (let i = 0; i < 0.03 * SR && off + i < out.length; i += 1) {
      out[off + i] = (out[off + i] as number) + 0.15 * Math.exp((-i / SR) * 120) * noise();
    }
  }
  for (let i = 0; i < out.length; i += 1) {
    const t = i / SR;
    let pad = 0;
    for (const f of chord) pad += Math.sin(2 * Math.PI * f * t) + 0.5 * Math.sin(2 * Math.PI * (f / 2) * t);
    out[i] = (out[i] as number) + 0.06 * pad;
  }
  // Quiet intro and outro: the body of the song is in between.
  const [head, tail] = quiet;
  for (let i = 0; i < out.length; i += 1) {
    const t = i / SR;
    if (t < head || t > seconds - tail) out[i] = (out[i] as number) * 0.15;
  }
  return out;
}

describe('camelot', () => {
  it('maps keys onto the wheel and measures harmony', () => {
    expect(camelotName(camelot(0, true))).toBe('8B'); // C major
    expect(camelotName(camelot(9, false))).toBe('8A'); // A minor
    expect(camelotName(camelot(7, true))).toBe('9B'); // G major
    expect(camelotName(camelot(11, true))).toBe('1B'); // B major
    expect(camelotDistance(camelot(0, true), camelot(9, false))).toBe(1); // relative keys
    expect(camelotDistance(camelot(0, true), camelot(7, true))).toBe(1); // a fifth apart
    expect(camelotDistance(camelot(0, true), camelot(6, true))).toBe(6); // a tritone: clash
    expect(camelotDistance(null, 3)).toBe(3);
  });
});

describe('analyse', () => {
  it('finds the tempo, the grid and the one', () => {
    const result = analyse(song({ bpm: 128, seconds: 40, start: 0.5 }), SR);
    expect(result.bpm).toBeGreaterThan(127.5);
    expect(result.bpm).toBeLessThan(128.5);
    const beat = 60 / 128;
    // The first beat sits on the grid (within 30 ms), and the one is the loud kick.
    const offGrid = Math.abs(((result.beat - 0.5) / beat) - Math.round((result.beat - 0.5) / beat)) * beat;
    expect(offGrid).toBeLessThan(0.01);
    const bar = (result.downbeat - 0.5) / beat;
    expect(Math.abs(Math.round(bar) % 4)).toBe(0);
    expect(result.confidence).toBeGreaterThan(0.25);
  });

  it('tells a slow song from a fast one', { timeout: 30_000 }, () => {
    for (const bpm of [75, 92, 104, 110, 133, 140, 160, 172]) {
      const result = analyse(song({ bpm, seconds: 30 }), SR);
      // Half or double time is the same grid to a DJ; anything else is a miss.
      const ratio = result.bpm / bpm;
      const folded = Math.min(Math.abs(ratio - 1), Math.abs(ratio - 2), Math.abs(ratio - 0.5));
      expect(folded, `${bpm} → ${result.bpm}`).toBeLessThan(0.01);
    }
  });

  it('finds the one past a backbeat: equal kicks, snare on 2 and 4, chords on the one', () => {
    const beat = 0.5;
    const start = 0.3;
    const seconds = 40;
    const out = new Float32Array(seconds * SR);
    const chords = [
      [220, 261.63, 329.63],
      [174.61, 220, 261.63],
      [196, 246.94, 293.66],
      [164.81, 207.65, 246.94],
    ];
    let seed = 5;
    const noise = () => ((seed = (seed * 16807) % 2147483647) / 2147483647) * 2 - 1;
    for (let n = 0; start + n * beat < seconds - 0.5; n += 1) {
      const at = Math.floor((start + n * beat) * SR);
      for (let i = 0; i < 0.12 * SR; i += 1) {
        const t = i / SR;
        out[at + i] = (out[at + i] as number) + 0.6 * Math.exp(-t * 16) * Math.sin(2 * Math.PI * (55 + 100 * Math.exp(-t * 35)) * t);
        // A loud snare with low end on the backbeat: the trap for a kick-only detector.
        if (n % 2 === 1) out[at + i] = (out[at + i] as number) + 0.5 * Math.exp(-t * 20) * (0.7 * noise() + 0.5 * Math.sin(2 * Math.PI * 180 * t));
      }
      if (n % 4 === 0) {
        const chord = chords[(n / 4) % 4] as number[];
        for (let i = 0; i < 4 * beat * SR && at + i < out.length; i += 1) {
          const t = i / SR;
          out[at + i] = (out[at + i] as number) + 0.05 * chord.reduce((sum, f) => sum + Math.sin(2 * Math.PI * f * t), 0);
        }
      }
    }
    const result = analyse(out, SR);
    expect(result.bpm).toBeCloseTo(120, 0);
    expect(Math.abs(result.downbeat - start) % (4 * beat)).toBeLessThan(0.03);
  });

  it('does not invent a beat where there is none', () => {
    const pad = new Float32Array(20 * SR);
    for (let i = 0; i < pad.length; i += 1) pad[i] = 0.2 * Math.sin((2 * Math.PI * 220 * i) / SR) * (1 + 0.3 * Math.sin(i / SR));
    expect(analyse(pad, SR).confidence).toBeLessThan(0.25);
  });

  it('hears the key', () => {
    expect(camelotName(analyse(song({ bpm: 120, seconds: 20 }), SR).key)).toBe('8A');
    // G major: G B D.
    const g = analyse(song({ bpm: 120, seconds: 20, chord: [392, 493.88, 587.33] }), SR);
    expect(camelotName(g.key)).toBe('9B');
  });

  it('finds where the intro ends and the outro starts', () => {
    const result = analyse(song({ bpm: 120, seconds: 64, start: 0, quiet: [16, 16] }), SR);
    expect(Math.abs(result.intro - 16)).toBeLessThan(2.1); // within a bar
    expect(Math.abs(result.outro - 48)).toBeLessThan(2.1);
  });
});
