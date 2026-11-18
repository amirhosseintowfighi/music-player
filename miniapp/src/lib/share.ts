import { post, type Track } from '@/api/client';
import { artistNames } from '@/lib/format';
import { drawCard } from '@/lib/shareCard';
import { BOT_USERNAME, canShareToStory, shareToStory } from '@/lib/telegram';

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
        }
      }
    }
  } catch {
    // ignore
  }

  if (nav.share) {
    try {
      await nav.share({ title: track.title, text, url });
      return 'shared';
    } catch (e) {
      if ((e as Error | undefined)?.name === 'AbortError') return 'shared';
    }
  }

  return 'sheet';
}


function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.readAsDataURL(blob);
  });
}

/**
 * Straight into a Telegram story: the share card, a caption and a link sticker that
 * opens the song in the Mini App. Telegram's story editor only takes a URL, so the
 * card goes up to the API for an hour first. False when it could not be done here.
 */
export async function shareTrackToStory(track: Track, coverSrc?: string): Promise<boolean> {
  if (!canShareToStory()) return false;
  const canvas = await drawCard(track, coverSrc, BOT_USERNAME);
  if (!canvas) return false;
  // JPEG: a story is a photo, and a third of the size of the PNG uploads faster.
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.9));
  if (!blob) return false;
  const { url } = await post<{ url: string }>('/v1/share/story', { image: await blobToBase64(blob) });
  shareToStory(url, {
    text: `${track.title} — ${artistNames(track) || ''} 🎧 @${BOT_USERNAME}`.slice(0, 200),
    widget_link: { url: buildShareUrl(track.id), name: 'Noax' },
  });
  return true;
}
