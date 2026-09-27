import { AnimatePresence, motion } from 'framer-motion';

import { Cover, PlayPauseGlyph, Spinner, bouncy, easeOut, spring } from '@/components/ui';
import { JamIcon, NextIcon, PrevIcon } from '@/components/icons';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { useJam } from '@/store/jam';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

/** A transport glyph that dips when pressed, like Music's. */
function TransportButton({
  label,
  onPress,
  children,
  size = 36,
}: {
  label: string;
  onPress: () => void;
  children: React.ReactNode;
  size?: number;
}) {
  return (
    <motion.button
      type="button"
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation();
        haptic('light');
        onPress();
      }}
      whileTap={{ scale: 0.8 }}
      transition={bouncy}
      className="grid shrink-0 place-items-center rounded-full text-[var(--ink)] active:bg-[var(--fill)]"
      style={{ width: size, height: size }}
    >
      {children}
    </motion.button>
  );
}

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
  const jamListeners = useJam((s) => s.jam?.members.length ?? 0);
  const inJam = useJam((s) => Boolean(s.jam));

  const progress = total > 0 ? Math.min(100, (position / total) * 100) : 0;

  return (
    <AnimatePresence>
      {track && (
        <motion.div
          layout
          initial={{ y: 64, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 64, opacity: 0 }}
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
          // Music stacks the now-playing bar straight onto the tab bar, full width,
          // with one hairline above it. No gap, no corners, nothing floating.
          className="glass glass-strong relative flex items-center gap-1 overflow-hidden border-t border-[var(--separator)] py-2 ps-3 pe-1.5"
          style={{ ['--spec' as string]: 0.55 }}
        >
          <button
            type="button"
            className="flex min-w-0 flex-1 items-center gap-3 text-start"
            onClick={() => openPlayer(true)}
            aria-label={t('player.nowPlaying')}
          >
            <motion.div
              layoutId="cover"
              // The artwork sits back a little while paused, and steps forward when
              // the music starts — the same breath the full player takes.
              animate={{ scale: isPlaying ? 1 : 0.9 }}
              transition={bouncy}
              className="rounded-[8px] shadow-[0_2px_8px_rgba(0,0,0,0.25)]"
            >
              <Cover src={thumb} seed={track.id} size={42} radius={8} />
            </motion.div>
            <span className="relative min-w-0 flex-1 overflow-hidden">
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.span
                  key={track.id}
                  className="block"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  transition={{ duration: 0.28, ease: easeOut }}
                >
                  <span className="block truncate text-[13.5px] font-semibold">{track.title}</span>
                  <span className="flex items-center gap-1.5 truncate text-[11.5px] text-[var(--ink-dim)]">
                    {inJam && (
                      <span className="inline-flex shrink-0 items-center gap-1 font-semibold text-[var(--accent)]">
                        <JamIcon size={12} />
                        {t('jam.pill', { count: jamListeners })}
                        <span aria-hidden>·</span>
                      </span>
                    )}
                    <span className="truncate">{artistNames(track)}</span>
                  </span>
                </motion.span>
              </AnimatePresence>
            </span>
          </button>
          {/* Transport glyphs point along time, which runs left to right in every
              language — so the row keeps that order in RTL too, as Music does. */}
          <div dir="ltr" className="flex shrink-0 items-center">
            <TransportButton label={t('player.previous')} onPress={() => void usePlayer.getState().previous()}>
              <PrevIcon size={20} />
            </TransportButton>
            <TransportButton
              label={isPlaying ? t('common.pause') : t('common.play')}
              onPress={() => void toggle()}
              size={40}
            >
              {isLoading ? <Spinner size={16} /> : <PlayPauseGlyph playing={isPlaying} size={24} />}
            </TransportButton>
            <TransportButton label={t('player.next')} onPress={() => void usePlayer.getState().next()}>
              <NextIcon size={20} />
            </TransportButton>
          </div>
          <div className="absolute inset-x-0 bottom-0 h-[2px] bg-[var(--fill)]">
            <div
              className="h-full bg-[var(--accent)] transition-[width] duration-300 ease-linear"
              style={{ width: `${progress}%` }}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
