/**
 * For the people who run a channel: how it does here. Plays, listeners, minutes, the
 * last thirty days and the most played songs — aggregates only, never who listened.
 *
 * Ownership is proven through Telegram: an administrator of the channel, checked by
 * the bot. Making the bot an admin does it on the spot; otherwise claim it here.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError, get, post } from '@/api/client';
import type { components } from '@/api/schema';
import { TrackRow } from '@/components/TrackRow';
import { EmptyState, ErrorNote, Glass, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { BOT_USERNAME, haptic } from '@/lib/telegram';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';

export type OwnerChannel = components['schemas']['OwnerChannelOut'];

const KEY = ['owner', 'channels'] as const;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-[var(--fill)] px-3 py-2.5">
      <p className="text-[19px] font-bold tabular-nums">{value}</p>
      <p className="mt-0.5 text-[11.5px] text-[var(--ink-dim)]">{label}</p>
    </div>
  );
}

/** Thirty bars, one per day; today is the last one. */
export function DailyBars({ daily, label }: { daily: number[]; label: string }) {
  const top = Math.max(1, ...daily);
  return (
    <div role="img" aria-label={label} className="flex h-20 items-end gap-[3px]" dir="ltr">
      {daily.map((plays, day) => (
        <span
          key={day}
          className={cx('flex-1 rounded-t-[3px]', plays ? 'bg-[var(--accent)]' : 'bg-[var(--fill)]')}
          style={{ height: `${Math.max(6, (plays / top) * 100)}%`, opacity: plays ? 0.45 + 0.55 * (plays / top) : 1 }}
        />
      ))}
    </div>
  );
}

function ChannelCardStats({ channel }: { channel: OwnerChannel }) {
  const { t, n } = useI18n();
  const play = usePlayer((s) => s.play);
  const thumbs = useThumbs(channel.top_tracks.map((track) => track.id));
  return (
    <Glass className="mb-4 p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="truncate text-[16px] font-bold">{channel.title ?? `@${channel.username ?? ''}`}</h2>
        {channel.username && <span className="shrink-0 text-[12px] text-[var(--ink-faint)]" dir="ltr">@{channel.username}</span>}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat label={t('owner.plays7')} value={n(channel.plays_7d)} />
        <Stat label={t('owner.listeners7')} value={n(channel.listeners_7d)} />
        <Stat label={t('owner.minutes30')} value={n(channel.minutes_30d)} />
        <Stat label={t('owner.followers')} value={n(channel.app_followers)} />
      </div>
      <p className="mb-2 mt-4 text-[12.5px] font-semibold text-[var(--ink-dim)]">
        {t('owner.last30', { plays: n(channel.plays_30d), listeners: n(channel.listeners_30d) })}
      </p>
      <DailyBars daily={channel.daily} label={t('owner.last30', { plays: n(channel.plays_30d), listeners: n(channel.listeners_30d) })} />
      {channel.top_tracks.length > 0 && (
        <>
          <p className="mb-1 mt-4 text-[12.5px] font-semibold text-[var(--ink-dim)]">{t('owner.top')}</p>
          <div className="-mx-2">
            {channel.top_tracks.map((track, index) => (
              <TrackRow
                key={track.id}
                track={track}
                {...(thumbs[track.id] ? { thumb: thumbs[track.id] as string } : {})}
                onPlay={() => void play({ queue: channel.top_tracks, index, source: 'channel', sourceId: channel.id })}
              />
            ))}
          </div>
        </>
      )}
      <p className="mt-3 text-[11.5px] text-[var(--ink-faint)]">{t('owner.private', { tracks: n(channel.tracks_count) })}</p>
    </Glass>
  );
}

function Claim() {
  const { t } = useI18n();
  const client = useQueryClient();
  const [value, setValue] = useState('');
  const claim = useMutation({
    mutationFn: (channel: string) => post<OwnerChannel>('/v1/owner/channels', { channel }),
    onSuccess: () => {
      haptic('success');
      setValue('');
      void client.invalidateQueries({ queryKey: KEY });
    },
  });
  const reason =
    claim.error instanceof ApiError
      ? ((claim.error.details?.reason as string | undefined) ?? (claim.error.status === 403 ? 'not_admin' : 'error'))
      : null;
  return (
    <Glass className="mb-4 p-4">
      <h2 className="text-[15px] font-bold">{t('owner.claim')}</h2>
      <ol className="mt-2 list-decimal space-y-1 ps-5 text-[13px] leading-6 text-[var(--ink-dim)]">
        <li>
          {t('owner.step1')}{' '}
          <code dir="ltr" className="inline-block rounded-md bg-[var(--fill)] px-1.5 font-mono text-[12.5px] text-[var(--ink)]">
            @{BOT_USERNAME}
          </code>
        </li>
        <li>{t('owner.step2')}</li>
      </ol>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) claim.mutate(value.trim());
        }}
      >
        <input
          dir="ltr"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="@channel"
          aria-label={t('owner.claim')}
          className="min-w-0 flex-1 rounded-full bg-[var(--fill)] px-4 py-2 text-[14px] outline-none"
        />
        <button
          type="submit"
          disabled={claim.isPending || !value.trim()}
          className="shrink-0 rounded-full bg-[var(--accent)] px-4 py-2 text-[13px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
        >
          {claim.isPending ? <Spinner size={14} /> : t('owner.verify')}
        </button>
      </form>
      {reason && (
        <p role="alert" className="mt-2 text-[12.5px] text-[#ff8a80]">
          {t(reason === 'bot_not_admin' ? 'owner.err.bot' : reason === 'not_admin' ? 'owner.err.admin' : 'owner.err.other', {
            bot: BOT_USERNAME,
          })}
        </p>
      )}
    </Glass>
  );
}

export function OwnerScreen() {
  const { t } = useI18n();
  const channels = useQuery({ queryKey: KEY, queryFn: () => get<OwnerChannel[]>('/v1/owner/channels') });
  return (
    <div className="px-4 pt-4">
      <h1 className="text-[21px] font-bold">{t('owner.title')}</h1>
      <p className="mb-4 mt-1 text-[13px] leading-6 text-[var(--ink-dim)]">{t('owner.intro')}</p>
      {channels.isPending ? (
        <div className="grid place-items-center py-14">
          <Spinner />
        </div>
      ) : channels.isError ? (
        <ErrorNote onRetry={() => void channels.refetch()} />
      ) : (
        <>
          {channels.data.length === 0 && <EmptyState title={t('owner.none')} body={t('owner.noneHint')} />}
          {channels.data.map((channel) => (
            <ChannelCardStats key={channel.id} channel={channel} />
          ))}
          <Claim />
        </>
      )}
    </div>
  );
}
