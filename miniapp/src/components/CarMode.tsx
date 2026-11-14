/**
 * Car mode: the player for a phone in a dashboard mount. Three huge controls, the
 * title in large type, nothing that needs aiming. The screen stays on while it is
 * open (Wake Lock where the browser has it), and it never opens a sheet.
 */
import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

import { useToggleLike } from '@/api/playlists';
import { CloseIcon, HeartIcon, NextIcon, PrevIcon } from '@/components/icons';
import { Cover, PlayPauseGlyph, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

interface WakeLockSentinel {
  release: () => Promise<void>;
}

/** Keeps the screen on while mounted; quietly does nothing where unsupported. */
function useWakeLock(): void {
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    let gone = false;
    const take = async () => {
      const wakeLock = (navigator as Navigator & { wakeLock?: { request: (t: 'screen') => Promise<WakeLockSentinel> } })
        .wakeLock;
      if (!wakeLock || document.visibilityState !== 'visible') return;
      try {
        const taken = await wakeLock.request('screen');
        if (gone) void taken.release();
        else lock = taken;
      } catch {
        /* denied or unsupported: the screen just sleeps as usual */
      }
    };
    void take();
    // The lock is dropped whenever the app is hidden; take it again on return.
    const onVisible = () => void take();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      gone = true;
      document.removeEventListener('visibilitychange', onVisible);
      void lock?.release().catch(() => undefined);
    };
  }, []);
}

const BIG = 'grid place-items-center rounded-[28px] bg-[var(--fill)] text-[var(--ink)] active:bg-[var(--fill-strong)]';

export function CarMode({ thumb }: { thumb?: string | undefined }) {
  const { t } = useI18n();
  const close = useUi((s) => s.setCarMode);
  const track = usePlayer((s) => s.current);
  const isPlaying = usePlayer((s) => s.isPlaying);
  const isLoading = usePlayer((s) => s.isLoading);
  const position = usePlayer((s) => s.position);
  const length = usePlayer((s) => s.duration);
  const toggleLike = useToggleLike();
  // The queue holds its own copy of the track: the heart follows the tap here.
  const [liked, setLiked] = useState<{ id: number; liked: boolean } | null>(null);
  useWakeLock();

  if (!track) return null;
  const isLiked = liked?.id === track.id ? liked.liked : track.liked;
  const progress = length > 0 ? Math.min(1, position / length) : 0;
  return (
    <motion.div
      role="dialog"
      aria-modal="true"
      aria-label={t('car.title')}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[70] flex flex-col bg-[var(--bg-0)] px-5"
      style={{ paddingTop: 'calc(var(--safe-top) + 12px)', paddingBottom: 'calc(var(--safe-bottom, 0px) + 20px)' }}
    >
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-semibold text-[var(--ink-dim)]">🚗 {t('car.title')}</span>
        <button
          type="button"
          aria-label={t('common.close')}
          onClick={() => close(false)}
          className="grid h-14 w-14 place-items-center rounded-full bg-[var(--fill)]"
        >
          <CloseIcon size={24} />
        </button>
      </div>

      <div className="mt-4 flex min-h-0 flex-1 items-center gap-5">
        <Cover src={thumb} seed={track.id} size={112} radius={18} />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-[30px] font-bold leading-tight">{track.title || '—'}</p>
          <p className="mt-1 truncate text-[20px] text-[var(--ink-dim)]">{artistNames(track)}</p>
        </div>
      </div>

      <div className="my-5 h-2 overflow-hidden rounded-full bg-[var(--fill)]" aria-hidden>
        <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${progress * 100}%` }} />
      </div>

      <div className="grid grid-cols-3 gap-3" dir="ltr">
        <button
          type="button"
          aria-label="previous"
          className={cx(BIG, 'h-28')}
          onClick={() => {
            haptic('medium');
            void usePlayer.getState().previous();
          }}
        >
          <PrevIcon size={48} />
        </button>
        <button
          type="button"
          aria-label={isPlaying ? t('common.pause') : t('common.play')}
          className={cx(BIG, 'h-28 bg-[var(--accent)] text-[var(--accent-ink)] active:bg-[var(--accent)]')}
          onClick={() => {
            haptic('medium');
            void usePlayer.getState().toggle();
          }}
        >
          {isLoading ? <Spinner size={40} /> : <PlayPauseGlyph playing={isPlaying} size={56} />}
        </button>
        <button
          type="button"
          aria-label="next"
          className={cx(BIG, 'h-28')}
          onClick={() => {
            haptic('medium');
            void usePlayer.getState().next();
          }}
        >
          <NextIcon size={48} />
        </button>
      </div>
      <button
        type="button"
        aria-pressed={isLiked}
        aria-label={isLiked ? t('player.unlike') : t('player.like')}
        onClick={() => {
          haptic('success');
          toggleLike.mutate({ trackId: track.id, liked: isLiked });
          setLiked({ id: track.id, liked: !isLiked });
        }}
        className={cx(BIG, 'mt-3 h-20 gap-2 text-[17px] font-semibold', isLiked && 'text-[var(--accent)]')}
      >
        <HeartIcon size={30} filled={isLiked} />
      </button>
    </motion.div>
  );
}
