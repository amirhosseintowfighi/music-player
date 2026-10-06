import { Reorder, useDragControls } from 'framer-motion';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError, type Playlist, type Track } from '@/api/client';
import {
  useAddToPlaylist,
  useDeletePlaylist,
  useJoinPlaylist,
  useLikedTracks,
  useMoveInPlaylist,
  usePlaylist,
  usePlaylists,
  useRemoveFromPlaylist,
  useSharedPlaylist,
  useUpdatePlaylist,
} from '@/api/playlists';
import { useArtist } from '@/api/hooks';
import { useFolderActions, useFolders, usePins, usePlaylistRecommendations } from '@/api/listening';
import { ChevronIcon, FolderIcon, PinIcon, PlayIcon, PlusIcon, ShuffleIcon, SparkleIcon } from '@/components/icons';
import { PinButton } from '@/components/PinButton';
import { TrackRow } from '@/components/TrackRow';
import { Cover, EmptyState, Glass, Sheet, Spinner, cx } from '@/components/ui';
import { CreatePlaylistSheet } from '@/components/CreatePlaylistSheet';
import { useI18n } from '@/i18n';
import { duration } from '@/lib/format';
import { haptic, openTelegramLink } from '@/lib/telegram';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

/** A pinned tile: compact, two to a row, above the rest of the library. */
function PinnedTile({ title, glyph, seed, image, onClick }: { title: string; glyph: string; seed: string; image?: string | null; onClick: () => void }) {
  return (
    <Glass className="flex items-center gap-2.5 p-2" onClick={onClick}>
      <Cover src={image} seed={seed} size={40} radius={glyph === '🎤' ? 20 : 8} glyph={glyph} />
      <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{title}</span>
      <PinIcon size={13} filled className="shrink-0 text-[var(--accent)]" />
    </Glass>
  );
}

function PinnedPlaylist({ playlist, playlistId }: { playlist: Playlist | undefined; playlistId: number }) {
  const navigate = useNavigate();
  if (!playlist) return null;
  return (
    <PinnedTile
      title={playlist.name}
      seed={playlist.name}
      glyph={playlist.kind === 'blend' ? '◑' : MADE_FOR_YOU.has(playlist.kind) ? '✦' : '≡'}
      onClick={() => navigate(`/playlist/${playlistId}`)}
    />
  );
}

function PinnedArtist({ artistId }: { artistId: number }) {
  const navigate = useNavigate();
  const artist = useArtist(artistId);
  if (!artist.data) return null;
  return (
    <PinnedTile
      title={artist.data.name}
      seed={artist.data.name}
      image={artist.data.image_url}
      glyph="🎤"
      onClick={() => navigate(`/artist/${artistId}`)}
    />
  );
}

type SortKey = 'recent' | 'alpha' | 'created';
type FilterKey = 'all' | 'mine' | 'made' | 'blend';
const MADE_FOR_YOU = new Set(['discover_weekly', 'daily_mix', 'release_radar', 'daylist']);

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      data-focusable
      onClick={() => {
        haptic('select');
        onClick();
      }}
      className={cx(
        'tv-hit flex shrink-0 items-center gap-1 rounded-full px-3 py-1.5 text-[12.5px] transition-colors duration-200',
        on ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-dim)]',
      )}
    >
      {children}
    </button>
  );
}

export function Playlists() {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const playlists = usePlaylists();
  const folders = useFolders();
  const { remove: removeFolder } = useFolderActions();
  const [createOpen, setCreateOpen] = useState(false);
  const [sort, setSort] = useState<SortKey>('recent');
  const [filter, setFilter] = useState<FilterKey>('all');
  const [folder, setFolder] = useState<number | null>(null);

  const pins = usePins();
  const topLevel = folder === null && filter === 'all';
  const pinnedPlaylists = useMemo(
    () => new Set((pins.data ?? []).filter((pin) => pin.kind === 'playlist').map((pin) => pin.ref_id)),
    [pins.data],
  );

  const shown = useMemo(() => {
    const list = (playlists.data ?? []).filter((playlist) => {
      if (folder !== null) return playlist.folder_id === folder;
      // Pinned ones sit above everything else instead of appearing twice.
      if (filter === 'all' && pinnedPlaylists.has(playlist.id)) return false;
      if (filter === 'mine') return playlist.kind === 'manual' && playlist.is_owner;
      if (filter === 'made') return MADE_FOR_YOU.has(playlist.kind);
      if (filter === 'blend') return playlist.kind === 'blend';
      // Filed playlists live in their folder, not at the top level.
      return filter !== 'all' || !playlist.folder_id;
    });
    const collator = new Intl.Collator(lang);
    return [...list].sort((a, b) =>
      sort === 'alpha'
        ? collator.compare(a.name, b.name)
        : sort === 'created'
          ? (b.created_at ?? '').localeCompare(a.created_at ?? '')
          : b.updated_at.localeCompare(a.updated_at),
    );
  }, [playlists.data, folder, filter, sort, lang, pinnedPlaylists]);
  const openFolder = folders.data?.find((item) => item.id === folder);

  return (
    <div className="px-4 pt-4 tv-safe [container-type:inline-size]">
      <div className="flex items-center justify-between">
        <h1 className="text-[clamp(18px,2.5cqw,22px)] font-bold">{openFolder ? openFolder.name : t('library.playlists')}</h1>
        <button
          type="button"
          aria-label={t('playlist.new')}
          data-focusable
          onClick={() => setCreateOpen(true)}
          className="tv-hit grid h-9 w-9 place-items-center rounded-full bg-[var(--accent)] text-[var(--accent-ink)] transition-transform active:scale-90"
        >
          <PlusIcon size={18} />
        </button>
      </div>

      <div className="tv-row no-scrollbar -mx-1 mt-3 flex gap-2 overflow-x-auto px-1 pb-1">
        {openFolder ? (
          <>
            <Chip on={false} onClick={() => setFolder(null)}>
              <ChevronIcon size={14} className="rotate-180 rtl:rotate-0" />
              {t('library.playlists')}
            </Chip>
            <Chip
              on={false}
              onClick={() => {
                removeFolder.mutate(openFolder.id);
                setFolder(null);
              }}
            >
              {t('folders.delete')}
            </Chip>
          </>
        ) : (
          (['all', 'mine', 'made', 'blend'] as const).map((key) => (
            <Chip key={key} on={filter === key} onClick={() => setFilter(key)}>
              {t(`library.filter.${key}`)}
            </Chip>
          ))
        )}
        <span className="mx-1 w-px shrink-0 bg-[var(--separator)]" />
        {(['recent', 'alpha', 'created'] as const).map((key) => (
          <Chip key={key} on={sort === key} onClick={() => setSort(key)}>
            {t(`library.sort.${key}`)}
          </Chip>
        ))}
      </div>

      <div className="rise mt-3 flex flex-col gap-2">
        {topLevel && (pins.data?.length ?? 0) > 0 && (
          <div className="tv-grid grid grid-cols-2 gap-2 @[560px]:grid-cols-3 @[820px]:grid-cols-4">
            {pins.data?.map((pin) =>
              pin.kind === 'artist' ? (
                <PinnedArtist key={`a-${pin.ref_id}`} artistId={pin.ref_id} />
              ) : (
                <PinnedPlaylist
                  key={`p-${pin.ref_id}`}
                  playlist={playlists.data?.find((playlist) => playlist.id === pin.ref_id)}
                  playlistId={pin.ref_id}
                />
              ),
            )}
          </div>
        )}
        {topLevel && (
          <>
            <Glass className="flex items-center gap-3 p-3" onClick={() => navigate('/likes')}>
              <Cover seed="likes" size={48} glyph="♡" />
              <div>
                <p className="text-[14px] font-bold">{t('library.liked')}</p>
                <p className="text-[11.5px] text-[var(--ink-faint)]">{t('library.likedHint')}</p>
              </div>
            </Glass>
            {folders.data?.map((item) => (
              <Glass key={`folder-${item.id}`} className="flex items-center gap-3 p-3" onClick={() => setFolder(item.id)}>
                <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[12px] bg-[var(--fill-strong)] text-[var(--accent)]">
                  <FolderIcon size={24} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-semibold">{item.name}</p>
                  <p className="text-[11.5px] text-[var(--ink-faint)]">{t('folders.count', { count: item.playlists })}</p>
                </div>
                <ChevronIcon size={18} className="text-[var(--ink-faint)] rtl:rotate-180" />
              </Glass>
            ))}
          </>
        )}

        {shown.map((playlist) => (
          <Glass
            key={playlist.id}
            className="flex items-center gap-3 p-3"
            onClick={() => navigate(`/playlist/${playlist.id}`)}
          >
            <Cover seed={playlist.name} size={48} glyph={playlist.kind === 'blend' ? '◑' : MADE_FOR_YOU.has(playlist.kind) ? '✦' : '≡'} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[14px] font-semibold">{playlist.name}</p>
              <p className="truncate text-[11.5px] text-[var(--ink-faint)]">
                {t('library.count', { count: playlist.tracks_count })}
                {playlist.is_public ? ` · ${t('playlist.shared')}` : ''}
                {playlist.is_owner ? '' : ` · ${t('playlist.collaborating')}`}
              </p>
            </div>
            <ChevronIcon size={18} className="text-[var(--ink-faint)] rtl:rotate-180" />
          </Glass>
        ))}

        {shown.length === 0 && !playlists.isLoading && (
          <EmptyState title={t('playlist.empty')} cta={t('playlist.new')} onCta={() => setCreateOpen(true)} />
        )}
      </div>

      <CreatePlaylistSheet open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

function PlaylistHeader({
  title,
  subtitle,
  tracks,
  source,
  sourceId,
  actions,
}: {
  title: string;
  subtitle: string;
  tracks: Track[];
  source: 'playlist' | 'shared';
  sourceId: number;
  actions?: React.ReactNode;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const play = usePlayer((s) => s.play);
  const setShuffle = usePlayer((s) => s.setShuffle);

  return (
    <>
      <div className="flex items-center gap-3 pt-4">
        <button type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
          <ChevronIcon size={22} className="rtl:rotate-180" />
        </button>
        <Cover seed={title} size={56} glyph="≡" />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[18px] font-bold">{title}</h1>
          <p className="truncate text-[12.5px] text-[var(--ink-dim)]">{subtitle}</p>
        </div>
        {actions}
      </div>
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          disabled={tracks.length === 0}
          onClick={() => void play({ queue: tracks, index: 0, source: 'playlist', sourceId })}
          className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--accent)] py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
        >
          <PlayIcon size={16} />
          {t('common.play')}
        </button>
        <button
          type="button"
          disabled={tracks.length === 0}
          onClick={() => {
            setShuffle(true);
            void play({
              queue: tracks,
              index: Math.floor(Math.random() * Math.max(1, tracks.length)),
              source: source === 'shared' ? 'shared' : 'playlist',
              sourceId,
            });
          }}
          className="flex items-center gap-2 rounded-xl bg-[var(--fill)] px-4 py-2.5 text-[13.5px] disabled:opacity-50"
        >
          <ShuffleIcon size={16} />
          {t('player.shuffle')}
        </button>
      </div>
    </>
  );
}

/** One reorderable row; the drag handle is separate so tapping still plays. */
function ReorderRow({
  track,
  thumb,
  onPlay,
  onRemove,
}: {
  track: Track;
  thumb?: string;
  onPlay: () => void;
  onRemove: () => void;
}) {
  const controls = useDragControls();
  const { t } = useI18n();
  return (
    <Reorder.Item
      value={track}
      dragListener={false}
      dragControls={controls}
      className="flex items-center gap-1"
    >
      <div className="min-w-0 flex-1">
        <TrackRow track={track} {...(thumb ? { thumb } : {})} onPlay={onPlay} />
      </div>
      <button
        type="button"
        aria-label={t('common.delete')}
        className="p-1.5 text-[var(--ink-faint)]"
        onClick={onRemove}
      >
        ✕
      </button>
      <button
        type="button"
        aria-label={t('playlist.reorder')}
        className="cursor-grab touch-none p-1.5 text-[var(--ink-faint)]"
        onPointerDown={(event) => {
          haptic('select');
          controls.start(event);
        }}
      >
        ⋮⋮
      </button>
    </Reorder.Item>
  );
}

/**
 * "Recommended songs" under a playlist the listener can edit (Spotify's Enhance, in
 * Music's clothes): songs that belong with these, one tap to add, refresh for more.
 */
function Recommended({ playlistId, trackIds }: { playlistId: number; trackIds: number[] }) {
  const { t } = useI18n();
  const recs = usePlaylistRecommendations(playlistId, trackIds.length > 0);
  const add = useAddToPlaylist();
  const toast = useUi((s) => s.toast);
  const play = usePlayer((s) => s.play);
  const [added, setAdded] = useState<number[]>([]);
  const items = (recs.data?.items ?? []).filter((track) => !trackIds.includes(track.id));
  const thumbs = useThumbs(items.map((track) => track.id));
  if (items.length === 0) return null;
  return (
    <section className="mt-6">
      <div className="mb-2 flex items-center justify-between px-1">
        <h2 className="flex items-center gap-1.5 text-[16px] font-bold">
          <SparkleIcon size={16} className="text-[var(--accent)]" />
          {t('enhance.title')}
        </h2>
        <button type="button" className="text-[13px] text-[var(--accent)]" onClick={() => void recs.refetch()}>
          {t('enhance.refresh')}
        </button>
      </div>
      <p className="mb-2 px-1 text-[12px] text-[var(--ink-faint)]">{t('enhance.hint')}</p>
      <Glass className="rise p-1.5">
        {items.map((track, index) => (
          <div key={track.id} className="flex items-center">
            <div className="min-w-0 flex-1">
              <TrackRow
                track={track}
                {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
                onPlay={() => void play({ queue: items, index, source: 'discover' })}
              />
            </div>
            <button
              type="button"
              aria-label={t('player.addToPlaylist')}
              disabled={added.includes(track.id)}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-[var(--accent)] transition-transform active:scale-90 disabled:opacity-40"
              onClick={() =>
                add.mutate(
                  { playlistId, trackIds: [track.id] },
                  {
                    onSuccess: () => {
                      haptic('success');
                      setAdded((list) => [...list, track.id]);
                      toast(t('enhance.added'), 'success');
                    },
                  },
                )
              }
            >
              {added.includes(track.id) ? '✓' : <PlusIcon size={18} />}
            </button>
          </div>
        ))}
      </Glass>
    </section>
  );
}

/** Filing a playlist into a folder, from the playlist's own menu. */
function FolderPicker({ playlistId, folderId, onDone }: { playlistId: number; folderId: number | null; onDone: () => void }) {
  const { t } = useI18n();
  const folders = useFolders();
  const { create, file } = useFolderActions();
  const [name, setName] = useState('');
  return (
    <div className="mt-3">
      <p className="mb-2 px-1 text-[12.5px] text-[var(--ink-dim)]">{t('folders.moveTo')}</p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => file.mutate({ playlistId, folderId: null }, { onSuccess: onDone })}
          className={cx('rounded-full px-3 py-1.5 text-[12.5px]', folderId === null ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)]')}
        >
          {t('folders.none')}
        </button>
        {folders.data?.map((folder) => (
          <button
            key={folder.id}
            type="button"
            onClick={() => file.mutate({ playlistId, folderId: folder.id }, { onSuccess: onDone })}
            className={cx(
              'flex items-center gap-1 rounded-full px-3 py-1.5 text-[12.5px]',
              folderId === folder.id ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)]',
            )}
          >
            <FolderIcon size={14} />
            {folder.name}
          </button>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t('folders.new')}
          aria-label={t('folders.new')}
          maxLength={60}
          className="min-w-0 flex-1 rounded-xl bg-[var(--fill)] px-3 py-2 text-[13px] outline-none"
        />
        <button
          type="button"
          disabled={!name.trim() || create.isPending}
          className="rounded-xl bg-[var(--fill-strong)] px-3 text-[13px] disabled:opacity-50"
          onClick={() =>
            create.mutate(name.trim(), {
              onSuccess: (folder) => {
                setName('');
                file.mutate({ playlistId, folderId: folder.id }, { onSuccess: onDone });
              },
            })
          }
        >
          {t('folders.create')}
        </button>
      </div>
    </div>
  );
}

export function PlaylistScreen() {
  const { t, lang } = useI18n();
  const id = Number(useParams().id);
  const query = usePlaylist(id);
  const move = useMoveInPlaylist(id);
  const removeTrack = useRemoveFromPlaylist(id);
  const update = useUpdatePlaylist(id);
  const remove = useDeletePlaylist();
  const navigate = useNavigate();
  const play = usePlayer((s) => s.play);
  const toast = useUi((s) => s.toast);
  const showUpsell = useUi((s) => s.showUpsell);
  const [menuOpen, setMenuOpen] = useState(false);
  const [order, setOrder] = useState<Track[]>([]);

  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  useEffect(() => setOrder(items), [items]);
  const thumbs = useThumbs(items.map((track) => track.id));

  if (query.isLoading) {
    return (
      <div className="grid place-items-center py-20">
        <Spinner />
      </div>
    );
  }
  if (!query.data) return <EmptyState title={t('app.error')} />;
  const playlist = query.data;

  const commitOrder = (next: Track[]) => {
    setOrder(next);
    const moved = next.find((track, index) => items[index]?.id !== track.id);
    if (!moved) return;
    const at = next.findIndex((track) => track.id === moved.id);
    const after = at === 0 ? null : (next[at - 1]?.id ?? null);
    move.mutate({ track_id: moved.id, after_track_id: after });
  };

  return (
    <div className="px-4 tv-safe [container-type:inline-size]">
      <PlaylistHeader
        title={playlist.name}
        subtitle={`${t('library.count', { count: playlist.tracks_count })} · ${duration(playlist.duration_total, lang)}`}
        tracks={items}
        source="playlist"
        sourceId={playlist.id}
        actions={
          <>
            <PinButton kind="playlist" refId={playlist.id} />
            <button type="button" aria-label={t('common.more')} data-focusable onClick={() => setMenuOpen(true)} className="tv-hit p-1.5">
              ⋯
            </button>
          </>
        }
      />

      {items.length === 0 ? (
        <EmptyState title={t('playlist.emptyTracks')} />
      ) : (
        <Glass className="rise mt-3 p-1.5">
          <Reorder.Group axis="y" values={order} onReorder={commitOrder} as="div">
            {order.map((track, index) => (
              <ReorderRow
                key={track.id}
                track={track}
                {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
                onPlay={() => void play({ queue: order, index, source: 'playlist', sourceId: playlist.id })}
                onRemove={() => removeTrack.mutate(track.id)}
              />
            ))}
          </Reorder.Group>
        </Glass>
      )}

      {playlist.can_edit && playlist.kind === 'manual' && (
        <Recommended playlistId={playlist.id} trackIds={items.map((track) => track.id)} />
      )}

      <Sheet open={menuOpen} onClose={() => setMenuOpen(false)} title={playlist.name}>
        {playlist.is_owner && (
          <>
            <FolderPicker playlistId={playlist.id} folderId={playlist.folder_id ?? null} onDone={() => setMenuOpen(false)} />
            <div className="my-3 h-px bg-[var(--separator)]" />
            <button
              type="button"
              className="w-full rounded-xl bg-[var(--fill)] px-4 py-3 text-start text-[13.5px]"
              onClick={() =>
                update.mutate(
                  { is_public: !playlist.is_public },
                  {
                    onSuccess: (updated) => {
                      if (updated.share_url) {
                        void navigator.clipboard?.writeText(updated.share_url);
                        toast(t('playlist.linkCopied'), 'success');
                      }
                    },
                    onError: (error) => {
                      if (error instanceof ApiError && error.isPlanLimit) {
                        setMenuOpen(false);
                        showUpsell({ kind: 'share_playlist', limit: 0 });
                      }
                    },
                  },
                )
              }
            >
              {playlist.is_public ? t('playlist.unshare') : t('playlist.share')}
            </button>
            {playlist.share_url && (
              <button
                type="button"
                className="mt-2 w-full rounded-xl bg-[var(--fill)] px-4 py-3 text-start text-[13.5px]"
                onClick={() =>
                  openTelegramLink(
                    `https://t.me/share/url?url=${encodeURIComponent(playlist.share_url ?? '')}`,
                  )
                }
              >
                {t('playlist.sendLink')}
              </button>
            )}
            <button
              type="button"
              className="mt-2 w-full rounded-xl bg-[var(--fill)] px-4 py-3 text-start text-[13.5px]"
              onClick={() =>
                update.mutate(
                  { is_collaborative: !playlist.is_collaborative },
                  {
                    onError: (error) => {
                      if (error instanceof ApiError && error.isPlanLimit) {
                        setMenuOpen(false);
                        showUpsell({ kind: 'collab_playlist', limit: 0 });
                      }
                    },
                  },
                )
              }
            >
              {playlist.is_collaborative ? t('playlist.collabOff') : t('playlist.collabOn')}
            </button>
            <button
              type="button"
              role="switch"
              aria-checked={playlist.exclude_from_taste ?? false}
              className="mt-2 flex w-full items-center justify-between gap-3 rounded-xl bg-[var(--fill)] px-4 py-3 text-start"
              onClick={() =>
                update.mutate(
                  { exclude_from_taste: !playlist.exclude_from_taste },
                  {
                    onSuccess: (updated) =>
                      toast(updated.exclude_from_taste ? t('taste.excluded') : t('taste.included')),
                    onError: () => toast(t('app.error'), 'error'),
                  },
                )
              }
            >
              <span className="min-w-0">
                <span className="block text-[13.5px]">{t('taste.exclude')}</span>
                <span className="block text-[11.5px] leading-5 text-[var(--ink-faint)]">{t('taste.excludeHint')}</span>
              </span>
              <span
                aria-hidden
                className={cx(
                  'relative h-6 w-10 shrink-0 rounded-full transition-colors',
                  playlist.exclude_from_taste ? 'bg-[var(--accent)]' : 'bg-[var(--fill-strong)]',
                )}
              >
                <span
                  className={cx(
                    'absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all',
                    playlist.exclude_from_taste ? 'start-[18px]' : 'start-0.5',
                  )}
                />
              </span>
            </button>
            <button
              type="button"
              className="mt-2 w-full rounded-xl bg-[var(--fill)] px-4 py-3 text-start text-[13.5px] text-[#ff9a9a]"
              onClick={() =>
                remove.mutate(playlist.id, {
                  onSuccess: () => {
                    toast(t('playlist.deleted'));
                    navigate('/library?tab=playlists');
                  },
                })
              }
            >
              {t('common.delete')}
            </button>
          </>
        )}
        {!playlist.is_owner && <p className="px-1 text-[13px] text-[var(--ink-dim)]">{t('playlist.collaborating')}</p>}
      </Sheet>
    </div>
  );
}

export function SharedPlaylistScreen() {
  const { t, lang } = useI18n();
  const slug = useParams().slug ?? '';
  const query = useSharedPlaylist(slug);
  const join = useJoinPlaylist();
  const toast = useUi((s) => s.toast);
  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const thumbs = useThumbs(items.map((track) => track.id));
  const play = usePlayer((s) => s.play);

  if (query.isLoading) {
    return (
      <div className="grid place-items-center py-20">
        <Spinner />
      </div>
    );
  }
  if (!query.data) return <EmptyState title={t('playlist.notFound')} />;
  const playlist = query.data;

  return (
    <div className="px-4 tv-safe [container-type:inline-size]">
      <PlaylistHeader
        title={playlist.name}
        subtitle={`${t('library.count', { count: playlist.tracks_count })} · ${duration(playlist.duration_total, lang)}`}
        tracks={items}
        source="shared"
        sourceId={playlist.id}
        actions={
          playlist.is_collaborative && !playlist.is_owner ? (
            <button
              type="button"
              className={cx('rounded-full bg-[var(--accent)] px-3 py-1.5 text-[12px] font-bold text-[var(--accent-ink)]')}
              onClick={() => join.mutate(slug, { onSuccess: () => toast(t('playlist.joined'), 'success') })}
            >
              {t('playlist.join')}
            </button>
          ) : null
        }
      />
      <Glass className="rise mt-3 p-1.5">
        {items.map((track, index) => (
          <TrackRow
            key={track.id}
            track={track}
            {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
            onPlay={() => void play({ queue: items, index, source: 'shared', sourceId: playlist.id })}
          />
        ))}
      </Glass>
    </div>
  );
}

export function LikedScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const query = useLikedTracks();
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const thumbs = useThumbs(items.map((track) => track.id));
  const play = usePlayer((s) => s.play);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="px-4 tv-safe [container-type:inline-size]">
      <PlaylistHeader
        title={t('library.liked')}
        subtitle={t('library.count', { count: items.length })}
        tracks={items}
        source="playlist"
        sourceId={0}
        actions={
          <button
            type="button"
            aria-label={t('playlist.new')}
            onClick={() => setAddOpen(true)}
            className="p-1.5 text-[var(--ink-dim)]"
          >
            <PlusIcon size={18} />
          </button>
        }
      />
      {items.length === 0 ? (
        <EmptyState title={t('library.empty')} cta={t('tab.search')} onCta={() => navigate('/search')} />
      ) : (
        <Glass className="rise mt-3 p-1.5">
          {items.map((track, index) => (
            <TrackRow
              key={track.id}
              track={track}
              {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
              onPlay={() => void play({ queue: items, index, source: 'playlist' })}
            />
          ))}
        </Glass>
      )}
      <CreatePlaylistSheet
        open={addOpen}
        onClose={() => setAddOpen(false)}
        trackIds={items.map((track) => track.id)}
      />
    </div>
  );
}
