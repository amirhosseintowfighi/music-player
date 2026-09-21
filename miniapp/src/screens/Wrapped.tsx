import { useNavigate } from 'react-router-dom';

import { useWrapped } from '@/api/social';
import { TrackRow } from '@/components/TrackRow';
import { EmptyState, ErrorNote, Glass, SectionHead, Spinner } from '@/components/ui';
import { useI18n } from '@/i18n';
import { formatYear } from '@/lib/format';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

function Headline({ value, label }: { value: string; label: string }) {
  return (
    <Glass className="p-4 text-center">
      <p className="text-[26px] font-bold tabular-nums">{value}</p>
      <p className="mt-0.5 text-[12px] text-[var(--ink-dim)]">{label}</p>
    </Glass>
  );
}

export function Wrapped() {
  const { t, n, lang } = useI18n();
  const navigate = useNavigate();
  const wrapped = useWrapped();
  const play = usePlayer((s) => s.play);
  const tracks = wrapped.data?.tracks ?? [];
  const thumbs = useThumbs(tracks.map((track) => track.id));

  if (wrapped.isError) return <ErrorNote onRetry={() => void wrapped.refetch()} />;
  if (wrapped.isLoading || !wrapped.data) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  const data = wrapped.data;
  if (data.plays === 0) {
    return (
      <div className="px-4 pt-6">
        <EmptyState title={t('wrapped.title', { year: formatYear(data.year, lang) })} body={t('wrapped.empty')} />
      </div>
    );
  }

  return (
    <div className="px-4 pb-28 pt-4">
      <div className="mb-4 rounded-2xl bg-gradient-to-br from-[#7b5cff] via-[#c44bd6] to-[#ff6b9a] p-6 text-center text-white">
        <p className="text-[13px] opacity-90">{t('wrapped.title', { year: formatYear(data.year, lang) })}</p>
        <p className="mt-1 text-[34px] font-bold tabular-nums">{n(data.minutes)}</p>
        <p className="text-[13px] opacity-90">{t('wrapped.minutes', { count: data.minutes })}</p>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Headline value={n(data.plays)} label={t('wrapped.plays', { count: data.plays })} />
        <Headline
          value={n(data.unique_tracks)}
          label={t('wrapped.uniqueTracks', { count: data.unique_tracks })}
        />
        <Headline
          value={n(data.active_days)}
          label={t('wrapped.activeDays', { count: data.active_days })}
        />
      </div>

      {data.busiest_day && (
        <p className="mt-3 text-center text-[12.5px] text-[var(--ink-dim)]">
          {t('wrapped.busiest', { day: data.busiest_day.day, count: data.busiest_day.plays })}
        </p>
      )}

      {data.top_artists.length > 0 && (
        <>
          <SectionHead title={t('wrapped.topArtists')} />
          <Glass className="p-1.5">
            {data.top_artists.map((artist, index) => (
              <button
                key={artist.id}
                type="button"
                onClick={() => navigate(`/artist/${artist.id}`)}
                className="flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-start"
              >
                <span className="w-5 text-[15px] font-bold text-[var(--accent)]">{index + 1}</span>
                <span className="min-w-0 flex-1 truncate text-[14px]">{artist.name}</span>
                <span className="text-[11.5px] text-[var(--ink-faint)]">{n(artist.plays)}</span>
              </button>
            ))}
          </Glass>
        </>
      )}

      {tracks.length > 0 && (
        <>
          <SectionHead title={t('wrapped.topTracks')} />
          <Glass className="p-1.5">
            {tracks.map((track, index) => (
              <TrackRow
                key={track.id}
                track={track}
                {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
                onPlay={() => void play({ queue: tracks, index, source: 'library' })}
              />
            ))}
          </Glass>
        </>
      )}
    </div>
  );
}
