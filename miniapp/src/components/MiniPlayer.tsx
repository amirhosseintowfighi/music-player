import { motion } from 'framer-motion';

import { Cover, Spinner, spring } from '@/components/ui';
import { PauseIcon, PlayIcon } from '@/components/icons';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

/** Sticky glass bar above the tab bar; tapping it opens the full player. */
export function MiniPlayer({ thumb }: { thumb?: string | null }) {
  const { t } = useI18n();
  const track = usePlayer((s) => s.current);
  const isPlaying = usePlayer((s) => s.isPlaying);
  const isLoading = usePlayer((s) => s.isLoading);
  const position = usePlayer((s) => s.position);
  const total = usePlayer((s) => s.duration || s.current?.duration || 0);
  const toggle = usePlayer((s) => s.toggle);
  const openPlayer = useUi((s) => s.setPlayerOpen);

  if (!track) return null;
  const progress = total > 0 ? Math.min(100, (position / total) * 100) : 0;

  return (
    <motion.div
      layout
      initial={{ y: 40, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={spring}
      // Swipe sideways for the next or previous track, up to open the full player —
      // the gestures people already use on every other music app.
      drag
      dragDirectionLock
      dragConstraints={{ left: 0, right: 0, top: 0, bottom: 0 }}
      dragElastic={{ left: 0.35, right: 0.35, top: 0.2, bottom: 0 }}
      onDragEnd={(_, info) => {
        const { x, y } = info.offset;
        if (y < -60 && Math.abs(y) > Math.abs(x)) {
          openPlayer(true);
          return;
        }
        if (Math.abs(x) < 70) return;
        haptic('light');
        // RTL and LTR both read "swipe away from where you came from" as next.
        const forward = document.dir === 'rtl' ? x > 0 : x < 0;
        void (forward ? usePlayer.getState().next() : usePlayer.getState().previous());
      }}
      className="glass glass-edge glass-spec glass-strong mb-2.5 flex items-center gap-3 overflow-hidden rounded-[var(--radius-glass)] px-3 py-2.5"
      style={{ ['--spec' as string]: 0.55 }}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-3 text-start"
        onClick={() => openPlayer(true)}
        aria-label={t('player.nowPlaying')}
      >
        <motion.div layoutId="cover">
          <Cover src={thumb} seed={track.id} size={42} />
        </motion.div>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-semibold">{track.title}</span>
          <span className="block truncate text-[11.5px] text-[var(--ink-faint)]">{artistNames(track)}</span>
        </span>
      </button>
      <button
        type="button"
        onClick={() => void toggle()}
        aria-label={isPlaying ? t('common.pause') : t('common.play')}
        className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--ink)] text-[#0b0d12]"
      >
        {isLoading ? <Spinner size={16} /> : isPlaying ? <PauseIcon size={17} /> : <PlayIcon size={17} />}
      </button>
      <div className="absolute inset-x-0 bottom-0 h-[2px] bg-white/10">
        <div className="h-full bg-[var(--accent)] transition-[width] duration-300" style={{ width: `${progress}%` }} />
      </div>
    </motion.div>
  );
}
