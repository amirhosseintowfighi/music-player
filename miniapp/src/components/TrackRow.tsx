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
    <div
      className={cx(
        'flex items-center gap-3 rounded-[12px] px-2 py-2 transition-colors',
        isCurrent && 'bg-white/6',
      )}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-start" onClick={onPlay}>
        <Cover src={thumb} seed={track.id} size={46} />
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
          <span className="block truncate text-[12px] text-[var(--ink-faint)]">
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
        className="shrink-0 p-1.5 text-[var(--ink-faint)]"
        onClick={() => (onMore ? onMore() : openActions(track))}
      >
        <MoreIcon size={18} />
      </button>
    </div>
  );
});
