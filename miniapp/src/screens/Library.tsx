import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { ApiError } from '@/api/client';
import {
  flatten,
  useAddChannel,
  useLibraryAlbums,
  useLibraryArtists,
  useLibraryTracks,
  useMyChannels,
  useRemoveChannel,
  type TrackFilters,
} from '@/api/hooks';
import { ChannelCard } from '@/components/ChannelCard';
import { TrackRow } from '@/components/TrackRow';
import { Cover, EmptyState, ErrorNote, Glass, LoadMore, Sheet, Spinner, cx } from '@/components/ui';
import { PlusIcon } from '@/components/icons';
import { useI18n, type Key } from '@/i18n';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

type Tab = 'tracks' | 'artists' | 'albums' | 'channels' | 'playlists';
const TABS: { id: Tab; label: Key }[] = [
  { id: 'tracks', label: 'library.tracks' },
  { id: 'playlists', label: 'library.playlists' },
  { id: 'artists', label: 'library.artists' },
  { id: 'albums', label: 'library.albums' },
  { id: 'channels', label: 'library.channels' },
];

function AddChannelSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const add = useAddChannel();
  const toast = useUi((s) => s.toast);
  const showUpsell = useUi((s) => s.showUpsell);

  const submit = () => {
    setError(null);
    add.mutate(value.trim(), {
      onSuccess: () => {
        toast(t('channel.added'), 'success');
        setValue('');
        onClose();
      },
      onError: (failure) => {
        if (failure instanceof ApiError && failure.isPlanLimit) {
          onClose();
          showUpsell({ kind: String(failure.details.kind ?? 'channels'), limit: Number(failure.details.limit ?? 0) });
          return;
        }
        if (failure instanceof ApiError && failure.details.reason === 'private_link') setError(t('channel.add.private'));
        else if (failure instanceof ApiError && failure.status === 403) setError(t('channel.add.blocked'));
        else setError(t('channel.add.invalid'));
      },
    });
  };

  return (
    <Sheet open={open} onClose={onClose} title={t('channel.add.title')}>
      <p className="mb-3 px-1 text-[12.5px] leading-6 text-[var(--ink-dim)]">{t('channel.add.hint')}</p>
      <div className="flex gap-2">
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && value.trim()) submit();
          }}
          dir="ltr"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder="@PersianMusic"
          aria-label={t('channel.add.title')}
          className="min-w-0 flex-1 rounded-xl bg-white/8 px-3.5 py-2.5 text-[14px] outline-none"
        />
        <button
          type="button"
          disabled={!value.trim() || add.isPending}
          onClick={submit}
          className="grid min-w-20 place-items-center rounded-xl bg-[var(--accent)] px-4 text-[13.5px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
        >
          {add.isPending ? <Spinner size={16} /> : t('channel.add.button')}
        </button>
      </div>
      {error && <p className="mt-2.5 px-1 text-[12.5px] text-[#ff9a9a]">{error}</p>}
    </Sheet>
  );
}

export function Library() {
  const { t, n } = useI18n();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get('tab') as Tab) ?? 'tracks');
  const [addOpen, setAddOpen] = useState(params.get('add') === '1');
  const [filters, setFilters] = useState<TrackFilters>({});
  const [filterOpen, setFilterOpen] = useState(false);

  const play = usePlayer((s) => s.play);
  const tracksQuery = useLibraryTracks(filters);
  const artistsQuery = useLibraryArtists();
  const albumsQuery = useLibraryAlbums();
  const channelsQuery = useMyChannels();
  const removeChannel = useRemoveChannel();
  const toast = useUi((s) => s.toast);

  const tracks = useMemo(() => flatten(tracksQuery.data), [tracksQuery.data]);
  const artists = useMemo(() => flatten(artistsQuery.data), [artistsQuery.data]);
  const thumbs = useThumbs(tracks.map((track) => track.id));
  const hasFilters = Object.values(filters).some((value) => value !== null && value !== undefined);

  const closeAdd = () => {
    setAddOpen(false);
    if (params.has('add')) {
      params.delete('add');
      setParams(params, { replace: true });
    }
  };

  return (
    <div className="px-4 pt-4">
      <div className="flex items-center justify-between">
        <h1 className="text-[21px] font-bold">{t('tab.library')}</h1>
        <button
          type="button"
          aria-label={t('channel.add.title')}
          onClick={() => setAddOpen(true)}
          className="grid h-9 w-9 place-items-center rounded-full bg-[var(--accent)] text-[var(--accent-ink)]"
        >
          <PlusIcon size={18} />
        </button>
      </div>

      <div className="no-scrollbar -mx-1 mt-3 flex gap-2 overflow-x-auto px-1 pb-1">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => (item.id === 'playlists' ? navigate('/playlists') : setTab(item.id))}
            className={cx(
              'shrink-0 rounded-full px-3.5 py-1.5 text-[12.5px]',
              tab === item.id ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8 text-[var(--ink-dim)]',
            )}
          >
            {t(item.label)}
          </button>
        ))}
        {tab === 'tracks' && (
          <button
            type="button"
            onClick={() => setFilterOpen(true)}
            className={cx(
              'shrink-0 rounded-full px-3.5 py-1.5 text-[12.5px]',
              hasFilters ? 'bg-white/20 text-[var(--ink)]' : 'bg-white/8 text-[var(--ink-dim)]',
            )}
          >
            {t('library.filter')}
          </button>
        )}
      </div>

      {tab === 'tracks' &&
        (tracksQuery.isError ? (
          <ErrorNote onRetry={() => void tracksQuery.refetch()} />
        ) : tracks.length === 0 && !tracksQuery.isLoading ? (
          <EmptyState title={t('library.empty')} cta={t('home.empty.cta')} onCta={() => setAddOpen(true)} />
        ) : (
          <>
            <p className="mt-4 mb-1 px-1 text-[12px] text-[var(--ink-faint)]">{t('library.count', { count: tracks.length })}</p>
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
            <LoadMore enabled={Boolean(tracksQuery.hasNextPage)} onVisible={() => void tracksQuery.fetchNextPage()} />
          </>
        ))}

      {tab === 'artists' && (
        <>
          <div className="mt-4 grid grid-cols-2 gap-3">
            {artists.map((artist) => (
              <Glass
                key={artist.id}
                as="button"
                className="flex items-center gap-2.5 p-3 text-start"
                onClick={() => navigate(`/artist/${artist.id}`)}
              >
                <Cover seed={artist.name} size={42} radius={999} glyph="🎤" />
                <span className="min-w-0">
                  <span className="block truncate text-[13.5px] font-semibold">{artist.name}</span>
                  <span className="block text-[11.5px] text-[var(--ink-faint)]">
                    {t('library.count', { count: artist.tracks_count })}
                  </span>
                </span>
              </Glass>
            ))}
          </div>
          <LoadMore enabled={Boolean(artistsQuery.hasNextPage)} onVisible={() => void artistsQuery.fetchNextPage()} />
        </>
      )}

      {tab === 'albums' && (
        <div className="mt-4 flex flex-col gap-2">
          {(albumsQuery.data ?? []).map((album) => (
            <Glass
              key={`${album.album}-${album.artist_id ?? 0}`}
              className="flex items-center gap-3 p-3"
              onClick={() => setFilters({ album: album.album })}
            >
              <Cover seed={album.album} size={44} glyph="💿" />
              <div className="min-w-0">
                <p className="truncate text-[13.5px] font-semibold">{album.album}</p>
                <p className="truncate text-[11.5px] text-[var(--ink-faint)]">
                  {album.artist_name ?? ''} · {n(album.tracks_count)}
                </p>
              </div>
            </Glass>
          ))}
          {(albumsQuery.data?.length ?? 0) === 0 && !albumsQuery.isLoading && <EmptyState title={t('library.empty')} />}
        </div>
      )}

      {tab === 'channels' && (
        <div className="mt-4 flex flex-col gap-2.5">
          {channelsQuery.data?.map((channel) => (
            <div key={channel.id} className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <ChannelCard channel={channel} onClick={() => navigate(`/channel/${channel.id}`)} />
              </div>
              <button
                type="button"
                aria-label={t('channel.remove')}
                className="shrink-0 rounded-xl bg-white/8 px-3 py-2 text-[12px] text-[var(--ink-dim)]"
                onClick={() =>
                  removeChannel.mutate(channel.id, { onSuccess: () => toast(t('channel.removed')) })
                }
              >
                ✕
              </button>
            </div>
          ))}
          {(channelsQuery.data?.length ?? 0) === 0 && !channelsQuery.isLoading && (
            <EmptyState title={t('home.empty.title')} body={t('home.empty.body')} cta={t('home.empty.cta')} onCta={() => setAddOpen(true)} />
          )}
        </div>
      )}

      <AddChannelSheet open={addOpen} onClose={closeAdd} />

      <Sheet open={filterOpen} onClose={() => setFilterOpen(false)} title={t('library.filter')}>
        <p className="mb-2 px-1 text-[12.5px] text-[var(--ink-dim)]">{t('library.filter.language')}</p>
        <div className="mb-4 flex flex-wrap gap-2">
          {(['fa', 'en', 'ar', 'tr'] as const).map((language) => (
            <button
              key={language}
              type="button"
              onClick={() => setFilters((f) => ({ ...f, language: f.language === language ? null : language }))}
              className={cx(
                'rounded-xl px-4 py-2 text-[13px]',
                filters.language === language ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8',
              )}
            >
              {language.toUpperCase()}
            </button>
          ))}
        </div>
        <p className="mb-2 px-1 text-[12.5px] text-[var(--ink-dim)]">{t('library.filter.duration')}</p>
        <div className="mb-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setFilters((f) => ({ ...f, max_duration: f.max_duration ? null : 180, min_duration: null }))}
            className={cx('rounded-xl px-4 py-2 text-[13px]', filters.max_duration ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8')}
          >
            {t('library.filter.short')}
          </button>
          <button
            type="button"
            onClick={() => setFilters((f) => ({ ...f, min_duration: f.min_duration ? null : 300, max_duration: null }))}
            className={cx('rounded-xl px-4 py-2 text-[13px]', filters.min_duration ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8')}
          >
            {t('library.filter.long')}
          </button>
        </div>
        <button
          type="button"
          onClick={() => {
            setFilters({});
            setFilterOpen(false);
          }}
          className="w-full rounded-xl bg-white/8 py-2.5 text-[13px]"
        >
          {t('library.filter.clear')}
        </button>
      </Sheet>
    </div>
  );
}
