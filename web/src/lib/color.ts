/**
 * Dominant colours of the current artwork drive the background gradient.
 *
 * Downscaling to 12×12 on a canvas is enough for an average-ish palette and costs
 * well under a millisecond. Results are cached per URL.
 */

export interface Palette {
  c1: string;
  c2: string;
  c3: string;
}

const cache = new Map<string, Palette>();
export const DEFAULT_PALETTE: Palette = { c1: '#2bd9c4', c2: '#4a6bff', c3: '#b02bd9' };

function hex(r: number, g: number, b: number): string {
  return `#${[r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')}`;
}

/** Pushes a colour away from grey so the background keeps some life. */
function vivid(r: number, g: number, b: number, boost = 1.35): [number, number, number] {
  const mean = (r + g + b) / 3;
  return [mean + (r - mean) * boost, mean + (g - mean) * boost, mean + (b - mean) * boost];
}

export function paletteFromImageData(data: Uint8ClampedArray): Palette {
  const buckets = new Map<string, { r: number; g: number; b: number; n: number }>();
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3] ?? 0;
    if (a < 128) continue;
    const r = data[i] ?? 0;
    const g = data[i + 1] ?? 0;
    const b = data[i + 2] ?? 0;
    // Quantise to 32 levels per channel so similar pixels group together.
    const key = `${r >> 5}:${g >> 5}:${b >> 5}`;
    const bucket = buckets.get(key) ?? { r: 0, g: 0, b: 0, n: 0 };
    bucket.r += r;
    bucket.g += g;
    bucket.b += b;
    bucket.n += 1;
    buckets.set(key, bucket);
  }
  const ranked = [...buckets.values()]
    .sort((a, b) => b.n - a.n)
    .slice(0, 3)
    .map((bucket) => {
      const [r, g, b] = vivid(bucket.r / bucket.n, bucket.g / bucket.n, bucket.b / bucket.n);
      return hex(r, g, b);
    });
  return {
    c1: ranked[0] ?? DEFAULT_PALETTE.c1,
    c2: ranked[1] ?? ranked[0] ?? DEFAULT_PALETTE.c2,
    c3: ranked[2] ?? ranked[0] ?? DEFAULT_PALETTE.c3,
  };
}

export async function paletteFromUrl(url: string): Promise<Palette> {
  const cached = cache.get(url);
  if (cached) return cached;
  const image = new Image();
  image.crossOrigin = 'anonymous';
  image.src = url;
  await image.decode();
  const canvas = document.createElement('canvas');
  canvas.width = 12;
  canvas.height = 12;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return DEFAULT_PALETTE;
  context.drawImage(image, 0, 0, 12, 12);
  const palette = paletteFromImageData(context.getImageData(0, 0, 12, 12).data);
  cache.set(url, palette);
  return palette;
}

export function applyPalette(palette: Palette): void {
  const style = document.documentElement.style;
  style.setProperty('--art-1', palette.c1);
  style.setProperty('--art-2', palette.c2);
  style.setProperty('--art-3', palette.c3);
}

/** "#rrggbb,#rrggbb,#rrggbb" from the API, or null when it is not a palette. */
export function parsePalette(stored: string | null | undefined): Palette | null {
  const parts = (stored ?? '').split(',');
  if (parts.length !== 3 || !parts.every((part) => /^#[0-9a-fA-F]{6}$/.test(part))) return null;
  return { c1: parts[0] as string, c2: parts[1] as string, c3: parts[2] as string };
}
