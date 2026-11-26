import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useListeningStats, type ListeningStats } from '@/api/stats';
import { ChevronIcon } from '@/components/icons';
import { TrackRow } from '@/components/TrackRow';
import { EmptyState, Glass, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { usePack } from '@/i18n/pack';
import { statsStrings } from '@/i18n/packs/stats';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

type Tp = ReturnType<typeof usePack<keyof typeof statsStrings.fa>>;

/**
 * One series of bars: magnitude in a single hue, rounded data-ends on the baseline,
 * a 2px gap between bars, a recessive baseline and no grid. Tapping (or hovering) a
 * bar shows its value; only the tallest is labelled up front. A table of the same
 * numbers is there for screen readers.
 */
function BarChart({
  values,
  labels,
  format,
  caption,
  showEvery = 1,
}: {
  values: number[];
  labels: string[];
  format: (value: number) => string;
  caption: string;
  showEvery?: number;
}) {
  const { dir } = useI18n();
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(1, ...values);
  const peak = values.indexOf(Math.max(...values));
  const shown = active ?? (values[peak] ? peak : null);

  return (
    <figure className="m-0">
      <div className="relative h-36" dir="ltr" onMouseLeave={() => setActive(null)}>
        <div className="absolute inset-x-0 bottom-0 h-px bg-[var(--separator)]" aria-hidden />
        <div className="absolute inset-0 flex items-end gap-[2px]">
          {values.map((value, index) => {
            const height = value > 0 ? Math.max(4, (value / max) * 100) : 0;
            return (
              <button data-focusable
                key={index}
                type="button"
                aria-label={`${labels[index] ?? ''}: ${format(value)}`}
                onMouseEnter={() => setActive(index)}
                onFocus={() => setActive(index)}
                onClick={() => setActive(index)}
                // The hit target is the whole column, not just the bar.
                className="group relative flex h-full min-w-0 flex-1 items-end outline-none"
              >
                <span
                  className={cx(
                    'block w-full rounded-t-[4px] transition-opacity',
                    active !== null && active !== index ? 'opacity-45' : 'opacity-100',
                  )}
                  style={{ height: `${height}%`, background: 'var(--accent)' }}
                />
                {shown === index && value > 0 && (
                  <span
                    dir={dir}
                    className="pointer-events-none absolute left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md bg-[var(--bg-1,#222)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--ink)] shadow"
                    style={{ bottom: `calc(${height}% + 4px)` }}
                  >
                    {format(value)}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>
      <div className="mt-1.5 flex gap-[2px] text-[10px] text-[var(--ink-faint)]" dir="ltr" aria-hidden>
        {labels.map((label, index) => (
          <span key={index} className="min-w-0 flex-1 truncate text-center">
            {index % showEvery === 0 ? label : ''}
          </span>
        ))}
      </div>
      <table className="sr-only">
        <caption>{caption}</caption>
        <tbody>
          {values.map((value, index) => (
            <tr key={index}>
              <th scope="row">{labels[index]}</th>
              <td>{format(value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <Glass className="flex flex-col p-3">
      <span className="text-[20px] font-extrabold tabular-nums">{value}</span>
      <span className="text-[11.5px] text-[var(--ink-dim)]">{label}</span>
    </Glass>
  );
}

function Trend({ stats, tp }: { stats: ListeningStats; tp: Tp }) {
  if (stats.previous_minutes === 0) return <span>{stats.minutes > 0 ? tp('stats.new') : ''}</span>;
  const pct = Math.round(((stats.minutes - stats.previous_minutes) / stats.previous_minutes) * 100);
  if (pct === 0) return <span>{tp('stats.same')}</span>;
  return (
    <span>
      {pct > 0 ? '▲ ' : '▼ '}
      {pct > 0 ? tp('stats.up', { pct }) : tp('stats.down', { pct: Math.abs(pct) })}
    </span>
  );
}

function Body({ stats }: { stats: ListeningStats }) {
  const { lang, n } = useI18n();
  const tp = usePack(statsStrings);
  const play = usePlayer((s) => s.play);
  const thumbs = useThumbs(stats.top_tracks.map((entry) => entry.track.id));
  const locale = lang === 'fa' ? 'fa-IR' : 'en-GB';
  const start = new Date(`${stats.start}T12:00:00`);
  const dayLabels = stats.daily_minutes.map((_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return stats.days === 7
      ? new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(day)
      : new Intl.DateTimeFormat(locale, { day: 'numeric' }).format(day);
  });
  const hourLabels = stats.hourly_plays.map((_, hour) => n(hour));
  const sourceTotal = Object.values(stats.sources).reduce((sum, value) => sum + value, 0) || 1;
  const queue = stats.top_tracks.map((entry) => entry.track);

  if (stats.plays === 0) return <EmptyState title={tp('stats.empty')} />;

  return (
    <>
      <Glass className="mt-4 p-4">
        <p className="text-[40px] font-extrabold leading-none tabular-nums">{n(stats.minutes)}</p>
        <p className="mt-1 text-[13px] text-[var(--ink-dim)]">{tp('stats.minutes')}</p>
        <p className="mt-2 text-[12px] text-[var(--ink-dim)]">
          <Trend stats={stats} tp={tp} />
        </p>
      </Glass>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <Tile value={n(stats.plays)} label={tp('stats.plays')} />
        <Tile value={n(stats.songs)} label={tp('stats.songs')} />
        <Tile value={n(stats.artists)} label={tp('stats.artists')} />
      </div>

      <h2 className="mt-6 text-[15px] font-bold">{tp('stats.daily')}</h2>
      <Glass className="mt-2 p-3">
        <BarChart
          values={stats.daily_minutes}
          labels={dayLabels}
          format={(value) => tp('stats.minutesValue', { count: value })}
          caption={tp('stats.daily')}
          showEvery={stats.days === 7 ? 1 : 5}
        />
      </Glass>

      <h2 className="mt-6 text-[15px] font-bold">{tp('stats.hourly')}</h2>
      <Glass className="mt-2 p-3">
        <BarChart
          values={stats.hourly_plays}
          labels={hourLabels}
          format={(value) => tp('stats.playsValue', { count: value })}
          caption={tp('stats.hourly')}
          showEvery={6}
        />
      </Glass>

      {stats.top_tracks.length > 0 && (
        <>
          <h2 className="mt-6 text-[15px] font-bold">{tp('stats.topTracks')}</h2>
          <Glass className="mt-2 p-1.5">
            {stats.top_tracks.map((entry, index) => (
              <TrackRow
                key={entry.track.id}
                track={entry.track}
                thumb={thumbs[entry.track.id] ?? null}
                trailing={tp('stats.timesPlayed', { count: entry.plays })}
                onPlay={() => void play({ queue, index, source: 'library' })}
              />
            ))}
          </Glass>
        </>
      )}

      {stats.top_artists.length > 0 && (
        <>
          <h2 className="mt-6 text-[15px] font-bold">{tp('stats.topArtists')}</h2>
          <Glass className="mt-2 p-3">
            <ol className="flex flex-col gap-2">
              {stats.top_artists.map((artist, index) => (
                <li key={artist.id} className="flex items-center gap-3 text-[13.5px]">
                  <span className="w-5 text-center font-bold text-[var(--ink-faint)]">{n(index + 1)}</span>
                  <span className="min-w-0 flex-1 truncate font-semibold">{artist.name}</span>
                  <span className="text-[12px] tabular-nums text-[var(--ink-dim)]">
                    {tp('stats.playsValue', { count: artist.plays })}
                  </span>
                </li>
              ))}
            </ol>
          </Glass>
        </>
      )}

      <h2 className="mt-6 text-[15px] font-bold">{tp('stats.sources')}</h2>
      <Glass className="mt-2 p-3">
        <ul className="flex flex-col gap-2.5">
          {Object.entries(stats.sources).map(([source, count]) => (
            <li key={source} className="text-[12.5px]">
              <div className="flex justify-between">
                <span>{tp(`stats.source.${source}` as keyof typeof statsStrings.fa)}</span>
                <span className="tabular-nums text-[var(--ink-dim)]">{n(Math.round((count / sourceTotal) * 100))}
                  {lang === 'fa' ? '٪' : '%'}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--fill)]">
                <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${(count / sourceTotal) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      </Glass>
    </>
  );
}

export function StatsScreen() {
  const { t } = useI18n();
  const tp = usePack(statsStrings);
  const navigate = useNavigate();
  const [days, setDays] = useState<7 | 30>(7);
  const stats = useListeningStats(days);

  return (
    <div className="px-4 pb-8 pt-4 tv-safe [container-type:inline-size]">
      <div className="flex items-center gap-2">
        <button data-focusable type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
          <ChevronIcon size={22} className="rtl:rotate-180" />
        </button>
        <h1 className="min-w-0 flex-1 truncate text-[21px] font-bold">📊 {tp('stats.title')}</h1>
        <div className="flex rounded-full bg-[var(--fill)] p-0.5" role="tablist">
          {([7, 30] as const).map((value) => (
            <button data-focusable
              key={value}
              type="button"
              role="tab"
              aria-selected={days === value}
              onClick={() => setDays(value)}
              className={cx(
                'rounded-full px-3 py-1 text-[12px]',
                days === value ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'text-[var(--ink-dim)]',
              )}
            >
              {value === 7 ? tp('stats.week') : tp('stats.month')}
            </button>
          ))}
        </div>
      </div>
      {stats.isLoading ? (
        <div className="grid place-items-center py-20">
          <Spinner />
        </div>
      ) : stats.data ? (
        <Body stats={stats.data} />
      ) : (
        <EmptyState title={t('app.error')} />
      )}
    </div>
  );
}
