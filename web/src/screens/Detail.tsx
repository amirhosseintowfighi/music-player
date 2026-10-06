import { motion } from 'framer-motion';
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
import { useFollowArtist, useRelatedArtists, useThisIs } from '@/api/listening';
import { PinButton } from '@/components/PinButton';
import { TrackRow } from '@/components/TrackRow';
import { Cover, ErrorNote, Glass, LoadMore, Spinner, bouncy, cx } from '@/components/ui';
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

/** Follow an artist: their new music lands in Release Radar and in a notice. */
function FollowButton({ artistId, following }: { artistId: number; following: boolean }) {
  const { t } = useI18n();
  const toggle = useFollowArtist(artistId);
  const on = toggle.isPending ? !following : following;
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.92 }}
      transition={bouncy}
      aria-pressed={on}
      disabled={toggle.isPending}
      onClick={() => toggle.mutate(following)}
      className={cx(
        'shrink-0 rounded-full px-4 py-1.5 text-[12.5px] font-semibold transition-colors',
        on ? 'border border-[var(--separator)] text-[var(--ink)]' : 'bg-[var(--accent)] text-[var(--accent-ink)]',
      )}
    >
      {on ? t('artist.following') : t('artist.follow')}
    </motion.button>
  );
}

/** "Fans also like": other artists this one's listeners play. */
function RelatedArtists({ artistId }: { artistId: number }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const related = useRelatedArtists(artistId);
  const items = related.data ?? [];
  if (items.length === 0) return null;
  return (
    <section className="mt-5">
      <h2 className="mb-2 text-[14px] font-bold">{t('artist.fansAlsoLike')}</h2>
      <div className="tv-row flex gap-3.5 overflow-x-auto pb-1">
        {items.map((other, index) => (
          <motion.button
            key={other.id}
            type="button"
            data-focusable
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(index, 8) * 0.04 }}
            whileTap={{ scale: 0.95 }}
            className="tv-hit w-24 shrink-0 text-center"
            onClick={() => navigate(`/artist/${other.id}`)}
          >
            <Cover src={other.image_url} seed={other.name} size={96} radius={48} glyph="🎤" />
            <p className="mt-1.5 truncate text-[12.5px] font-medium">{other.name}</p>
          </motion.button>
        ))}
      </div>
    </section>
  );
}

/** The "This Is" card on an artist page: their essentials, one tap away. */
function ThisIsCard({ artistId, name, image }: { artistId: number; name: string; image?: string | null }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.98 }}
      onClick={() => navigate(`/this-is/${artistId}`)}
      className="mt-4 flex w-full items-center gap-3.5 overflow-hidden rounded-[var(--radius-glass)] bg-gradient-to-br from-[color-mix(in_oklab,var(--accent)_28%,var(--card))] to-[var(--card)] p-3 text-start"
    >
      <Cover src={image} seed={`this-is-${artistId}`} size={64} radius={10} glyph="★" />
      <span className="min-w-0 flex-1">
        <span className="block text-[11px] font-bold uppercase tracking-wide text-[var(--accent)]">
          {t('thisIs.label')}
        </span>
        <span className="block truncate text-[16px] font-bold">{t('thisIs.title', { name })}</span>
        <span className="block truncate text-[12px] text-[var(--ink-dim)]">{t('thisIs.hint')}</span>
      </span>
      <PlayIcon size={22} className="shrink-0 text-[var(--accent)]" />
    </motion.button>
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
      <Glass className="rise mt-3 p-1.5">
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
    <div className="px-4 tv-safe [container-type:inline-size]">
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
  const thumbs = useThumbs([
    ...top.map((track) => track.id),
    ...albums.map((album) => album.cover_track_id).filter((id): id is number => id != null),
    ...tracks.map((track) => track.id),
  ]);

  if (artist.isError) return <ErrorNote onRetry={() => void artist.refetch()} />;
  if (!artist.data) return <div className="grid place-items-center py-20"><Spinner /></div>;

  return (
    <div className="px-4 tv-safe [container-type:inline-size]">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0 flex-1">
          <Header
            title={artist.data.name}
            subtitle={
              page.data?.followers
                ? `${t('library.count', { count: artist.data.tracks_count })} · ${t('artist.followers', { count: page.data.followers })}`
                : t('library.count', { count: artist.data.tracks_count })
            }
            seed={artist.data.name}
            glyph="🎤"
            thumb={artist.data.image_url ?? undefined}
          />
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <PinButton kind="artist" refId={id} />
          {page.data && <FollowButton artistId={id} following={page.data.following ?? false} />}
        </div>
      </div>

      <ThisIsCard artistId={id} name={artist.data.name} image={artist.data.image_url} />

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
          <div className="tv-row flex gap-3 overflow-x-auto pb-1">
            {albums.map((album) => (
              <button
                key={album.name}
                type="button"
                data-focusable
                className="tv-hit w-28 shrink-0 text-start"
                onClick={() =>
                  navigate(`/album/${id}/${encodeURIComponent(album.name)}`)
                }
              >
                <Cover
                  src={album.cover_track_id ? thumbs[album.cover_track_id] : undefined}
                  seed={album.name}
                  size={112}
                  glyph="💿"
                />
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

      <RelatedArtists artistId={id} />

      <h2 className="mt-5 text-[14px] font-bold">{t('artist.allTracks')}</h2>
      <TrackList tracks={tracks} thumbs={thumbs} source="library" sourceId={id} />
      <LoadMore enabled={Boolean(tracksQuery.hasNextPage)} onVisible={() => void tracksQuery.fetchNextPage()} />
    </div>
  );
}

/** "This Is <artist>": Last.fm's order of their best-known songs, from our archive. */
export function ThisIsScreen() {
  const { t } = useI18n();
  const id = Number(useParams().id);
  const query = useThisIs(id);
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const thumbs = useThumbs(items.map((track) => track.id));

  if (query.isError) return <ErrorNote onRetry={() => void query.refetch()} />;
  if (!query.data) return <div className="grid place-items-center py-20"><Spinner /></div>;
  const artist = query.data.artist;
  return (
    <div className="px-4 tv-safe [container-type:inline-size]">
      <Header
        title={t('thisIs.title', { name: artist.name })}
        subtitle={
          query.data.source === 'lastfm'
            ? `${t('library.count', { count: items.length })} · ${t('thisIs.byLastfm')}`
            : t('library.count', { count: items.length })
        }
        seed={`this-is-${artist.id}`}
        glyph="★"
        thumb={artist.image_url ?? undefined}
      />
      <TrackList tracks={items} thumbs={thumbs} source="playlist" sourceId={artist.id} />
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
    <div className="px-4 tv-safe [container-type:inline-size]">
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
    <div className="px-4 tv-safe [container-type:inline-size]">
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
