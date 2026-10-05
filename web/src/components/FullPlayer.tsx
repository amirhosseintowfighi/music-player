import { AnimatePresence, motion, Reorder } from 'framer-motion';
import { useEffect, useId, useRef, useState } from 'react';

import type { Track } from '@/api/client';
import { CreditsSheet } from '@/components/Credits';
import { DevicesSheet } from '@/components/Devices';
import {
  ChevronIcon,
  ClockIcon,
  DevicesIcon,
  InfoIcon,
  JamIcon,
  LyricsIcon,
  SparkleIcon,
  NextIcon,
  PrevIcon,
  QueueIcon,
  RepeatIcon,
  ShareIcon,
  ShuffleIcon,
  SpeedIcon,
} from '@/components/icons';
import {
  Aurora,
  Cover,
  Glass,
  LoadMore,
  PlayPauseGlyph,
  Sheet,
  Spinner,
  bouncy,
  cx,
  easeOut,
  spring,
  useScrollLock,
  FOCUSABLE_SELECTOR,
} from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames, duration } from '@/lib/format';
import { shareCard } from '@/lib/shareCard';
import { BOT_USERNAME, backButton, haptic, openTelegramLink } from '@/lib/telegram';
import { LyricsView } from '@/components/Lyrics';
import { useAudioSettings } from '@/store/audio';
import { useConnect } from '@/store/connect';
import { hiRes } from '@/player/thumbs';
import { useJam } from '@/store/jam';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];
const SLEEP_OPTIONS = [null, 15, 30, 60] as const;

function Scrubber() {
  const { lang, t } = useI18n();
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
        aria-label={t('player.seek')}
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
        <div className="absolute inset-x-0 top-2.5 h-1 rounded-full bg-[var(--fill-strong)]">
          <div
            className="absolute inset-y-0 start-0 rounded-full bg-[var(--fill-strong)]"
            style={{ width: total > 0 ? `${Math.min(100, (buffered / total) * 100)}%` : 0 }}
          />
          <div
            className="absolute inset-y-0 start-0 rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)]"
            style={{ width: `${ratio * 100}%` }}
          />
          <div
            className="absolute -top-1.5 h-4 w-4 -translate-x-1/2 rounded-full bg-[var(--ink)] shadow-[0_1px_3px_rgba(0,0,0,0.3)] rtl:translate-x-1/2"
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
  const suggested = usePlayer((s) => s.smartIds.includes(track.id));
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
          <span className="flex items-center gap-1 truncate text-[11.5px] text-[var(--ink-faint)]">
            {suggested && <SparkleIcon size={11} className="shrink-0 text-[var(--accent)]" />}
            {artistNames(track)}
          </span>
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

/**
 * Canvas: the artwork, large and soft, drifting slowly behind the player while the
 * music plays. Spotify loops a short video here; we have the cover, so the cover
 * moves — and holds still when the music does.
 */
function Canvas({ src, playing }: { src?: string; playing: boolean }) {
  const enabled = useAudioSettings((s) => s.canvas);
  const lowPerf = useUi((s) => s.lowPerf);
  if (!enabled || !src || lowPerf) return null;
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <motion.img
        key={src}
        src={src}
        alt=""
        className="absolute left-1/2 top-1/2 h-[140vmax] w-[140vmax] max-w-none object-cover"
        style={{ filter: 'blur(38px) saturate(160%)', x: '-50%', y: '-50%' }}
        initial={{ opacity: 0, scale: 1 }}
        animate={
          playing
            ? { opacity: 0.55, scale: [1, 1.18, 1.05, 1], rotate: [0, 6, -4, 0] }
            : { opacity: 0.4 }
        }
        transition={
          playing
            ? { opacity: { duration: 1.2 }, scale: { duration: 28, repeat: Infinity, ease: 'easeInOut' }, rotate: { duration: 28, repeat: Infinity, ease: 'easeInOut' } }
            : { duration: 0.8 }
        }
      />
      <div className="absolute inset-0 bg-[color-mix(in_oklab,var(--bg-0)_45%,transparent)]" />
    </div>
  );
}

/**
 * The big cover, sharp: the list-size thumbnail shows at once, and the full-size one
 * (usually the file's own artwork) fades in over it as soon as it has loaded.
 */
function SharpCover({ small, seed }: { small?: string; seed: number }) {
  const big = hiRes(small);
  const [loaded, setLoaded] = useState<string | null>(null);
  return (
    <div className="relative" style={{ width: 300, height: 300 }}>
      <Cover src={small} seed={seed} size={300} radius={26} glyph="♫" />
      {big && (
        <motion.img
          key={big}
          src={big}
          alt=""
          decoding="async"
          onLoad={() => setLoaded(big)}
          initial={{ opacity: 0 }}
          animate={{ opacity: loaded === big ? 1 : 0 }}
          transition={{ duration: 0.35 }}
          className="absolute inset-0 h-full w-full object-cover"
          style={{ borderRadius: 26 }}
        />
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
  const smart = usePlayer((s) => s.smart);
  const repeat = usePlayer((s) => s.repeat);
  const speed = usePlayer((s) => s.speed);
  const sleepAt = usePlayer((s) => s.sleepAt);
  const autoplay = usePlayer((s) => s.autoplay);
  const [queueOpen, setQueueOpen] = useState(false);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [lyricsOpen, setLyricsOpen] = useState(false);
  const [devicesOpen, setDevicesOpen] = useState(false);
  const [creditsOpen, setCreditsOpen] = useState(false);
  const devices = useConnect((s) => s.devices.length);
  const jamListeners = useJam((s) => s.jam?.members.length ?? 0);
  const inJam = useJam((s) => Boolean(s.jam));
  const openJam = () => {
    haptic('light');
    setOpen(false);
    window.location.hash = '#/jam';
  };

  const fullTitleId = useId();
  const fullPanelRef = useRef<HTMLDivElement>(null);
  useScrollLock(open);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => {
      const panel = fullPanelRef.current;
      if (!panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      if (focusable.length) focusable[0]!.focus();
      else panel.focus();
    });
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Tab') {
        const panel = fullPanelRef.current;
        if (!panel) return;
        const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
        if (focusable.length === 0) { event.preventDefault(); return; }
        const first = focusable[0] as HTMLElement;
        const last = focusable[focusable.length - 1] as HTMLElement;
        const active = document.activeElement as HTMLElement | null;
        if (event.shiftKey) { if (active === first) { event.preventDefault(); last.focus(); } }
        else if (active === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

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
          ref={fullPanelRef}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-labelledby={fullTitleId}
          className="fixed inset-0 z-50 overflow-y-auto"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          style={{ background: 'var(--bg-0)', overscrollBehavior: 'contain' }}
        >
          {/* The one screen Music lets the artwork colour: a soft wash of the cover's
              own palette behind the art, and a solid page everywhere else. */}
          <Aurora />
          <Canvas src={thumbs[track.id]} playing={isPlaying} />
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
              {inJam ? (
                <button
                  type="button"
                  onClick={openJam}
                  className="inline-flex items-center gap-1.5 rounded-full bg-[color-mix(in_oklab,var(--accent)_15%,transparent)] px-2.5 py-1 text-[12px] font-semibold text-[var(--accent)]"
                >
                  <span className="live-dot" />
                  {t('jam.pill', { count: jamListeners })}
                </button>
              ) : (
                <p className="text-[12px] text-[var(--ink-dim)]">{t('player.nowPlaying')}</p>
              )}
              <div className="flex items-center">
                <motion.button
                  type="button"
                  aria-label={t('jam.title')}
                  whileTap={{ scale: 0.85 }}
                  onClick={openJam}
                  className={cx('p-1.5', inJam && 'text-[var(--accent)]')}
                >
                  <JamIcon size={21} />
                </motion.button>
                <motion.button
                  type="button"
                  aria-label={t('player.queue')}
                  whileTap={{ scale: 0.85 }}
                  // In a Jam the queue is the room's, and it lives on the Jam screen.
                  onClick={() => (inJam ? openJam() : setQueueOpen(true))}
                  className="p-1.5"
                >
                  <QueueIcon size={21} />
                </motion.button>
              </div>
            </div>

            {lyricsOpen ? (
              <div className="h-[300px]">
                <LyricsView trackId={track.id} />
              </div>
            ) : (
            <motion.div layoutId="cover" className="mx-auto">
              {/* Music's signature: the artwork steps back while paused and forward
                  again when the music starts, with a shadow that grows with it. */}
              <motion.div
                animate={{
                  scale: isPlaying ? 1 : 0.8,
                  boxShadow: isPlaying ? '0 24px 60px rgba(0,0,0,0.45)' : '0 8px 20px rgba(0,0,0,0.25)',
                }}
                transition={{ type: 'spring', stiffness: 260, damping: 20 }}
                style={{ borderRadius: 26 }}
              >
                <SharpCover small={thumbs[track.id]} seed={track.id} />
              </motion.div>
            </motion.div>
            )}

            <div className="relative mt-7 overflow-hidden">
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={track.id}
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -24 }}
                  transition={{ duration: 0.32, ease: easeOut }}
                >
                  <h1 id={fullTitleId} className="truncate text-[23px] font-bold tracking-tight">{track.title}</h1>
                  <p className="mt-1 truncate text-[14px] text-[var(--ink-dim)]">{artistNames(track)}</p>
                  <ChannelChip track={track} />
                </motion.div>
              </AnimatePresence>
            </div>

            <Scrubber />

            {/* Music's transport is plain glyphs, not filled discs: the artwork is
                the colour on this screen and the controls stay out of its way. */}
            <div dir="ltr" className="mt-2 flex items-center justify-center gap-7">
              <button
                type="button"
                aria-label={smart ? t('player.smartShuffle') : t('player.shuffle')}
                aria-pressed={shuffle}
                // The room's order is the room's: no shuffling or looping one copy of it.
                disabled={inJam}
                onClick={() => {
                  const wasSmart = usePlayer.getState().smart;
                  usePlayer.getState().cycleShuffle();
                  if (!wasSmart && shuffle) useUi.getState().toast(t('player.smartShuffle.on'), 'success');
                }}
                className={cx('relative p-2 disabled:opacity-30', shuffle ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]')}
              >
                <ShuffleIcon size={20} />
                {smart && <SparkleIcon size={11} className="absolute right-0.5 top-0.5" />}
              </button>
              <motion.button
                type="button"
                aria-label="previous"
                whileTap={{ scale: 0.75, x: -4 }}
                transition={bouncy}
                onClick={() => {
                  haptic('light');
                  void usePlayer.getState().previous();
                }}
                className="rounded-full p-2 text-[var(--ink)] active:bg-[var(--fill)]"
              >
                <PrevIcon size={30} />
              </motion.button>
              <motion.button
                type="button"
                aria-label={isPlaying ? t('common.pause') : t('common.play')}
                whileTap={{ scale: 0.82 }}
                transition={bouncy}
                onClick={() => void usePlayer.getState().toggle()}
                className="grid h-16 w-16 place-items-center rounded-full text-[var(--ink)] active:bg-[var(--fill)]"
              >
                {isLoading ? <Spinner size={26} /> : <PlayPauseGlyph playing={isPlaying} size={40} />}
              </motion.button>
              <motion.button
                type="button"
                aria-label="next"
                whileTap={{ scale: 0.75, x: 4 }}
                transition={bouncy}
                onClick={() => {
                  haptic('light');
                  void usePlayer.getState().next();
                }}
                className="rounded-full p-2 text-[var(--ink)] active:bg-[var(--fill)]"
              >
                <NextIcon size={30} />
              </motion.button>
              <button
                type="button"
                aria-label={t('player.repeat')}
                aria-pressed={repeat !== 'off'}
                disabled={inJam}
                onClick={() => usePlayer.getState().cycleRepeat()}
                className={cx('relative p-2 disabled:opacity-30', repeat !== 'off' ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]')}
              >
                <RepeatIcon size={20} />
                {repeat === 'one' && <span className="absolute right-0.5 top-0.5 text-[9px] font-bold">1</span>}
              </button>
            </div>

            <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
              <Glass
                className={cx('flex items-center gap-2 px-3.5 py-2 text-[12.5px]', lyricsOpen && 'text-[var(--accent)]')}
                onClick={() => setLyricsOpen((open) => !open)}
              >
                <LyricsIcon size={16} />
                {t('lyrics.title')}
              </Glass>
              <Glass
                className={cx('flex items-center gap-2 px-3.5 py-2 text-[12.5px]', devices > 0 && 'text-[var(--accent)]')}
                onClick={() => setDevicesOpen(true)}
              >
                <DevicesIcon size={16} />
                {devices > 0 ? t('connect.count', { count: devices }) : t('connect.short')}
              </Glass>
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
              <Glass className="flex items-center gap-2 px-3.5 py-2 text-[12.5px]" onClick={() => setCreditsOpen(true)}>
                <InfoIcon size={16} />
                {t('credits.title')}
              </Glass>
              <Glass
                className="flex items-center gap-2 px-3.5 py-2 text-[12.5px]"
                onClick={async () => {
                  const outcome = await shareCard(track, hiRes(thumbs[track.id]) ?? thumbs[track.id], BOT_USERNAME);
                  if (outcome === 'saved') useUi.getState().toast(t('share.saved'), 'success');
                  if (outcome === 'failed') useUi.getState().toast(t('app.error'), 'error');
                }}
              >
                <ShareIcon size={16} />
                {t('share.short')}
              </Glass>
            </div>
          </motion.div>

          <QueueSheet open={queueOpen} onClose={() => setQueueOpen(false)} thumbs={thumbs} />
          <DevicesSheet open={devicesOpen} onClose={() => setDevicesOpen(false)} />
          <CreditsSheet track={track} open={creditsOpen} onClose={() => setCreditsOpen(false)} />

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
                    speed === value ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)]',
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
                autoplay ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)]',
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
                      ? 'bg-[var(--fill-strong)]'
                      : 'bg-[var(--fill)]',
                  )}
                >
                  {minutes === null ? t('player.sleep.off') : t('player.sleep.minutes', { count: minutes })}
                </button>
              ))}
              <button
                type="button"
                onClick={() => usePlayer.getState().setSleep(0, true)}
                className="rounded-xl bg-[var(--fill)] px-4 py-2 text-[13px]"
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
