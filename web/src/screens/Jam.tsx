/**
 * Jam: listen together (Spotify's Jam, in Music's clothes).
 *
 * `/jam` starts one or shows the one you are in; `/jam/:code` is where an invite
 * lands (`startapp=jam_<code>`) and joins it.
 */
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { useMe } from '@/api/hooks';
import { CloseIcon, JamIcon, LinkIcon, SendIcon } from '@/components/icons';
import { Cover, EmptyState, Equalizer, PlayPauseGlyph, Spinner, bouncy, cx, easeOut, spring } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames, coverColors } from '@/lib/format';
import { haptic, openTelegramLink } from '@/lib/telegram';
import { useThumbs } from '@/player/thumbs';
import { useJam, type JamMember } from '@/store/jam';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

/** Rings that ripple out from the Jam glyph: something is being broadcast. */
function Pulse({ active }: { active: boolean }) {
  return (
    <div className="relative mx-auto grid h-36 w-36 place-items-center">
      {[0, 1, 2].map((ring) => (
        <motion.span
          key={ring}
          className="absolute inset-0 rounded-full border border-[var(--accent)]"
          initial={{ scale: 0.55, opacity: 0 }}
          animate={active ? { scale: [0.55, 1.25], opacity: [0.55, 0] } : { scale: 0.8, opacity: 0.15 }}
          transition={active ? { duration: 2.4, repeat: Infinity, delay: ring * 0.8, ease: 'easeOut' } : spring}
        />
      ))}
      <motion.div
        className="grid h-20 w-20 place-items-center rounded-full bg-gradient-to-br from-[var(--accent)] to-[var(--accent-2)] text-[var(--accent-ink)] shadow-[0_10px_30px_color-mix(in_oklab,var(--accent)_45%,transparent)]"
        initial={{ scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={bouncy}
      >
        <JamIcon size={38} />
      </motion.div>
    </div>
  );
}

function Avatar({ member, size = 44 }: { member: JamMember; size?: number }) {
  const [c1, c2] = coverColors(member.user_id);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-bold text-white ring-2 ring-[var(--bg-0)]"
      style={{ width: size, height: size, background: `linear-gradient(140deg, ${c1}, ${c2})`, fontSize: size * 0.4 }}
    >
      {[...member.first_name.trim()][0]?.toUpperCase() ?? '♪'}
    </span>
  );
}

/** An on/off switch whose knob travels with a spring, as iOS's does. */
function Switch({ on, onChange, label }: { on: boolean; onChange: (next: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => {
        haptic('select');
        onChange(!on);
      }}
      className={cx(
        'flex h-[31px] w-[51px] shrink-0 rounded-full p-[2px] transition-colors duration-300',
        on ? 'justify-end bg-[#34c759]' : 'justify-start bg-[var(--fill-strong)]',
      )}
      dir="ltr"
    >
      <motion.span layout transition={bouncy} className="block h-[27px] w-[27px] rounded-full bg-white shadow-[0_2px_4px_rgba(0,0,0,0.25)]" />
    </button>
  );
}

function StartJam() {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const toast = useUi((s) => s.toast);
  return (
    <div className="flex flex-col items-center px-6 pt-10 text-center">
      <Pulse active />
      <motion.h1
        className="mt-6 text-[24px] font-bold tracking-tight"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: easeOut, delay: 0.1 }}
      >
        {t('jam.startTitle')}
      </motion.h1>
      <motion.p
        className="mt-3 max-w-xs text-[14px] leading-7 text-[var(--ink-dim)]"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: easeOut, delay: 0.18 }}
      >
        {t('jam.startHint')}
      </motion.p>
      <motion.button
        type="button"
        disabled={busy}
        className="mt-8 inline-flex items-center gap-2 rounded-full bg-[var(--accent)] px-7 py-3.5 text-[15px] font-bold text-[var(--accent-ink)] shadow-[0_8px_24px_color-mix(in_oklab,var(--accent)_40%,transparent)] disabled:opacity-60"
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        whileTap={{ scale: 0.95 }}
        transition={{ duration: 0.5, ease: easeOut, delay: 0.26 }}
        onClick={() => {
          setBusy(true);
          useJam
            .getState()
            .start()
            .catch(() => toast(t('app.error'), 'error'))
            .finally(() => setBusy(false));
        }}
      >
        {busy ? <Spinner size={18} /> : <JamIcon size={20} />}
        {t('jam.start')}
      </motion.button>
    </div>
  );
}

function NowPlayingCard() {
  const { t } = useI18n();
  const jam = useJam((s) => s.jam);
  const items = useJam((s) => s.items);
  const isPlaying = usePlayer((s) => s.isPlaying);
  const isLoading = usePlayer((s) => s.isLoading);
  const track = jam ? items[jam.index]?.track : undefined;
  const thumbs = useThumbs(track ? [track.id] : []);
  if (!jam) return null;
  if (!track) {
    return <p className="rounded-[var(--radius-glass)] bg-[var(--card)] px-4 py-6 text-center text-[13px] text-[var(--ink-dim)]">{t('jam.empty')}</p>;
  }
  const behind = jam.playing && !isPlaying && !isLoading;
  return (
    <div className="relative overflow-hidden rounded-[var(--radius-glass-lg)] bg-[var(--card)] p-3.5">
      <div className="flex items-center gap-3.5">
        <motion.div animate={{ scale: isPlaying ? 1 : 0.92 }} transition={bouncy} className="rounded-[10px] shadow-[0_6px_18px_rgba(0,0,0,0.3)]">
          <Cover src={thumbs[track.id]} seed={track.id} size={64} radius={10} />
        </motion.div>
        <div className="min-w-0 flex-1">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.div
              key={`${track.id}-${jam.index}`}
              initial={{ opacity: 0, x: 16 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -16 }}
              transition={{ duration: 0.3, ease: easeOut }}
            >
              <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--accent)]">
                <Equalizer playing={jam.playing} size={10} />
                {t('player.nowPlaying')}
              </p>
              <p className="mt-0.5 truncate text-[15px] font-bold">{track.title}</p>
              <p className="truncate text-[12.5px] text-[var(--ink-dim)]">{artistNames(track)}</p>
            </motion.div>
          </AnimatePresence>
        </div>
        <motion.button
          type="button"
          aria-label={isPlaying ? t('common.pause') : t('common.play')}
          whileTap={{ scale: 0.85 }}
          transition={bouncy}
          onClick={() => void usePlayer.getState().toggle()}
          className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-[var(--fill)]"
        >
          {isLoading ? <Spinner size={18} /> : <PlayPauseGlyph playing={isPlaying} size={24} />}
        </motion.button>
      </div>
      <AnimatePresence>
        {behind && (
          <motion.button
            type="button"
            className="mt-3 w-full rounded-xl bg-[var(--accent)] py-2.5 text-[14px] font-bold text-[var(--accent-ink)]"
            initial={{ opacity: 0, height: 0, marginTop: 0 }}
            animate={{ opacity: 1, height: 'auto', marginTop: 12 }}
            exit={{ opacity: 0, height: 0, marginTop: 0 }}
            transition={spring}
            onClick={() => void usePlayer.getState().toggle()}
          >
            {t('jam.listenAlong')}
          </motion.button>
        )}
      </AnimatePresence>
    </div>
  );
}

function Members() {
  const { t } = useI18n();
  const jam = useJam((s) => s.jam);
  const me = useMe();
  if (!jam) return null;
  return (
    <div className="no-scrollbar -mx-4 flex gap-4 overflow-x-auto px-4 py-1">
      <AnimatePresence initial={false}>
        {jam.members.map((member) => (
          <motion.div
            key={member.user_id}
            layout
            className="flex w-16 shrink-0 flex-col items-center gap-1.5"
            initial={{ opacity: 0, scale: 0.4 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.4 }}
            transition={bouncy}
          >
            <span className="relative">
              <Avatar member={member} size={52} />
              {member.is_host && (
                <span className="absolute -bottom-1 start-1/2 -translate-x-1/2 rounded-full bg-[var(--accent)] px-1.5 py-px text-[9px] font-bold text-[var(--accent-ink)] rtl:translate-x-1/2">
                  {t('jam.host')}
                </span>
              )}
            </span>
            <span className="w-full truncate text-center text-[11.5px] text-[var(--ink-dim)]">
              {member.user_id === me.data?.id ? t('jam.you') : member.first_name}
            </span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function Queue() {
  const { t } = useI18n();
  const jam = useJam((s) => s.jam);
  const items = useJam((s) => s.items);
  const me = useMe();
  const upcoming = jam ? items.map((item, index) => ({ item, index })).slice(jam.index + 1) : [];
  const thumbs = useThumbs(upcoming.slice(0, 60).map(({ item }) => item.track.id));
  if (!jam || upcoming.length === 0) return null;
  const names = new Map(jam.members.map((m) => [m.user_id, m.first_name]));
  return (
    <section className="mt-6">
      <h2 className="mb-2 px-1 text-[17px] font-bold">{t('jam.upNext')}</h2>
      <motion.ul layout className="list-group list-rows">
        <AnimatePresence initial={false}>
          {upcoming.map(({ item, index }) => {
            const mine = item.added_by === me.data?.id;
            const canRemove = jam.is_host || mine;
            return (
              <motion.li
                key={`${item.track.id}-${index}`}
                layout
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: -40 }}
                transition={spring}
                className="flex items-center gap-3 px-3 py-2"
              >
                <button
                  type="button"
                  disabled={!jam.can_control}
                  className="flex min-w-0 flex-1 items-center gap-3 text-start"
                  onClick={() => void useJam.getState().control('jump', { index })}
                >
                  <Cover src={thumbs[item.track.id]} seed={item.track.id} size={44} radius={6} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14px] font-semibold">{item.track.title}</span>
                    <span className="block truncate text-[12px] text-[var(--ink-dim)]">
                      {artistNames(item.track)}
                      {item.added_by !== jam.host_id && (
                        <span className="text-[var(--accent)]">
                          {' · '}
                          {t('jam.addedBy', { name: mine ? t('jam.you') : names.get(item.added_by) ?? '—' })}
                        </span>
                      )}
                    </span>
                  </span>
                </button>
                {canRemove && (
                  <motion.button
                    type="button"
                    aria-label={t('common.delete')}
                    whileTap={{ scale: 0.8 }}
                    className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[var(--ink-faint)] active:bg-[var(--fill)]"
                    onClick={() => void useJam.getState().remove(index)}
                  >
                    <CloseIcon size={16} />
                  </motion.button>
                )}
              </motion.li>
            );
          })}
        </AnimatePresence>
      </motion.ul>
    </section>
  );
}

function InJam() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const jam = useJam((s) => s.jam);
  const toast = useUi((s) => s.toast);
  if (!jam) return null;

  const invite = () => {
    haptic('light');
    openTelegramLink(
      `https://t.me/share/url?url=${encodeURIComponent(jam.share_url)}&text=${encodeURIComponent(t('jam.shareText'))}`,
    );
  };

  return (
    <div className="px-4 pt-4">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-[26px] font-bold tracking-tight">
            {t('jam.title')}
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[color-mix(in_oklab,var(--accent)_15%,transparent)] px-2 py-0.5 text-[11px] font-bold text-[var(--accent)]">
              <span className="live-dot" />
              {t('jam.live')}
            </span>
          </h1>
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.p
              key={jam.members.length}
              className="text-[13px] text-[var(--ink-dim)]"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
            >
              {t('jam.listeners', { count: jam.members.length })}
            </motion.p>
          </AnimatePresence>
        </div>
        <span className="font-mono text-[12px] tracking-[0.2em] text-[var(--ink-faint)]" dir="ltr">
          {jam.code.toUpperCase()}
        </span>
      </header>

      <Members />

      <div className="mt-4 grid grid-cols-2 gap-2">
        <motion.button
          type="button"
          whileTap={{ scale: 0.96 }}
          onClick={invite}
          className="flex items-center justify-center gap-2 rounded-xl bg-[var(--accent)] py-3 text-[14px] font-bold text-[var(--accent-ink)]"
        >
          <SendIcon size={18} />
          {t('jam.invite')}
        </motion.button>
        <motion.button
          type="button"
          whileTap={{ scale: 0.96 }}
          onClick={() => {
            void navigator.clipboard?.writeText(jam.share_url);
            haptic('success');
            toast(t('jam.copied'), 'success');
          }}
          className="flex items-center justify-center gap-2 rounded-xl bg-[var(--card)] py-3 text-[14px] font-semibold"
        >
          <LinkIcon size={18} />
          {t('jam.copy')}
        </motion.button>
      </div>

      <div className="mt-5">
        <NowPlayingCard />
      </div>

      {jam.is_host && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-[var(--radius-glass)] bg-[var(--card)] px-3.5 py-3">
          <div className="min-w-0">
            <p className="text-[14px]">{t('jam.guestsControl')}</p>
            <p className="mt-0.5 text-[11.5px] text-[var(--ink-faint)]">{t('jam.guestsControlHint')}</p>
          </div>
          <Switch
            label={t('jam.guestsControl')}
            on={jam.guests_can_control}
            onChange={(on) => void useJam.getState().setGuestsCanControl(on)}
          />
        </div>
      )}

      <Queue />

      <motion.button
        type="button"
        whileTap={{ scale: 0.97 }}
        className="mt-6 mb-4 w-full rounded-xl bg-[var(--card)] py-3 text-[14px] font-semibold text-[#ff453a]"
        onClick={() => {
          haptic('medium');
          void (jam.is_host ? useJam.getState().end() : useJam.getState().leave());
          navigate('/', { replace: true });
        }}
      >
        {jam.is_host ? t('jam.end') : t('jam.leave')}
      </motion.button>
    </div>
  );
}

export function JamScreen() {
  const { t } = useI18n();
  const { code } = useParams<{ code?: string }>();
  const navigate = useNavigate();
  const jam = useJam((s) => s.jam);
  const [failed, setFailed] = useState(false);
  const tried = useRef<string | null>(null);

  // An invite: join it, unless we are already in that very jam.
  useEffect(() => {
    if (!code || tried.current === code) return;
    tried.current = code;
    if (useJam.getState().jam?.code === code.toLowerCase()) {
      navigate('/jam', { replace: true });
      return;
    }
    useJam
      .getState()
      .join(code)
      .then(() => navigate('/jam', { replace: true }))
      .catch(() => setFailed(true));
  }, [code, navigate]);

  if (code && failed) return <EmptyState title={t('jam.notFound')} cta={t('jam.start')} onCta={() => navigate('/jam', { replace: true })} />;
  if (code && !jam) {
    return (
      <div className="flex flex-col items-center px-6 pt-10 text-center">
        <Pulse active />
        <p className="mt-6 text-[14px] text-[var(--ink-dim)]">{t('jam.joining')}</p>
      </div>
    );
  }
  return jam ? <InJam /> : <StartJam />;
}
