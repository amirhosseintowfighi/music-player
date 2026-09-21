import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  useDiscover,
  useRefreshDiscover,
  useTrending,
  type Section,
  type TrendKind,
  type TrendWindow,
} from '@/api/discover';
import { usePlaylist } from '@/api/playlists';
import { useFriendsFeed } from '@/api/social';
import type { Track } from '@/api/client';
import { TrackRow } from '@/components/TrackRow';
import { Cover, EmptyState, ErrorNote, Glass, SectionHead, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

const WINDOWS: TrendWindow[] = ['24h', '7d', '30d'];
const KINDS: TrendKind[] = ['plays', 'most_added', 'rising'];

function TrackCarousel({ items, source }: { items: Track[]; source: 'discover' | 'trending' }) {
  const play = usePlayer((s) => s.play);
  const thumbs = useThumbs(items.slice(0, 12).map((track) => track.id));
  return (
    <div className="no-scrollbar flex gap-3 overflow-x-auto px-0.5 pb-1.5">
      {items.slice(0, 12).map((track, index) => (
        <Glass
          key={track.id}
          as="button"
          className="w-[132px] shrink-0 p-2.5 text-start"
          onClick={() => void play({ queue: items, index, source })}
        >
          <Cover src={thumbs[track.id]} seed={track.id} size={112} radius={14} glyph="♫" />
          <p className="mt-2 truncate text-[13px] font-semibold">{track.title}</p>
          <p className="truncate text-[11.5px] text-[var(--ink-faint)]">{artistNames(track)}</p>
        </Glass>
      ))}
    </div>
  );
}

/** A generated playlist row: the tracks live in the playlist, so they load on demand. */
function PlaylistSection({ section }: { section: Section }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const detail = usePlaylist(section.playlist_id ?? 0);
  const play = usePlayer((s) => s.play);
  const items = detail.data?.items ?? [];
  return (
    <>
      <SectionHead
        title={section.title}
        action={t('home.all')}
        onAction={() => navigate(`/playlist/${section.playlist_id}`)}
      />
      {detail.isLoading && <Spinner />}
      {items.length > 0 && (
        <Glass className="p-1.5">
          {items.slice(0, 5).map((track, index) => (
            <TrackRow
              key={track.id}
              track={track}
              onPlay={() => void play({ queue: items, index, source: 'discover' })}
            />
          ))}
        </Glass>
      )}
    </>
  );
}

function Chips<T extends string>({
  value,
  options,
  label,
  onChange,
}: {
  value: T;
  options: T[];
  label: (option: T) => string;
  onChange: (option: T) => void;
}) {
  return (
    <div className="no-scrollbar mb-2 flex gap-1.5 overflow-x-auto">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={cx(
            'shrink-0 rounded-full px-3 py-1.5 text-[12px]',
            option === value
              ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]'
              : 'glass text-[var(--ink-dim)]',
          )}
        >
          {label(option)}
        </button>
      ))}
    </div>
  );
}

/** What friends are playing, only rendered when there is something to show. */
function FriendsRow() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const feed = useFriendsFeed();
  const items = (feed.data ?? []).map((entry) => entry.track);
  if (items.length === 0) return null;
  return (
    <>
      <SectionHead title={t('social.feed')} action={t('home.all')} onAction={() => navigate('/friends')} />
      <TrackCarousel items={items} source="discover" />
    </>
  );
}

export function Discover() {
  const { t } = useI18n();
  const feed = useDiscover();
  const refresh = useRefreshDiscover();
  const [window, setWindow] = useState<TrendWindow>('7d');
  const [kind, setKind] = useState<TrendKind>('plays');
  const trending = useTrending(window, kind);
  const play = usePlayer((s) => s.play);

  if (feed.isError) return <ErrorNote onRetry={() => void feed.refetch()} />;

  const sections = feed.data?.sections ?? [];
  const generated = sections.filter((section) => section.kind === 'playlist');

  return (
    <div className="px-4 pb-28">
      <header className="flex items-center justify-between py-4">
        <h1 className="text-[21px] font-bold">{t('discover.title')}</h1>
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="glass rounded-full px-3 py-1.5 text-[12px]"
        >
          {refresh.isPending ? t('common.loading') : t('discover.refresh')}
        </button>
      </header>

      {feed.isLoading && (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      )}

      <FriendsRow />

      {generated.map((section) => (
        <PlaylistSection key={section.id} section={section} />
      ))}

      {sections
        .filter((section) => section.kind === 'tracks' && (section.items?.length ?? 0) > 0)
        .map((section) => (
          <div key={section.id}>
            <SectionHead title={section.title} />
            <TrackCarousel items={section.items ?? []} source="discover" />
          </div>
        ))}

      <SectionHead title={t('discover.trending')} />
      <Chips
        value={window}
        options={WINDOWS}
        label={(option) => t(option === '24h' ? 'discover.24h' : option === '7d' ? 'discover.7d' : 'discover.30d')}
        onChange={setWindow}
      />
      <Chips
        value={kind}
        options={KINDS}
        label={(option) =>
          t(
            option === 'plays'
              ? 'discover.kind.plays'
              : option === 'most_added'
                ? 'discover.kind.mostAdded'
                : 'discover.kind.rising',
          )
        }
        onChange={setKind}
      />
      {trending.isLoading && <Spinner />}
      {trending.data && trending.data.items.length > 0 && (
        <Glass className="p-1.5">
          {trending.data.items.slice(0, 20).map((track, index) => (
            <TrackRow
              key={track.id}
              track={track}
              onPlay={() =>
                void play({ queue: trending.data.items, index, source: 'trending' })
              }
            />
          ))}
        </Glass>
      )}

      {!feed.isLoading && sections.length === 0 && trending.data?.items.length === 0 && (
        <EmptyState title={t('discover.empty.title')} body={t('discover.empty.body')} />
      )}
    </div>
  );
}
