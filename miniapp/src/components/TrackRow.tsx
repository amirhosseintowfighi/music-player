import { memo } from 'react';

import type { Track } from '@/api/client';
import { Cover, cx } from '@/components/ui';
import { HeartIcon, MoreIcon } from '@/components/icons';
import { useToggleLike } from '@/api/playlists';
import { useUi } from '@/store/ui';
import { useI18n } from '@/i18n';
import { artistNames, duration } from '@/lib/format';
import { usePlayer } from '@/store/player';

interface Props {
  track: Track;
  thumb?: string | null;
  trailing?: string;
  onPlay: () => void;
  onMore?: () => void;
}

/** One row in any track list. Memoised: long lists re-render only the active row. */
export const TrackRow = memo(function TrackRow({ track, thumb, trailing, onPlay, onMore }: Props) {
  const { lang, t } = useI18n();
  const isCurrent = usePlayer((s) => s.current?.id === track.id);
  const openActions = useUi((s) => s.setActionTrack);
  const toggleLike = useToggleLike();
  const isPlaying = usePlayer((s) => s.isPlaying && s.current?.id === track.id);

  return (
    // Music's row: artwork, two lines, controls at the trailing edge, and a
    // hairline that starts where the text starts — never under the artwork.
    <div
      className={cx(
        'group relative flex items-center gap-3 px-3 py-2 transition-colors',
        "after:pointer-events-none after:absolute after:bottom-0 after:end-3 after:h-px after:bg-[var(--separator)] after:content-['']",
        'after:start-[70px] last:after:hidden',
        isCurrent && 'bg-[var(--fill)]',
      )}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-start" onClick={onPlay}>
        <Cover src={thumb} seed={track.id} size={46} radius={6} />
        <span className="min-w-0 flex-1">
          <span
            className={cx(
              'block truncate text-[14px] font-semibold',
              isCurrent && 'text-[var(--accent)]',
              !track.playable && 'opacity-50',
            )}
          >
            {isPlaying && <span aria-hidden className="me-1 text-[11px]">▮▮</span>}
            {track.title || '—'}
          </span>
          <span className="block truncate text-[12px] text-[var(--ink-dim)]">
            {artistNames(track) || t('search.empty')} · {duration(track.duration, lang)}
          </span>
        </span>
      </button>
      {trailing && <span className="shrink-0 text-[11.5px] text-[var(--ink-faint)]">{trailing}</span>}
      {track.liked && (
        <button
          type="button"
          aria-label={t('player.unlike')}
          className="shrink-0 p-1 text-[var(--accent)]"
          onClick={() => toggleLike.mutate({ trackId: track.id, liked: true })}
        >
          <HeartIcon size={16} filled />
        </button>
      )}
      <button
        type="button"
        aria-label={t('common.more')}
        className="shrink-0 p-1.5 text-[var(--ink-dim)]"
        onClick={() => (onMore ? onMore() : openActions(track))}
      >
        <MoreIcon size={18} />
      </button>
    </div>
  );
});
