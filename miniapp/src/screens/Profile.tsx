import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  useConnections,
  useFriendsFeed,
  useProfile,
  useToggleFollow,
  type Connection,
} from '@/api/social';
import { TrackRow } from '@/components/TrackRow';
import { Cover, EmptyState, ErrorNote, Glass, SectionHead, Sheet, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { formatDate } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

function Initial({ name, size = 56 }: { name: string; size?: number }) {
  return (
    <div
      className="grid shrink-0 place-items-center rounded-full bg-gradient-to-br from-[#ffb86b] to-[#ff6b9a] font-bold text-[#2a1206]"
      style={{ width: size, height: size, fontSize: size / 2.6 }}
    >
      {name.slice(0, 1)}
    </div>
  );
}

function PeopleSheet({
  userId,
  direction,
  open,
  onClose,
}: {
  userId: number;
  direction: 'followers' | 'following';
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const people = useConnections(userId, direction);
  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t(direction === 'followers' ? 'profile.followers' : 'profile.following')}
    >
      {people.isLoading && <Spinner />}
      {people.data?.length === 0 && (
        <p className="py-6 text-center text-[13px] text-[var(--ink-faint)]">
          {t('profile.nobody')}
        </p>
      )}
      <div className="flex flex-col gap-1.5">
        {people.data?.map((person: Connection) => (
          <button
            key={person.user_id}
            type="button"
            onClick={() => {
              onClose();
              navigate(`/user/${person.user_id}`);
            }}
            className="flex items-center gap-3 rounded-xl px-2 py-2 text-start"
          >
            <Initial name={person.first_name} size={36} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13.5px]">{person.first_name}</span>
              {person.username && (
                <span className="block truncate text-[11.5px] text-[var(--ink-faint)]">
                  @{person.username}
                </span>
              )}
            </span>
            {person.is_pro && <span className="text-[11px] text-[var(--accent)]">PRO</span>}
          </button>
        ))}
      </div>
    </Sheet>
  );
}

export function Profile() {
  const { t, n, lang } = useI18n();
  const navigate = useNavigate();
  const params = useParams();
  const userId = params.id ? Number(params.id) : 'me';
  const profile = useProfile(userId);
  const follow = useToggleFollow(typeof userId === 'number' ? userId : 0);
  const play = usePlayer((s) => s.play);
  const [people, setPeople] = useState<'followers' | 'following' | null>(null);

  const tracks = profile.data?.top_tracks ?? [];
  const thumbs = useThumbs(tracks.map((track) => track.id));

  if (profile.isError) {
    return (
      <div className="px-4 pt-6">
        <EmptyState title={t('profile.hidden')} body={t('profile.hiddenBody')} />
      </div>
    );
  }
  if (profile.isLoading || !profile.data) {
    return (
      <div className="flex justify-center py-16">
        <Spinner />
      </div>
    );
  }

  const data = profile.data;

  return (
    <div className="px-4 pb-28 pt-4">
      <Glass className="flex items-center gap-4 p-4">
        <Initial name={data.first_name} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[17px] font-bold">
            {data.first_name}
            {data.is_pro && <span className="ms-2 text-[11px] text-[var(--accent)]">PRO</span>}
          </p>
          {data.username && (
            <p className="truncate text-[12px] text-[var(--ink-faint)]">@{data.username}</p>
          )}
          <p className="mt-0.5 text-[11.5px] text-[var(--ink-faint)]">
            {t('profile.joined', { date: formatDate(data.joined_at, lang) })}
          </p>
        </div>
        {!data.is_me && (
          <button
            type="button"
            onClick={() => {
              haptic('select');
              follow.mutate(data.is_following);
            }}
            className={cx(
              'shrink-0 rounded-full px-4 py-2 text-[12.5px] font-bold',
              data.is_following
                ? 'bg-[var(--fill)] text-[var(--ink-dim)]'
                : 'bg-[var(--accent)] text-[var(--accent-ink)]',
            )}
          >
            {t(data.is_following ? 'profile.unfollow' : 'profile.follow')}
          </button>
        )}
      </Glass>

      <div className="mt-3 grid grid-cols-3 gap-2">
        <button
          type="button"
          onClick={() => setPeople('followers')}
          className="glass rounded-xl p-3 text-center"
        >
          <span className="block text-[17px] font-bold">{n(data.followers)}</span>
          <span className="text-[11px] text-[var(--ink-faint)]">{t('profile.followers')}</span>
        </button>
        <button
          type="button"
          onClick={() => setPeople('following')}
          className="glass rounded-xl p-3 text-center"
        >
          <span className="block text-[17px] font-bold">{n(data.following)}</span>
          <span className="text-[11px] text-[var(--ink-faint)]">{t('profile.following')}</span>
        </button>
        <div className="glass rounded-xl p-3 text-center">
          <span className="block text-[17px] font-bold">{n(data.tracks_played)}</span>
          <span className="text-[11px] text-[var(--ink-faint)]">{t('profile.plays')}</span>
        </div>
      </div>

      {data.is_me && (
        <button
          type="button"
          onClick={() => navigate('/wrapped')}
          className="mt-3 w-full rounded-xl bg-gradient-to-r from-[#7b5cff] to-[#ff6b9a] py-3 text-[13.5px] font-bold text-white"
        >
          {t('wrapped.open')}
        </button>
      )}

      {data.top_artists.length > 0 && (
        <>
          <SectionHead title={t('profile.topArtists')} />
          <div className="no-scrollbar flex gap-2 overflow-x-auto pb-1.5">
            {data.top_artists.map((artist) => (
              <button
                key={artist.id}
                type="button"
                onClick={() => navigate(`/artist/${artist.id}`)}
                className="glass shrink-0 rounded-full px-3.5 py-2 text-[12.5px]"
              >
                {artist.name}
              </button>
            ))}
          </div>
        </>
      )}

      {tracks.length > 0 && (
        <>
          <SectionHead title={t('profile.topTracks')} />
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

      {data.public_playlists.length > 0 && (
        <>
          <SectionHead title={t('profile.playlists')} />
          <div className="flex flex-col gap-2">
            {data.public_playlists.map((playlist) => (
              <Glass
                key={playlist.id}
                as="button"
                className="flex items-center gap-3 p-2.5 text-start"
                onClick={() => navigate(`/playlist/${playlist.id}`)}
              >
                <Cover seed={playlist.id} size={44} radius={12} glyph="♪" />
                <span className="min-w-0">
                  <span className="block truncate text-[13.5px] font-semibold">
                    {playlist.name}
                  </span>
                  <span className="text-[11.5px] text-[var(--ink-faint)]">
                    {t('library.count', { count: playlist.tracks_count })}
                  </span>
                </span>
              </Glass>
            ))}
          </div>
        </>
      )}

      <PeopleSheet
        userId={data.user_id}
        direction={people ?? 'followers'}
        open={people !== null}
        onClose={() => setPeople(null)}
      />
    </div>
  );
}

export function FriendsFeedScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const play = usePlayer((s) => s.play);
  const feed = useFriendsFeed();
  const tracks = (feed.data ?? []).map((entry) => entry.track);
  const thumbs = useThumbs(tracks.map((track) => track.id));

  if (feed.isError) return <ErrorNote onRetry={() => void feed.refetch()} />;

  return (
    <div className="px-4 pb-28 pt-4">
      <h1 className="mb-3 text-[21px] font-bold">{t('social.feed')}</h1>
      {feed.isLoading && <Spinner />}
      {feed.data?.length === 0 && (
        <EmptyState title={t('social.empty.title')} body={t('social.empty.body')} />
      )}
      <div className="flex flex-col gap-2">
        {feed.data?.map((entry, index) => (
          <Glass key={`${entry.user_id}-${entry.track.id}`} className="p-2.5">
            <button
              type="button"
              onClick={() => navigate(`/user/${entry.user_id}`)}
              className="mb-1.5 flex items-center gap-2 text-start"
            >
              <Initial name={entry.first_name} size={24} />
              <span className="text-[12px] text-[var(--ink-dim)]">
                {t('social.listened', { name: entry.first_name })}
              </span>
            </button>
            <TrackRow
              track={entry.track}
              {...(thumbs[entry.track.id] ? { thumb: thumbs[entry.track.id] as string } : {})}
              onPlay={() => void play({ queue: tracks, index, source: 'library' })}
            />
          </Glass>
        ))}
      </div>
    </div>
  );
}
