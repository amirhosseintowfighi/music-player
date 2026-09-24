import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  flatten,
  useArtist,
  useAlbum,
  useArtistPage,
  useArtistTracks,
  useChannel,
  useChannelTracks,
  useTrack,
} from '@/api/hooks';
import { TrackRow } from '@/components/TrackRow';
import { Cover, ErrorNote, Glass, LoadMore, Spinner } from '@/components/ui';
import { ChevronIcon, PlayIcon, ShuffleIcon } from '@/components/icons';
import { useI18n } from '@/i18n';
import { useThumbs } from '@/player/thumbs';
import { usePlayer, type PlaySource } from '@/store/player';

function Header({
  title,
  subtitle,
  seed,
  glyph,
  thumb,
}: {
  title: string;
  subtitle: string;
  seed: string | number;
  glyph: string;
  thumb?: string;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  return (
    <div className="flex items-center gap-3.5 pt-4">
      <button type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
        <ChevronIcon size={22} className="rtl:rotate-180" />
      </button>
      <Cover src={thumb} seed={seed} size={56} glyph={glyph} />
      <div className="min-w-0">
        <h1 className="truncate text-[18px] font-bold">{title}</h1>
        <p className="truncate text-[12.5px] text-[var(--ink-dim)]">{subtitle}</p>
      </div>
    </div>
  );
}

function TrackList({
  tracks,
  thumbs,
  source,
  sourceId,
}: {
  tracks: ReturnType<typeof flatten<import('@/api/client').Track>>;
  thumbs: Record<number, string>;
  source: PlaySource;
  sourceId: number;
}) {
  const { t } = useI18n();
  const play = usePlayer((s) => s.play);
  const setShuffle = usePlayer((s) => s.setShuffle);

  return (
    <>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={() => void play({ queue: tracks, index: 0, source, sourceId })}
          className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--accent)] py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)]"
        >
          <PlayIcon size={16} />
          {t('common.play')}
        </button>
        <button
          type="button"
          onClick={() => {
            setShuffle(true);
            void play({ queue: tracks, index: Math.floor(Math.random() * Math.max(1, tracks.length)), source, sourceId });
          }}
          className="flex items-center gap-2 rounded-xl bg-[var(--fill)] px-4 py-2.5 text-[13.5px]"
        >
          <ShuffleIcon size={16} />
          {t('player.shuffle')}
        </button>
      </div>
      <Glass className="mt-3 p-1.5">
        {tracks.map((track, index) => (
          <TrackRow
            key={`${track.id}-${index}`}
            track={track}
            {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
            onPlay={() => void play({ queue: tracks, index, source, sourceId })}
          />
        ))}
      </Glass>
    </>
  );
}

export function ChannelScreen() {
  const { t, n } = useI18n();
  const id = Number(useParams().id);
  const channel = useChannel(id);
  const tracksQuery = useChannelTracks(id);
  const tracks = useMemo(() => flatten(tracksQuery.data), [tracksQuery.data]);
  const thumbs = useThumbs(tracks.map((track) => track.id));

  if (channel.isError) return <ErrorNote onRetry={() => void channel.refetch()} />;
  if (!channel.data) return <div className="grid place-items-center py-20"><Spinner /></div>;

  return (
    <div className="px-4">
      <Header
        title={channel.data.title ?? `@${channel.data.username ?? ''}`}
        subtitle={
          channel.data.status === 'indexing'
            ? t('channel.status.indexing', { percent: channel.data.progress_pct })
            : t('channel.status.active', { tracks: channel.data.tracks_count })
        }
        seed={channel.data.username ?? id}
        glyph="📻"
      />
      <TrackList tracks={tracks} thumbs={thumbs} source="channel" sourceId={id} />
      <LoadMore enabled={Boolean(tracksQuery.hasNextPage)} onVisible={() => void tracksQuery.fetchNextPage()} />
      {tracks.length === 0 && tracksQuery.isLoading && (
        <div className="grid place-items-center py-10">
          <Spinner />
        </div>
      )}
      <p className="sr-only">{n(channel.data.tracks_count)}</p>
    </div>
  );
}

export function ArtistScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const id = Number(useParams().id);
  const artist = useArtist(id);
  const page = useArtistPage(id);
  const tracksQuery = useArtistTracks(id);
  const tracks = useMemo(() => flatten(tracksQuery.data), [tracksQuery.data]);
  const top = page.data?.top_tracks ?? [];
  const albums = page.data?.albums ?? [];
  const thumbs = useThumbs([...top, ...tracks].map((track) => track.id));

  if (artist.isError) return <ErrorNote onRetry={() => void artist.refetch()} />;
  if (!artist.data) return <div className="grid place-items-center py-20"><Spinner /></div>;

  return (
    <div className="px-4">
      <Header
        title={artist.data.name}
        subtitle={t('library.count', { count: artist.data.tracks_count })}
        seed={artist.data.name}
        glyph="🎤"
        thumb={artist.data.image_url ?? undefined}
      />

      {top.length > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 text-[14px] font-bold">{t('artist.popular')}</h2>
          {top.map((track, index) => (
            <TrackRow
              key={track.id}
              track={track}
              thumb={thumbs[track.id]}
              trailing={String(index + 1)}
              onPlay={() =>
                void usePlayer
                  .getState()
                  .play({ queue: top, index, source: 'library', sourceId: id })
              }
            />
          ))}
        </section>
      )}

      {albums.length > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 text-[14px] font-bold">{t('artist.albums')}</h2>
          <div className="flex gap-3 overflow-x-auto pb-1">
            {albums.map((album) => (
              <button
                key={album.name}
                type="button"
                className="w-28 shrink-0 text-start"
                onClick={() =>
                  navigate(`/album/${id}/${encodeURIComponent(album.name)}`)
                }
              >
                <Cover seed={album.name} size={112} glyph="💿" />
                <p className="mt-1 truncate text-[12.5px] font-medium">{album.name}</p>
                <p className="truncate text-[11px] text-[var(--ink-dim)]">
                  {album.year ? `${album.year} · ` : ''}
                  {t('library.count', { count: album.tracks })}
                </p>
              </button>
            ))}
          </div>
        </section>
      )}

      <h2 className="mt-5 text-[14px] font-bold">{t('artist.allTracks')}</h2>
      <TrackList tracks={tracks} thumbs={thumbs} source="library" sourceId={id} />
      <LoadMore enabled={Boolean(tracksQuery.hasNextPage)} onVisible={() => void tracksQuery.fetchNextPage()} />
    </div>
  );
}

export function AlbumScreen() {
  const { t } = useI18n();
  const params = useParams();
  const artistId = Number(params.artistId);
  const name = decodeURIComponent(params.name ?? '');
  const album = useAlbum(artistId, name);
  const tracks = useMemo(() => album.data?.items ?? [], [album.data]);
  const thumbs = useThumbs(tracks.map((track) => track.id));

  if (album.isError) return <ErrorNote onRetry={() => void album.refetch()} />;
  if (!album.data) return <div className="grid place-items-center py-20"><Spinner /></div>;

  const year = album.data.year;
  return (
    <div className="px-4">
      <Header
        title={album.data.album.album}
        subtitle={[
          album.data.album.artist_name,
          year ? String(year) : '',
          t('library.count', { count: album.data.album.tracks_count }),
        ]
          .filter(Boolean)
          .join(' · ')}
        seed={album.data.album.album}
        glyph="💿"
        {...(album.data.album.cover_track_id && thumbs[album.data.album.cover_track_id]
          ? { thumb: thumbs[album.data.album.cover_track_id] as string }
          : {})}
      />
      <TrackList tracks={tracks} thumbs={thumbs} source="library" sourceId={artistId} />
    </div>
  );
}

/**
 * One track, opened from a share link (`?startapp=tr_<id>`).
 *
 * Deliberately a real screen and not a modal: a shared link should land somewhere
 * that can be looked at, played, and navigated away from with the back button.
 */
export function TrackScreen() {
  const id = Number(useParams().id);
  const track = useTrack(id);
  const thumbs = useThumbs(track.data ? [track.data.id] : []);

  if (track.isError) return <ErrorNote onRetry={() => void track.refetch()} />;
  if (!track.data) return <div className="grid place-items-center py-20"><Spinner /></div>;

  return (
    <div className="px-4">
      <Header
        title={track.data.title}
        subtitle={track.data.artists.map((artist) => artist.name).join('، ')}
        seed={track.data.id}
        glyph="♫"
        thumb={thumbs[track.data.id]}
      />
      <TrackList tracks={[track.data]} thumbs={thumbs} source="shared" sourceId={track.data.id} />
    </div>
  );
}
