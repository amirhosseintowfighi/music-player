import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import type { Track } from '@/api/client';
import { useRecentTracks } from '@/api/playlists';
import {
  flatten,
  useFeaturedChannels,
  useLibraryTracks,
  useMe,
  useMyChannels,
} from '@/api/hooks';
import { ChannelCard } from '@/components/ChannelCard';
import { TrackRow } from '@/components/TrackRow';
import { Cover, EmptyState, ErrorNote, Glass, LoadMore, SectionHead, Spinner } from '@/components/ui';
import { PlayIcon } from '@/components/icons';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

function greetingKey(): 'home.greeting.morning' | 'home.greeting.afternoon' | 'home.greeting.night' {
  const hour = new Date().getHours();
  if (hour < 12) return 'home.greeting.morning';
  if (hour < 19) return 'home.greeting.afternoon';
  return 'home.greeting.night';
}

/** Hero card: the track that is playing, or the most recent one to resume. */
function ContinueCard({ track, thumb, onPlay }: { track: Track; thumb?: string; onPlay: () => void }) {
  const { t } = useI18n();
  const openPlayer = useUi((s) => s.setPlayerOpen);
  const isCurrent = usePlayer((s) => s.current?.id === track.id);
  return (
    <Glass strong className="flex items-center gap-3.5 p-3.5" spec={0.4}>
      <button type="button" className="flex min-w-0 flex-1 items-center gap-3.5 text-start" onClick={() => (isCurrent ? openPlayer(true) : onPlay())}>
        <Cover src={thumb} seed={track.id} size={64} radius={16} glyph="♫" />
        <span className="min-w-0">
          <span className="block truncate text-[16px] font-bold">{track.title}</span>
          <span className="block truncate text-[12.5px] text-[var(--ink-dim)]">{artistNames(track)}</span>
        </span>
      </button>
      <button
        type="button"
        aria-label={t('common.play')}
        onClick={onPlay}
        className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-[var(--accent)] text-[var(--accent-ink)] shadow-[0_10px_22px_-10px_var(--accent)]"
      >
        <PlayIcon size={18} />
      </button>
    </Glass>
  );
}

export function Home() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const me = useMe();
  const channels = useMyChannels();
  const recent = useLibraryTracks();
  const featured = useFeaturedChannels(null);
  const recentlyPlayed = useRecentTracks();
  const play = usePlayer((s) => s.play);
  const current = usePlayer((s) => s.current);

  const tracks = useMemo(() => flatten(recent.data), [recent.data]);
  const played = useMemo(() => recentlyPlayed.data?.items ?? [], [recentlyPlayed.data]);
  const thumbs = useThumbs([...tracks.slice(0, 20), ...played.slice(0, 10)].map((track) => track.id));
  const hero = current ?? played[0] ?? tracks[0];
  const totalTracks = channels.data?.reduce((sum, channel) => sum + channel.tracks_count, 0) ?? 0;

  if (recent.isError) return <ErrorNote onRetry={() => void recent.refetch()} />;

  const hasChannels = (channels.data?.length ?? 0) > 0;

  return (
    <div className="px-4">
      <header className="flex items-center justify-between py-4">
        <div className="min-w-0">
          <h1 className="truncate text-[21px] font-bold">
            {t(greetingKey(), { name: me.data?.first_name ?? '' })}
          </h1>
          {hasChannels && (
            <p className="mt-0.5 text-[12.5px] text-[var(--ink-dim)]">
              {t('home.summary', { channels: channels.data?.length ?? 0, tracks: totalTracks })}
            </p>
          )}
        </div>
        {me.data && (
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#ffb86b] to-[#ff6b9a] text-[15px] font-bold text-[#2a1206]">
            {me.data.first_name.slice(0, 1)}
          </div>
        )}
      </header>

      {!hasChannels && !channels.isLoading && (
        <EmptyState
          title={t('home.empty.title')}
          body={t('home.empty.body')}
          cta={t('home.empty.cta')}
          onCta={() => navigate('/library?add=1')}
        />
      )}

      {hero && (
        <>
          <SectionHead title={t('home.continue')} />
          <ContinueCard
            track={hero}
            {...(thumbs[hero.id] ? { thumb: thumbs[hero.id] as string } : {})}
            onPlay={() => void play({ queue: tracks.length ? tracks : [hero], index: Math.max(0, tracks.findIndex((x) => x.id === hero.id)), source: 'library' })}
          />
        </>
      )}

      {played.length > 0 && (
        <>
          <SectionHead title={t('home.playedRecently')} action={t('home.all')} onAction={() => navigate('/likes')} />
          <div className="no-scrollbar flex gap-3 overflow-x-auto px-0.5 pb-1.5">
            {played.slice(0, 10).map((track, index) => (
              <Glass
                key={track.id}
                as="button"
                className="w-[132px] shrink-0 p-2.5 text-start"
                onClick={() => void play({ queue: played, index, source: 'library' })}
              >
                <Cover src={thumbs[track.id]} seed={track.id} size={112} radius={14} glyph="♫" />
                <p className="mt-2 truncate text-[13px] font-semibold">{track.title}</p>
                <p className="truncate text-[11.5px] text-[var(--ink-faint)]">{artistNames(track)}</p>
              </Glass>
            ))}
          </div>
        </>
      )}

      {hasChannels && (
        <>
          <SectionHead title={t('home.yourChannels')} action={t('home.manage')} onAction={() => navigate('/library?tab=channels')} />
          <div className="no-scrollbar flex gap-3 overflow-x-auto px-0.5 pb-1.5">
            {channels.data?.map((channel) => (
              <ChannelCard key={channel.id} channel={channel} onClick={() => navigate(`/channel/${channel.id}`)} />
            ))}
          </div>
        </>
      )}

      {tracks.length > 0 && (
        <>
          <SectionHead title={t('home.recent')} action={t('home.all')} onAction={() => navigate('/library')} />
          <Glass className="p-1.5">
            {tracks.slice(0, 8).map((track, index) => (
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

      <SectionHead title={t('home.featured')} />
      <div className="no-scrollbar flex gap-3 overflow-x-auto px-0.5 pb-1.5">
        {flatten(featured.data).map((channel) => (
          <ChannelCard key={channel.id} channel={channel} onClick={() => navigate(`/channel/${channel.id}`)} />
        ))}
        {featured.isLoading && <Spinner />}
      </div>
      <LoadMore enabled={Boolean(featured.hasNextPage)} onVisible={() => void featured.fetchNextPage()} />
    </div>
  );
}
