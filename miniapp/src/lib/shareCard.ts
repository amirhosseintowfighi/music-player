/**
 * Share cards: a story-sized picture of a song, like Spotify's.
 *
 * Drawn on a canvas on the device: the artwork on a wash of its own colours, the
 * title and the artist, and the bot's name so whoever sees it knows where to find
 * it. Shared as a file where the platform can (the system share sheet), downloaded
 * where it cannot. Nothing is uploaded anywhere.
 */
import type { Track } from '@/api/client';
import { artistNames, coverColors } from '@/lib/format';

export const CARD_W = 1080;
export const CARD_H = 1920;
const COVER = 760;

function palette(track: Track): readonly [string, string] {
  const parts = (track.palette ?? '').split(',').filter((c) => /^#[0-9a-f]{6}$/i.test(c));
  if (parts.length >= 2) return [parts[0] as string, parts[1] as string];
  return coverColors(track.id);
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = src;
  });
}

/** Shrinks the text until it fits ``width``, down to ``min`` px, then truncates. */
function fitText(ctx: CanvasRenderingContext2D, text: string, width: number, size: number, min: number, weight: number): string {
  let px = size;
  for (; px > min; px -= 4) {
    ctx.font = `${weight} ${px}px system-ui, -apple-system, "Vazirmatn", sans-serif`;
    if (ctx.measureText(text).width <= width) return text;
  }
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > width) cut = cut.slice(0, -1);
  return cut === text ? text : `${cut}…`;
}

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export async function drawCard(track: Track, coverSrc: string | undefined, brand: string): Promise<HTMLCanvasElement | null> {
  const canvas = document.createElement('canvas');
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const [c1, c2] = palette(track);

  const wash = ctx.createLinearGradient(0, 0, CARD_W, CARD_H);
  wash.addColorStop(0, c1);
  wash.addColorStop(1, c2);
  ctx.fillStyle = wash;
  ctx.fillRect(0, 0, CARD_W, CARD_H);
  // Darken the bottom so white text always reads, whatever the artwork's colours.
  const shade = ctx.createLinearGradient(0, CARD_H * 0.45, 0, CARD_H);
  shade.addColorStop(0, 'rgba(0,0,0,0)');
  shade.addColorStop(1, 'rgba(0,0,0,0.55)');
  ctx.fillStyle = shade;
  ctx.fillRect(0, 0, CARD_W, CARD_H);

  const x = (CARD_W - COVER) / 2;
  const y = 330;
  const image = coverSrc ? await loadImage(coverSrc) : null;
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.45)';
  ctx.shadowBlur = 80;
  ctx.shadowOffsetY = 30;
  roundedRect(ctx, x, y, COVER, COVER, 44);
  ctx.fillStyle = 'rgba(255,255,255,0.14)';
  ctx.fill();
  ctx.restore();
  ctx.save();
  roundedRect(ctx, x, y, COVER, COVER, 44);
  ctx.clip();
  if (image) {
    ctx.drawImage(image, x, y, COVER, COVER);
  } else {
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.font = '700 300px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('♫', CARD_W / 2, y + COVER / 2);
  }
  ctx.restore();

  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';
  ctx.direction = /[؀-ۿ]/.test(track.title) ? 'rtl' : 'ltr';
  const title = fitText(ctx, track.title, CARD_W - 160, 76, 48, 800);
  ctx.fillText(title, CARD_W / 2, y + COVER + 150);
  ctx.fillStyle = 'rgba(255,255,255,0.78)';
  const artist = fitText(ctx, artistNames(track) || '', CARD_W - 200, 50, 36, 500);
  ctx.fillText(artist, CARD_W / 2, y + COVER + 225);

  ctx.direction = 'ltr';
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.font = '600 38px system-ui, sans-serif';
  ctx.fillText(`♪  @${brand}`, CARD_W / 2, CARD_H - 150);
  return canvas;
}

function toBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
}

/** Shares the card through the system sheet, or downloads it. Returns what happened. */
export async function shareCard(track: Track, coverSrc: string | undefined, brand: string): Promise<'shared' | 'saved' | 'failed'> {
  const canvas = await drawCard(track, coverSrc, brand);
  const blob = canvas ? await toBlob(canvas) : null;
  if (!blob) return 'failed';
  const file = new File([blob], `${track.title.slice(0, 40) || 'song'}.png`, { type: 'image/png' });
  const nav = navigator as Navigator & { canShare?: (data: { files: File[] }) => boolean };
  if (nav.canShare?.({ files: [file] }) && nav.share) {
    try {
      await nav.share({ files: [file], title: track.title });
      return 'shared';
    } catch (error) {
      // The listener closed the sheet: that is not a failure worth a toast.
      if ((error as Error | undefined)?.name === 'AbortError') return 'shared';
    }
  }
  try {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    return 'saved';
  } catch {
    return 'failed';
  }
}
