import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  useAlbumSearch,
  useArtistSearch,
  useClearSearchHistory,
  useSearch,
  useSuggestions,
} from '@/api/hooks';
import { TrackRow } from '@/components/TrackRow';
import { Cover, EmptyState, Glass, Spinner, cx } from '@/components/ui';
import { CloseIcon, SearchIcon } from '@/components/icons';
import { useI18n } from '@/i18n';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

const DEBOUNCE_MS = 280;

export function Search() {
  const { t } = useI18n();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  // Everything by default: someone searching for a song usually wants the song,
  // not the subset of it that happens to be in a channel they already added.
  const [scope, setScope] = useState<'library' | 'global'>('global');
  const play = usePlayer((s) => s.play);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(input.trim()), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [input]);

  const navigate = useNavigate();
  const results = useSearch(query, scope);
  // Artists are searched separately: a name typed into this box is usually a way to
  // reach the artist, not a track whose title happens to contain it.
  const artists = useArtistSearch(query);
  const albums = useAlbumSearch(query);
  const suggestions = useSuggestions(input.trim());
  const clearHistory = useClearSearchHistory();
  const tracks = useMemo(() => results.data?.items ?? [], [results.data]);
  const albumCoverIds = useMemo(
    () => (albums.data ?? []).map((album) => album.cover_track_id).filter((id): id is number => id != null),
    [albums.data],
  );
  const thumbs = useThumbs([...tracks.map((track) => track.id), ...albumCoverIds]);

  return (
    <div className="px-4 pt-4">
      <Glass className="flex items-center gap-2.5 rounded-full px-4 py-2.5" spec={0.35}>
        <SearchIcon size={18} className="text-[var(--ink-faint)]" />
        <input
          ref={inputRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          type="search"
          inputMode="search"
          enterKeyHint="search"
          aria-label={t('search.placeholder')}
          placeholder={t('search.placeholder')}
          className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-[var(--ink-faint)]"
        />
        {input && (
          <button type="button" aria-label={t('search.clear')} onClick={() => setInput('')} className="text-[var(--ink-faint)]">
            <CloseIcon size={16} />
          </button>
        )}
      </Glass>

      <div className="mt-3 flex gap-2">
        {(['library', 'global'] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setScope(value)}
            className={cx(
              'rounded-full px-3.5 py-1.5 text-[12.5px]',
              scope === value ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-white/8 text-[var(--ink-dim)]',
            )}
          >
            {t(value === 'library' ? 'search.scope.library' : 'search.scope.global')}
          </button>
        ))}
      </div>

      {!query && (suggestions.data?.history.length ?? 0) > 0 && (
        <>
          <div className="mt-6 mb-2 flex items-center justify-between px-1">
            <h2 className="text-[14px] font-bold">{t('search.recent')}</h2>
            <button type="button" className="text-[12.5px] text-[var(--ink-faint)]" onClick={() => clearHistory.mutate()}>
              {t('search.clear')}
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {suggestions.data?.history.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setInput(item)}
                className="rounded-full bg-white/8 px-3.5 py-1.5 text-[12.5px] text-[var(--ink-dim)]"
              >
                {item}
              </button>
            ))}
          </div>
        </>
      )}

      {query && results.isFetching && !results.data && (
        <div className="grid place-items-center py-14">
          <Spinner />
        </div>
      )}

      {query && (artists.data?.length ?? 0) > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 px-1 text-[14px] font-bold">{t('search.artists')}</h2>
          <div className="flex gap-3 overflow-x-auto pb-1">
            {artists.data?.map((artist) => (
              <button
                key={artist.id}
                type="button"
                className="w-20 shrink-0 text-center"
                onClick={() => navigate(`/artist/${artist.id}`)}
              >
                <Cover
                  {...(artist.image_url ? { src: artist.image_url } : {})}
                  seed={artist.name}
                  size={72}
                  radius={999}
                  glyph="🎤"
                />
                <p className="mt-1 truncate text-[12px] font-medium">{artist.name}</p>
                <p className="truncate text-[10.5px] text-[var(--ink-dim)]">
                  {t('library.count', { count: artist.tracks_count })}
                </p>
              </button>
            ))}
          </div>
        </section>
      )}

      {query && (albums.data?.length ?? 0) > 0 && (
        <section className="mt-5">
          <h2 className="mb-2 px-1 text-[14px] font-bold">{t('search.albums')}</h2>
          <div className="flex gap-3 overflow-x-auto pb-1">
            {albums.data?.map((album) => (
              <button
                key={`${album.artist_id}-${album.album}`}
                type="button"
                className="w-24 shrink-0 text-start"
                disabled={album.artist_id == null}
                onClick={() =>
                  navigate(`/album/${album.artist_id}/${encodeURIComponent(album.album)}`)
                }
              >
                <Cover
                  {...(album.cover_track_id && thumbs[album.cover_track_id]
                    ? { src: thumbs[album.cover_track_id] as string }
                    : {})}
                  seed={album.album}
                  size={96}
                  glyph="💿"
                />
                <p className="mt-1 truncate text-[12px] font-medium">{album.album}</p>
                <p className="truncate text-[10.5px] text-[var(--ink-dim)]">
                  {album.artist_name ?? ''}
                </p>
              </button>
            ))}
          </div>
        </section>
      )}

      {query && results.data && (
        <>
          <div className="mt-5 mb-1 flex items-center justify-between px-1 text-[12px] text-[var(--ink-faint)]">
            <span>{t('search.results', { count: results.data.total })}</span>
            {results.data.degraded && <span className="text-[#ffca6b]">{t('search.degraded')}</span>}
          </div>
          {tracks.length === 0 ? (
            <EmptyState title={t('search.empty')} body={t('search.emptyHint')} />
          ) : (
            <Glass className="p-1.5">
              {tracks.map((track, index) => (
                <TrackRow
                  key={track.id}
                  track={track}
                  {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
                  onPlay={() => void play({ queue: tracks, index, source: 'search' })}
                />
              ))}
            </Glass>
          )}
        </>
      )}
    </div>
  );
}
