import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError, type Track } from '@/api/client';
import {
  ALL_DAYS,
  WEEKDAYS,
  localOffset,
  useAlarmActions,
  useAlarms,
  useWake,
  type Alarm,
  type AlarmInput,
} from '@/api/alarms';
import { useLikedTracks, usePlaylists } from '@/api/playlists';
import { ChevronIcon, PlayIcon, PlusIcon } from '@/components/icons';
import { EmptyState, Glass, Sheet, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { usePack } from '@/i18n/pack';
import { wakeStrings } from '@/i18n/packs/wake';
import { haptic } from '@/lib/telegram';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

type Tp = ReturnType<typeof usePack<keyof typeof wakeStrings.fa>>;

const FADE_SECONDS = 30;

function hhmm(minuteOfDay: number): string {
  return `${String(Math.floor(minuteOfDay / 60)).padStart(2, '0')}:${String(minuteOfDay % 60).padStart(2, '0')}`;
}

function daysLabel(days: number, tp: Tp): string {
  if (days === 0) return tp('wake.once');
  if (days === ALL_DAYS) return tp('wake.everyDay');
  if (days === WEEKDAYS) return tp('wake.weekdays');
  return [0, 1, 2, 3, 4, 5, 6]
    .filter((day) => days & (1 << day))
    .map((day) => tp(`wake.day.${day}` as Parameters<Tp>[0]))
    .join(' ');
}

function Back({ title }: { title: string }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  return (
    <div className="flex items-center gap-2">
      <button type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
        <ChevronIcon size={22} className="rtl:rotate-180" />
      </button>
      <h1 className="text-[21px] font-bold">{title}</h1>
    </div>
  );
}

function Switch({ on, onChange, label }: { on: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={cx('relative h-6 w-10 shrink-0 rounded-full transition-colors', on ? 'bg-[var(--accent)]' : 'bg-[var(--fill-strong)]')}
    >
      <span className={cx('absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all', on ? 'start-[18px]' : 'start-0.5')} />
    </button>
  );
}

function AlarmSheet({ open, onClose, editing }: { open: boolean; onClose: () => void; editing: Alarm | null }) {
  const { t } = useI18n();
  const tp = usePack(wakeStrings);
  const toast = useUi((s) => s.toast);
  const playlists = usePlaylists();
  const { create, update } = useAlarmActions();
  const [time, setTime] = useState('07:00');
  const [days, setDays] = useState(ALL_DAYS);
  const [label, setLabel] = useState('');
  const [source, setSource] = useState<AlarmInput['source']>('likes');
  const [playlistId, setPlaylistId] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setTime(editing ? hhmm(editing.minute_of_day) : '07:00');
    setDays(editing ? editing.days : ALL_DAYS);
    setLabel(editing?.label ?? '');
    setSource(editing?.source ?? 'likes');
    setPlaylistId(editing?.playlist_id ?? null);
  }, [open, editing]);

  const [h, m] = time.split(':').map(Number);
  const body = {
    minute_of_day: (h ?? 7) * 60 + (m ?? 0),
    days,
    tz_offset_minutes: localOffset(),
    label: label.trim() || null,
    source,
    playlist_id: source === 'playlist' ? playlistId : null,
  };
  const pending = create.isPending || update.isPending;
  const onError = (error: unknown) =>
    toast(error instanceof ApiError && error.isPlanLimit ? tp('wake.limit') : t('app.error'), 'error');
  const save = () => {
    const onSuccess = () => {
      toast(tp('wake.saved'), 'success');
      onClose();
    };
    if (editing) update.mutate({ id: editing.id, ...body, enabled: true }, { onSuccess, onError });
    else create.mutate(body, { onSuccess, onError });
  };

  return (
    <Sheet open={open} onClose={onClose} title={editing ? hhmm(editing.minute_of_day) : tp('wake.add')}>
      <label className="block text-[12px] font-semibold text-[var(--ink-faint)]" htmlFor="alarm-time">
        {tp('wake.time')}
      </label>
      <input
        id="alarm-time"
        type="time"
        value={time}
        onChange={(event) => setTime(event.target.value || '07:00')}
        className="mt-1.5 w-full rounded-xl bg-[var(--fill)] px-3.5 py-3 text-center text-[28px] font-bold tabular-nums outline-none"
      />

      <p className="mt-4 text-[12px] font-semibold text-[var(--ink-faint)]">{tp('wake.days')}</p>
      <div className="mt-1.5 flex justify-between gap-1">
        {[0, 1, 2, 3, 4, 5, 6].map((day) => {
          const on = Boolean(days & (1 << day));
          return (
            <button
              key={day}
              type="button"
              aria-pressed={on}
              onClick={() => setDays(days ^ (1 << day))}
              className={cx(
                'grid h-9 w-9 place-items-center rounded-full text-[13px] font-bold',
                on ? 'bg-[var(--accent)] text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-dim)]',
              )}
            >
              {tp(`wake.day.${day}` as Parameters<Tp>[0])}
            </button>
          );
        })}
      </div>
      <p className="mt-1.5 text-[11.5px] text-[var(--ink-faint)]">{daysLabel(days, tp)}</p>

      <p className="mt-4 text-[12px] font-semibold text-[var(--ink-faint)]">{tp('wake.source')}</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {(['likes', 'daylist', 'recent', 'playlist'] as const).map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={source === key}
            onClick={() => setSource(key)}
            className={cx(
              'rounded-full px-3 py-1.5 text-[12.5px]',
              source === key ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-dim)]',
            )}
          >
            {tp(`wake.source.${key}`)}
          </button>
        ))}
      </div>
      {source === 'playlist' && (
        <select
          aria-label={tp('wake.pickPlaylist')}
          value={playlistId ?? ''}
          onChange={(event) => setPlaylistId(event.target.value ? Number(event.target.value) : null)}
          className="mt-2 w-full rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[13.5px] outline-none"
        >
          <option value="">{tp('wake.pickPlaylist')}</option>
          {playlists.data
            ?.filter((playlist) => playlist.is_owner)
            .map((playlist) => (
              <option key={playlist.id} value={playlist.id}>
                {playlist.name}
              </option>
            ))}
        </select>
      )}

      <input
        value={label}
        onChange={(event) => setLabel(event.target.value)}
        maxLength={60}
        placeholder={tp('wake.labelPlaceholder')}
        aria-label={tp('wake.label')}
        className="mt-4 w-full rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[14px] outline-none"
      />

      <button
        type="button"
        disabled={pending || (source === 'playlist' && !playlistId)}
        onClick={save}
        className="mt-4 w-full rounded-xl bg-[var(--accent)] py-3 text-[14px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
      >
        {pending ? <Spinner size={16} /> : t('common.save')}
      </button>
    </Sheet>
  );
}

function AlarmsSection() {
  const { t, lang } = useI18n();
  const tp = usePack(wakeStrings);
  const alarms = useAlarms();
  const { update, remove } = useAlarmActions();
  const [editing, setEditing] = useState<Alarm | null>(null);
  const [open, setOpen] = useState(false);
  const when = (iso: string) =>
    new Intl.DateTimeFormat(lang === 'fa' ? 'fa-IR' : 'en-GB', {
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(iso));

  return (
    <section className="mt-4">
      <div className="flex items-center justify-between">
        <h2 className="text-[16px] font-bold">⏰ {tp('wake.title')}</h2>
        <button
          type="button"
          aria-label={tp('wake.add')}
          onClick={() => {
            setEditing(null);
            setOpen(true);
          }}
          className="grid h-8 w-8 place-items-center rounded-full bg-[var(--accent)] text-[var(--accent-ink)]"
        >
          <PlusIcon size={16} />
        </button>
      </div>
      <p className="mt-1 text-[12px] leading-5 text-[var(--ink-dim)]">{tp('wake.hint')}</p>
      <div className="mt-3 flex flex-col gap-2">
        {alarms.isLoading && <Spinner />}
        {alarms.data?.length === 0 && <p className="py-3 text-center text-[13px] text-[var(--ink-faint)]">{tp('wake.empty')}</p>}
        {alarms.data?.map((alarm) => (
          <Glass key={alarm.id} className="flex items-center gap-3 p-3">
            <button
              type="button"
              className="min-w-0 flex-1 text-start"
              onClick={() => {
                setEditing(alarm);
                setOpen(true);
              }}
            >
              <span className={cx('block text-[26px] font-bold tabular-nums', !alarm.enabled && 'opacity-40')}>
                {hhmm(alarm.minute_of_day)}
              </span>
              <span className="block truncate text-[12px] text-[var(--ink-dim)]">
                {[alarm.label, daysLabel(alarm.days, tp), tp(`wake.source.${alarm.source}`)].filter(Boolean).join(' · ')}
              </span>
              <span className="block text-[11px] text-[var(--ink-faint)]">
                {alarm.enabled && alarm.next_fire_at ? tp('wake.next', { when: when(alarm.next_fire_at) }) : tp('wake.off')}
              </span>
            </button>
            <div className="flex flex-col items-end gap-2">
              <Switch
                on={alarm.enabled}
                label={hhmm(alarm.minute_of_day)}
                onChange={(enabled) => update.mutate({ id: alarm.id, enabled, tz_offset_minutes: localOffset() })}
              />
              <button type="button" className="text-[11.5px] text-[#ff9a9a]" onClick={() => remove.mutate(alarm.id)}>
                {t('common.delete')}
              </button>
            </div>
          </Glass>
        ))}
      </div>
      <AlarmSheet open={open} onClose={() => setOpen(false)} editing={editing} />
    </section>
  );
}

// ── focus mode ────────────────────────────────────────────────────────────────

const PRESETS = [
  { focus: 25, rest: 5 },
  { focus: 50, rest: 10 },
  { focus: 90, rest: 15 },
] as const;
const STATS_KEY = 'focus.stats';

function readStats(): { day: string; count: number; minutes: number } {
  const today = new Date().toDateString();
  try {
    const raw = JSON.parse(localStorage.getItem(STATS_KEY) ?? 'null') as { day: string; count: number; minutes: number } | null;
    if (raw && raw.day === today) return raw;
  } catch {
    /* a private window: start from zero */
  }
  return { day: today, count: 0, minutes: 0 };
}

function writeStats(stats: { day: string; count: number; minutes: number }): void {
  try {
    localStorage.setItem(STATS_KEY, JSON.stringify(stats));
  } catch {
    /* not kept: only today's tally is lost */
  }
}

/** Keeps the screen on while a timer runs, where the browser allows it. */
function useWakeLock(active: boolean) {
  useEffect(() => {
    if (!active) return undefined;
    let lock: { release: () => Promise<void> } | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request: (type: 'screen') => Promise<{ release: () => Promise<void> }> } };
    nav.wakeLock
      ?.request('screen')
      .then((granted) => {
        lock = granted;
      })
      .catch(() => undefined);
    return () => {
      void lock?.release().catch(() => undefined);
    };
  }, [active]);
}

type Phase = 'idle' | 'focus' | 'rest';

function FocusSection() {
  const tp = usePack(wakeStrings);
  const toast = useUi((s) => s.toast);
  const liked = useLikedTracks();
  const player = usePlayer();
  const [preset, setPreset] = useState(0);
  const [music, setMusic] = useState<'current' | 'likes' | 'none'>('likes');
  const [phase, setPhase] = useState<Phase>('idle');
  const [endsAt, setEndsAt] = useState<number | null>(null);
  const [pausedLeft, setPausedLeft] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [stats, setStats] = useState(readStats);
  const chosen = PRESETS[preset] ?? PRESETS[0];
  useWakeLock(phase !== 'idle' && pausedLeft === null);

  useEffect(() => {
    if (phase === 'idle' || pausedLeft !== null) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [phase, pausedLeft]);

  const likedTracks = useMemo<Track[]>(() => liked.data?.pages.flatMap((page) => page.items) ?? [], [liked.data]);
  const startMusic = () => {
    if (music === 'none') return;
    if (music === 'likes' && likedTracks.length > 0) {
      void player.play({ queue: likedTracks, index: Math.floor(Math.random() * likedTracks.length), source: 'library' });
    } else if (!player.isPlaying && player.current) {
      void player.toggle();
    }
  };
  const stopMusic = () => {
    if (usePlayer.getState().isPlaying) void usePlayer.getState().toggle();
  };

  const left = pausedLeft ?? (endsAt ? Math.max(0, endsAt - now) : 0);
  useEffect(() => {
    if (phase === 'idle' || pausedLeft !== null || !endsAt || now < endsAt) return;
    haptic('success');
    if (phase === 'focus') {
      const next = { ...stats, count: stats.count + 1, minutes: stats.minutes + chosen.focus };
      setStats(next);
      writeStats(next);
      toast(tp('focus.done'), 'success');
      stopMusic();
      setPhase('rest');
      setEndsAt(Date.now() + chosen.rest * 60_000);
    } else {
      toast(tp('focus.breakOver'));
      setPhase('focus');
      setEndsAt(Date.now() + chosen.focus * 60_000);
      startMusic();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [now, endsAt, phase, pausedLeft]);

  const begin = () => {
    setPhase('focus');
    setPausedLeft(null);
    setEndsAt(Date.now() + chosen.focus * 60_000);
    setNow(Date.now());
    startMusic();
  };
  const end = () => {
    setPhase('idle');
    setEndsAt(null);
    setPausedLeft(null);
    stopMusic();
  };
  const minutes = Math.floor(left / 60_000);
  const seconds = Math.floor((left % 60_000) / 1000);
  const total = (phase === 'rest' ? chosen.rest : chosen.focus) * 60_000;

  return (
    <section className="mt-8">
      <h2 className="text-[16px] font-bold">🎯 {tp('focus.title')}</h2>
      <p className="mt-1 text-[12px] leading-5 text-[var(--ink-dim)]">{tp('focus.hint')}</p>
      <Glass className="mt-3 p-4">
        {phase === 'idle' ? (
          <>
            <p className="text-[12px] font-semibold text-[var(--ink-faint)]">{tp('focus.preset')}</p>
            <div className="mt-1.5 flex gap-1.5">
              {PRESETS.map((option, index) => (
                <button
                  key={option.focus}
                  type="button"
                  aria-pressed={preset === index}
                  onClick={() => setPreset(index)}
                  className={cx(
                    'flex-1 rounded-xl py-2 text-[13px] tabular-nums',
                    preset === index ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)]',
                  )}
                >
                  {option.focus}/{option.rest}
                </button>
              ))}
            </div>
            <p className="mt-3 text-[12px] font-semibold text-[var(--ink-faint)]">{tp('focus.music')}</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {(['likes', 'current', 'none'] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={music === key}
                  onClick={() => setMusic(key)}
                  className={cx(
                    'rounded-full px-3 py-1.5 text-[12.5px]',
                    music === key ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-dim)]',
                  )}
                >
                  {tp(`focus.music.${key}`)}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={begin}
              className="mt-4 w-full rounded-xl bg-[var(--accent)] py-3 text-[14px] font-bold text-[var(--accent-ink)]"
            >
              {tp('focus.start')}
            </button>
          </>
        ) : (
          <div className="flex flex-col items-center gap-3">
            <p className={cx('text-[13px] font-bold', phase === 'rest' ? 'text-[#2fbf71]' : 'text-[var(--accent)]')}>
              {phase === 'rest' ? tp('focus.break') : tp('focus.working')}
            </p>
            <p className="text-[56px] font-extrabold tabular-nums leading-none" aria-live="off" dir="ltr">
              {String(minutes).padStart(2, '0')}:{String(seconds).padStart(2, '0')}
            </p>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--fill)]">
              <div
                className={cx('h-full', phase === 'rest' ? 'bg-[#2fbf71]' : 'bg-[var(--accent)]')}
                style={{ width: `${100 - (left / total) * 100}%` }}
              />
            </div>
            <div className="flex w-full gap-2">
              <button
                type="button"
                onClick={() => {
                  if (pausedLeft !== null) {
                    setEndsAt(Date.now() + pausedLeft);
                    setPausedLeft(null);
                    setNow(Date.now());
                  } else {
                    setPausedLeft(left);
                  }
                }}
                className="flex-1 rounded-xl bg-[var(--fill)] py-2.5 text-[13.5px]"
              >
                {pausedLeft !== null ? tp('focus.resume') : tp('focus.pause')}
              </button>
              <button type="button" onClick={end} className="flex-1 rounded-xl bg-[var(--fill)] py-2.5 text-[13.5px] text-[#ff9a9a]">
                {tp('focus.end')}
              </button>
            </div>
          </div>
        )}
        <p className="mt-3 text-center text-[11.5px] text-[var(--ink-faint)]">
          {tp('focus.today', { count: stats.count, minutes: stats.minutes })}
        </p>
      </Glass>
    </section>
  );
}

/** Alarms and focus mode: the two ways the app fits into a day. */
export function AlarmsScreen() {
  const tp = usePack(wakeStrings);
  return (
    <div className="px-4 pb-8 pt-4">
      <Back title={`${tp('wake.title')} · ${tp('focus.title')}`} />
      <AlarmsSection />
      <FocusSection />
    </div>
  );
}

/** Opened from the bot's wake-up message: plays the alarm's music, fading in. */
export function WakeScreen() {
  const tp = usePack(wakeStrings);
  const navigate = useNavigate();
  const id = Number(useParams().id);
  const wake = useWake(id);
  const play = usePlayer((s) => s.play);
  const isPlaying = usePlayer((s) => s.isPlaying);
  const setVolume = usePlayer((s) => s.setVolume);
  const target = useRef(usePlayer.getState().volume || 1);
  const fade = useRef<number | undefined>(undefined);
  const [started, setStarted] = useState(false);
  const [clock, setClock] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const start = () => {
    const items = wake.data?.items ?? [];
    if (items.length === 0) return;
    setStarted(true);
    setVolume(0.05);
    void play({ queue: items, index: 0, source: 'library' });
    const began = Date.now();
    window.clearInterval(fade.current);
    fade.current = window.setInterval(() => {
      const share = Math.min(1, (Date.now() - began) / (FADE_SECONDS * 1000));
      setVolume(Math.max(0.05, target.current * share));
      if (share >= 1) window.clearInterval(fade.current);
    }, 500);
  };

  // Try at once; a WebView that wants a tap first gets the big button below.
  const tried = useRef(false);
  useEffect(() => {
    if (tried.current || !wake.data) return;
    tried.current = true;
    start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wake.data]);

  useEffect(() => () => window.clearInterval(fade.current), []);

  const stop = () => {
    window.clearInterval(fade.current);
    if (usePlayer.getState().isPlaying) void usePlayer.getState().toggle();
    setVolume(target.current);
    navigate('/');
  };

  if (wake.isLoading) {
    return (
      <div className="grid place-items-center py-24">
        <Spinner />
      </div>
    );
  }
  if (!wake.data) return <EmptyState title={tp('wake.title')} />;
  const nothing = wake.data.items.length === 0;

  return (
    <div className="flex min-h-[80vh] flex-col items-center justify-center gap-5 px-6 text-center">
      <p className="text-[15px] text-[var(--ink-dim)]">{tp('wake.goodMorning')}</p>
      <p className="text-[64px] font-extrabold tabular-nums leading-none" dir="ltr">
        {String(clock.getHours()).padStart(2, '0')}:{String(clock.getMinutes()).padStart(2, '0')}
      </p>
      {wake.data.alarm.label && <p className="text-[15px] font-semibold">{wake.data.alarm.label}</p>}
      {nothing ? (
        <p className="text-[13px] text-[var(--ink-dim)]">{tp('wake.nothing')}</p>
      ) : (
        !(started && isPlaying) && (
          <button
            type="button"
            onClick={start}
            className="flex items-center gap-2 rounded-full bg-[var(--accent)] px-6 py-3.5 text-[15px] font-bold text-[var(--accent-ink)]"
          >
            <PlayIcon size={18} />
            {tp('wake.tapToStart')}
          </button>
        )
      )}
      <div className="mt-4 flex w-full max-w-xs gap-2">
        <button type="button" onClick={stop} className="flex-1 rounded-xl bg-[var(--fill)] py-3 text-[14px]">
          {tp('wake.stop')}
        </button>
        {!nothing && (
          <button
            type="button"
            onClick={() => {
              window.clearInterval(fade.current);
              setVolume(target.current);
              navigate('/');
            }}
            className="flex-1 rounded-xl bg-[var(--fill)] py-3 text-[14px]"
          >
            {tp('wake.keepPlaying')}
          </button>
        )}
      </div>
    </div>
  );
}
