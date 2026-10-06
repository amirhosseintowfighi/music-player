import type { Track } from '@/api/client';
import { artistNames } from '@/lib/format';
import { drawCard } from '@/lib/shareCard';
import { BOT_USERNAME } from '@/lib/telegram';

export function buildShareUrl(trackId: number, bot: string = BOT_USERNAME): string {
  return `https://t.me/${bot}?startapp=tr_${trackId}`;
}

export type ShareOutcome = 'shared' | 'sheet' | 'failed';

export async function shareTrack(track: Track, coverSrc?: string): Promise<ShareOutcome> {
  const nav = navigator as Navigator & {
    canShare?: (data: { files: File[] }) => boolean;
    share?: (data: ShareData) => Promise<void>;
  };
  const url = buildShareUrl(track.id);
  const text = `${track.title} — ${artistNames(track) || ''}`.trim();

  // Tier 1: try sharing the rendered card image as a file
  try {
    const canvas = await drawCard(track, coverSrc, BOT_USERNAME);
    const blob: Blob | null = canvas
      ? await new Promise<Blob | null>((resolve) => canvas.toBlob((b) => resolve(b), 'image/png'))
      : null;
    if (blob) {
      const file = new File([blob], `${track.title.slice(0, 40) || 'song'}.png`, { type: 'image/png' });
      if (nav.canShare?.({ files: [file] }) && nav.share) {
        try {
          await nav.share({ files: [file], title: track.title });
          return 'shared';
        } catch (e) {
          if ((e as Error | undefined)?.name === 'AbortError') return 'shared';
          // fall through to url tier
        }
      }
    }
  } catch {
    // ignore and continue to next tier
  }

  // Tier 2: share a plain link via the OS sheet (works in Telegram WebView where file share is blocked)
  if (nav.share) {
    try {
      await nav.share({ title: track.title, text, url });
      return 'shared';
    } catch (e) {
      if ((e as Error | undefined)?.name === 'AbortError') return 'shared';
      // fall through to in-app sheet
    }
  }

  // Tier 3: no system sheet available — let the caller open the in-app ShareSheet
  return 'sheet';
}
