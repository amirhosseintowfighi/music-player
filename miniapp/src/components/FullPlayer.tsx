import { AnimatePresence, motion, Reorder } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';

import type { Track } from '@/api/client';
import {
  ChevronIcon,
  ClockIcon,
  NextIcon,
  PauseIcon,
  PlayIcon,
  PrevIcon,
  QueueIcon,
  RepeatIcon,
  ShuffleIcon,
  SpeedIcon,
} from '@/components/icons';
import { Cover, Glass, LoadMore, Sheet, Spinner, cx, spring } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames, duration } from '@/lib/format';
import { backButton, haptic, openTelegramLink } from '@/lib/telegram';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];
const SLEEP_OPTIONS = [null, 15, 30, 60] as const;

function Scrubber() {
  const { lang } = useI18n();
  const position = usePlayer((s) => s.position);
  const buffered = usePlayer((s) => s.buffered);
  const total = usePlayer((s) => s.duration || s.current?.duration || 0);
  const seek = usePlayer((s) => s.seek);
  const [dragging, setDragging] = useState<number | null>(null);
  const bar = useRef<HTMLDivElement>(null);

  const shown = dragging ?? position;
  const ratio = total > 0 ? Math.min(1, shown / total) : 0;

  const positionFromEvent = (clientX: number): number => {
    const rect = bar.current?.getBoundingClientRect();
    if (!rect || total <= 0) return 0;
    const raw = (clientX - rect.left) / rect.width;
    const fraction = document.documentElement.dir === 'rtl' ? 1 - raw : raw;
    return Math.max(0, Math.min(1, fraction)) * total;
  };

  return (
    <div className="mt-6">
      <div
        ref={bar}
        role="slider"
        tabIndex={0}
        aria-label="seek"
        aria-valuemin={0}
        aria-valuemax={Math.round(total)}
        aria-valuenow={Math.round(shown)}
        className="relative h-6 cursor-pointer touch-none"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragging(positionFromEvent(event.clientX));
        }}
        onPointerMove={(event) => {
          if (dragging !== null) setDragging(positionFromEvent(event.clientX));
        }}
        onPointerUp={() => {
          if (dragging !== null) {
            seek(dragging);
            haptic('light');
          }
          setDragging(null);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowRight') seek(position + 10);
          if (event.key === 'ArrowLeft') seek(position - 10);
        }}
      >
        <div className="absolute inset-x-0 top-2.5 h-1 rounded-full bg-white/14">
          <div
            className="absolute inset-y-0 start-0 rounded-full bg-white/18"
            style={{ width: total > 0 ? `${Math.min(100, (buffered / total) * 100)}%` : 0 }}
          />
          <div
            className="absolute inset-y-0 start-0 rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)]"
            style={{ width: `${ratio * 100}%` }}
          />
          <div
            className="absolute -top-1.5 h-4 w-4 -translate-x-1/2 rounded-full bg-white shadow-lg rtl:translate-x-1/2"
            style={{ insetInlineStart: `${ratio * 100}%` }}
          />
        </div>
      </div>
      <div className="flex justify-between text-[11px] text-[var(--ink-faint)]">
        <span>{duration(shown, lang)}</span>
        <span>-{duration(Math.max(0, total - shown), lang)}</span>
      </div>
    </div>
  );
}

/**
 * How many upcoming tracks are in the DOM at once.
 *
 * A 500-track queue is normal (a whole album playlist), and rendering it in one go
 * costs a visible stall on a phone. Rows grow in chunks as the user scrolls, and
 * each row is `content-visibility: auto` so the browser skips the ones off screen.
 */
const QUEUE_CHUNK = 40;

function QueueRow({
  track,
  thumb,
  onPlay,
  onRemove,
  active,
}: {
  track: Track;
  thumb?: string;
  onPlay: () => void;
  onRemove?: () => void;
  active?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div
      className="flex items-center gap-3 rounded-xl px-1 py-1.5"
      style={{ contentVisibility: 'auto', containIntrinsicSize: '56px' }}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-start" onClick={onPlay}>
        <Cover src={thumb} seed={track.id} size={38} />
        <span className="min-w-0">
          <span className={cx('block truncate text-[13.5px]', active && 'font-bold text-[var(--accent)]')}>
            {track.title}
          </span>
          <span className="block truncate text-[11.5px] text-[var(--ink-faint)]">{artistNames(track)}</span>
        </span>
      </button>
      {onRemove && (
        <button
          type="button"
          aria-label={t('common.delete')}
          className="p-1 text-[var(--ink-faint)]"
          onClick={onRemove}
        >
          ✕
        </button>
      )}
    </div>
  );
}

/**
 * Two lists, because they behave differently: what the user queued by hand (which
 * they can reorder and remove) and what the source will play afterwards.
 */
function QueueSheet({ open, onClose, thumbs }: { open: boolean; onClose: () => void; thumbs: Record<number, string> }) {
  const { t } = useI18n();
  const queue = usePlayer((s) => s.queue);
  const manual = usePlayer((s) => s.manual);
  const index = usePlayer((s) => s.index);
  const current = usePlayer((s) => s.current);
  const play = usePlayer((s) => s.play);
  const next = usePlayer((s) => s.next);
  const source = usePlayer((s) => s.source);
  const remove = usePlayer((s) => s.removeFromQueue);
  const removeManual = usePlayer((s) => s.removeManual);

  const rest = queue.slice(index + 1);
  const [shown, setShown] = useState(QUEUE_CHUNK);
  const visible = rest.slice(0, shown);

  return (
    <Sheet open={open} onClose={onClose} title={t('player.queue')}>
      {current && (
        <>
          <p className="px-1 pb-1 text-[11.5px] text-[var(--ink-faint)]">{t('player.nowPlaying')}</p>
          <QueueRow track={current} thumb={thumbs[current.id]} onPlay={() => undefined} active />
        </>
      )}

      {manual.length > 0 && (
        <>
          <p className="px-1 pb-1 pt-2 text-[11.5px] text-[var(--ink-faint)]">{t('player.queue.manual')}</p>
          <Reorder.Group
            axis="y"
            values={manual}
            onReorder={(order) => usePlayer.setState({ manual: order })}
            className="flex flex-col gap-1"
          >
            {manual.map((track, i) => (
              <Reorder.Item key={`${track.id}-${i}`} value={track} className="touch-none">
                <QueueRow
                  track={track}
                  thumb={thumbs[track.id]}
                  onPlay={() => {
                    // Everything before it was skipped over, so drop it and move on.
                    usePlayer.setState({ manual: manual.slice(i) });
                    void next();
                  }}
                  onRemove={() => removeManual(i)}
                />
              </Reorder.Item>
            ))}
          </Reorder.Group>
        </>
      )}

      {rest.length > 0 && (
        <>
          <p className="px-1 pb-1 pt-2 text-[11.5px] text-[var(--ink-faint)]">{t('player.upNext')}</p>
          <ul className="flex flex-col gap-1 pb-2">
            {visible.map((track, i) => {
              const queueIndex = index + 1 + i;
              return (
                <li key={`${track.id}-${queueIndex}`}>
                  <QueueRow
                    track={track}
                    thumb={thumbs[track.id]}
                    onPlay={() => void play({ queue, index: queueIndex, source })}
                    onRemove={() => remove(queueIndex)}
                  />
                </li>
              );
            })}
          </ul>
          <LoadMore
            enabled={shown < rest.length}
            onVisible={() => setShown((count) => count + QUEUE_CHUNK)}
          />
        </>
      )}
    </Sheet>
  );
}

/**
 * Credit for the channel that published this track, and a way into it.
 *
 * The channel shown is the one the listener has *not* joined (ADR-003 §2-5): this is
 * the only place in the app where a channel owner gets something back for the music
 * we are indexing, so spending it on a channel the user already has is a waste.
 */
function ChannelChip({ track }: { track: Track }) {
  const { t } = useI18n();
  const channel = track.channel;
  if (!channel?.username) return null;
  const extra = track.channels_count - 1;
  return (
    <div className="mt-2 flex flex-wrap items-center justify-center gap-1.5">
      <button
        type="button"
        onClick={() => {
          haptic('light');
          openTelegramLink(`https://t.me/${channel.username}`);
        }}
        className="inline-flex items-center gap-1 rounded-full bg-[color-mix(in_oklab,var(--accent)_16%,transparent)] px-2.5 py-1 text-[11px] text-[var(--accent)]"
      >
        <span className="truncate max-w-[60vw]">
          {channel.joined
            ? t('player.fromChannel', { name: channel.title })
            : t('player.joinChannel', { name: channel.title })}
        </span>
      </button>
      {extra > 0 && (
        <span className="text-[11px] text-[var(--ink-faint)]">
          {t('player.inChannels', { count: extra })}
        </span>
      )}
    </div>
  );
}

export function FullPlayer({ thumbs }: { thumbs: Record<number, string> }) {
  const { t, lang } = useI18n();
  const open = useUi((s) => s.playerOpen);
  const setOpen = useUi((s) => s.setPlayerOpen);
  const track = usePlayer((s) => s.current);
  const isPlaying = usePlayer((s) => s.isPlaying);
  const isLoading = usePlayer((s) => s.isLoading);
  const shuffle = usePlayer((s) => s.shuffle);
  const repeat = usePlayer((s) => s.repeat);
  const speed = usePlayer((s) => s.speed);
  const sleepAt = usePlayer((s) => s.sleepAt);
  const autoplay = usePlayer((s) => s.autoplay);
  const [queueOpen, setQueueOpen] = useState(false);
  const [optionsOpen, setOptionsOpen] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    backButton(() => setOpen(false));
    return () => backButton(null);
  }, [open, setOpen]);

  if (!track) return null;

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 overflow-y-auto"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          style={{ background: 'color-mix(in oklab, var(--bg-0) 88%, transparent)', backdropFilter: 'blur(30px)' }}
        >
          <motion.div
            className="mx-auto flex min-h-full max-w-lg flex-col px-5"
            style={{ paddingTop: 'calc(18px + var(--safe-top))', paddingBottom: 'calc(24px + var(--safe-bottom))' }}
            drag="y"
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.5 }}
            onDragEnd={(_, info) => {
              if (info.offset.y > 140) setOpen(false);
            }}
            initial={{ y: 60 }}
            animate={{ y: 0 }}
            exit={{ y: 80 }}
            transition={spring}
          >
            <div className="mb-4 flex items-center justify-between">
              <button type="button" aria-label={t('common.back')} onClick={() => setOpen(false)} className="p-1.5">
                <ChevronIcon size={22} className="rotate-90" />
              </button>
              <p className="text-[12px] text-[var(--ink-dim)]">{t('player.nowPlaying')}</p>
              <button type="button" aria-label={t('player.queue')} onClick={() => setQueueOpen(true)} className="p-1.5">
                <QueueIcon size={21} />
              </button>
            </div>

            <motion.div layoutId="cover" className="mx-auto">
              <Cover src={thumbs[track.id]} seed={track.id} size={300} radius={26} glyph="♫" />
            </motion.div>

            <div className="mt-7">
              <h1 className="truncate text-[23px] font-bold tracking-tight">{track.title}</h1>
              <p className="mt-1 truncate text-[14px] text-[var(--ink-dim)]">{artistNames(track)}</p>
              <ChannelChip track={track} />
            </div>

            <Scrubber />

            <div className="mt-2 flex items-center justify-center gap-7">
              <button
                type="button"
                aria-label={t('player.shuffle')}
                aria-pressed={shuffle}
                onClick={() => usePlayer.getState().setShuffle(!shuffle)}
                className={cx('p-2', shuffle ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]')}
              >
                <ShuffleIcon size={20} />
              </button>
              <button type="button" aria-label="previous" onClick={() => void usePlayer.getState().previous()} className="p-2">
                <PrevIcon size={28} />
              </button>
              <button
                type="button"
                aria-label={isPlaying ? t('common.pause') : t('common.play')}
                onClick={() => void usePlayer.getState().toggle()}
                className="grid h-16 w-16 place-items-center rounded-full bg-[var(--ink)] text-[#0b0d12] shadow-[0_16px_34px_-16px_rgba(255,255,255,.55)]"
              >
                {isLoading ? <Spinner size={22} /> : isPlaying ? <PauseIcon size={24} /> : <PlayIcon size={24} />}
              </button>
              <button type="button" aria-label="next" onClick={() => void usePlayer.getState().next()} className="p-2">
                <NextIcon size={28} />
              </button>
              <button
                type="button"
                aria-label={t('player.repeat')}
                aria-pressed={repeat !== 'off'}
                onClick={() => usePlayer.getState().cycleRepeat()}
                className={cx('relative p-2', repeat !== 'off' ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]')}
              >
                <RepeatIcon size={20} />
                {repeat === 'one' && <span className="absolute right-0.5 top-0.5 text-[9px] font-bold">1</span>}
              </button>
            </div>

            <div className="mt-7 flex items-center justify-center gap-3">
              <Glass className="flex items-center gap-2 px-3.5 py-2 text-[12.5px]" onClick={() => setOptionsOpen(true)}>
                <SpeedIcon size={16} />
                {speed}×
              </Glass>
              <Glass
                className={cx(
                  'flex items-center gap-2 px-3.5 py-2 text-[12.5px]',
                  sleepAt !== null && 'text-[var(--accent)]',
                )}
                onClick={() => setOptionsOpen(true)}
              >
                <ClockIcon size={16} />
                {sleepAt ? duration(Math.max(0, (sleepAt - Date.now()) / 1000), lang) : t('player.sleep')}
              </Glass>
            </div>
          </motion.div>

          <QueueSheet open={queueOpen} onClose={() => setQueueOpen(false)} thumbs={thumbs} />

          <Sheet open={optionsOpen} onClose={() => setOptionsOpen(false)} title={t('common.more')}>
            <p className="mb-2 px-1 text-[12.5px] text-[var(--ink-dim)]">{t('player.speed')}</p>
            <div className="mb-5 flex gap-2">
              {SPEEDS.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => usePlayer.getState().setSpeed(value)}
                  className={cx(
                    'flex-1 rounded-xl py-2 text-[13px]',
                    speed === value ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8',
                  )}
                >
                  {value}×
                </button>
              ))}
            </div>
            <p className="mb-2 px-1 text-[12.5px] text-[var(--ink-dim)]">{t('player.autoplay')}</p>
            <button
              type="button"
              onClick={() => usePlayer.getState().setAutoplay(!autoplay)}
              className={cx(
                'mb-5 w-full rounded-xl py-2 text-[13px]',
                autoplay ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8',
              )}
            >
              {autoplay ? t('player.autoplay.on') : t('player.autoplay.off')}
            </button>
            <p className="mb-2 px-1 text-[12.5px] text-[var(--ink-dim)]">{t('player.sleep')}</p>
            <div className="flex flex-wrap gap-2">
              {SLEEP_OPTIONS.map((minutes) => (
                <button
                  key={String(minutes)}
                  type="button"
                  onClick={() => usePlayer.getState().setSleep(minutes)}
                  className={cx(
                    'rounded-xl px-4 py-2 text-[13px]',
                    (minutes === null && !sleepAt) || (minutes !== null && sleepAt)
                      ? 'bg-white/12'
                      : 'bg-white/8',
                  )}
                >
                  {minutes === null ? t('player.sleep.off') : t('player.sleep.minutes', { count: minutes })}
                </button>
              ))}
              <button
                type="button"
                onClick={() => usePlayer.getState().setSleep(0, true)}
                className="rounded-xl bg-white/8 px-4 py-2 text-[13px]"
              >
                {t('player.sleep.endOfTrack')}
              </button>
            </div>
          </Sheet>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
