import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import type { Track } from '@/api/client';
import { fetchRadio, useSimilar } from '@/api/discover';
import { useReportTrack } from '@/api/hooks';
import { useAddToPlaylist, usePlaylists, useSendToChat, useToggleLike } from '@/api/playlists';
import { DownloadIcon, HeartIcon, PlusIcon, QueueIcon, RadioIcon, SendIcon } from '@/components/icons';
import { Cover, Sheet, Spinner, cx } from '@/components/ui';
import { CreatePlaylistSheet } from '@/screens/Playlists';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { openTelegramLink } from '@/lib/telegram';
import { listOffline, removeOffline, saveOffline } from '@/player/engine';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

function Action({
  icon,
  label,
  onClick,
  tone,
  busy,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  tone?: 'accent';
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        'flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-start text-[13.5px]',
        'bg-white/8',
        tone === 'accent' && 'text-[var(--accent)]',
      )}
    >
      <span className="shrink-0">{busy ? <Spinner size={16} /> : icon}</span>
      {label}
    </button>
  );
}

const REPORT_REASONS = ['wrong_metadata', 'copyright', 'inappropriate', 'broken'] as const;

/** A shared track opens the Mini App on that track (`startapp=tr_<id>`). */
function shareLink(trackId: number): string {
  const bot = (import.meta.env.VITE_BOT_USERNAME as string | undefined) ?? 'tmusic_bot';
  return `https://t.me/${bot}?startapp=tr_${trackId}`;
}

/**
 * Three or four similar tracks, right inside the sheet.
 *
 * Loaded only when the sheet is open — "what else sounds like this" is a question
 * people ask *here*, and making them navigate away to answer it loses the moment.
 */
function SimilarStrip({ track, onPlay }: { track: Track; onPlay: () => void }) {
  const { t } = useI18n();
  const similar = useSimilar(track.id);
  const play = usePlayer((s) => s.play);
  const items = (similar.data?.items ?? []).slice(0, 5);
  if (items.length === 0) return null;
  return (
    <div className="mt-4">
      <p className="mb-2 px-1 text-[12px] text-[var(--ink-dim)]">{t('track.similar')}</p>
      <ul className="flex flex-col gap-1">
        {items.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              className="flex w-full items-center gap-3 rounded-xl px-1 py-1.5 text-start"
              onClick={() => {
                onPlay();
                void play({ queue: items, index: items.indexOf(item), source: 'discover' });
              }}
            >
              <Cover seed={item.id} size={36} />
              <span className="min-w-0">
                <span className="block truncate text-[13px]">{item.title}</span>
                <span className="block truncate text-[11px] text-[var(--ink-faint)]">
                  {artistNames(item)}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Long-press / "…" menu for a track: like, playlist, queue, send, offline. */
export function TrackActions({
  track,
  open,
  onClose,
}: {
  track: Track | null;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const toast = useUi((s) => s.toast);
  const enqueue = usePlayer((s) => s.enqueue);
  const toggleLike = useToggleLike();
  const playlists = usePlaylists();
  const addTo = useAddToPlaylist();
  const send = useSendToChat();
  const [pickPlaylist, setPickPlaylist] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [offline, setOffline] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [radioBusy, setRadioBusy] = useState(false);
  const [reporting, setReporting] = useState(false);
  const play = usePlayer((s) => s.play);
  const report = useReportTrack(track?.id ?? 0);

  if (!track) return null;

  const close = () => {
    setPickPlaylist(false);
    setReporting(false);
    onClose();
  };

  return (
    <>
      <Sheet open={open && !pickPlaylist && !reporting} onClose={close} title={t('track.actions')}>
        <div className="mb-4 flex items-center gap-3 px-1">
          <Cover seed={track.id} size={44} />
          <div className="min-w-0">
            <p className="truncate text-[14px] font-semibold">{track.title}</p>
            <p className="truncate text-[12px] text-[var(--ink-faint)]">{artistNames(track)}</p>
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <Action
            icon={<HeartIcon size={18} filled={track.liked} />}
            label={track.liked ? t('player.unlike') : t('player.like')}
            tone={track.liked ? 'accent' : undefined}
            onClick={() => toggleLike.mutate({ trackId: track.id, liked: track.liked })}
          />
          <Action
            icon={<PlusIcon size={18} />}
            label={t('playlist.addTo')}
            onClick={() => setPickPlaylist(true)}
          />
          <Action
            icon={<RadioIcon size={18} />}
            label={t('track.radio')}
            busy={radioBusy}
            onClick={async () => {
              setRadioBusy(true);
              try {
                const station = await fetchRadio(track.id);
                if (station.length > 0) {
                  await play({ queue: station, index: 0, source: 'radio' });
                  close();
                }
              } catch {
                toast(t('app.error'), 'error');
              } finally {
                setRadioBusy(false);
              }
            }}
          />
          <Action
            icon={<QueueIcon size={18} />}
            label={t('track.addQueue')}
            onClick={() => {
              enqueue([track], 'next');
              toast(t('track.queued'));
              close();
            }}
          />
          <Action
            icon={<SendIcon size={18} />}
            label={t('player.sendToChat')}
            busy={send.isPending}
            onClick={() =>
              send.mutate(track.id, {
                onSuccess: () => {
                  toast(t('player.sent'), 'success');
                  close();
                },
                onError: () => toast(t('app.error'), 'error'),
              })
            }
          />
          <Action
            icon={<DownloadIcon size={18} />}
            label={
              offline === 'saved'
                ? t('player.removeDownload')
                : offline === 'saving'
                  ? t('player.downloading')
                  : t('player.download')
            }
            busy={offline === 'saving'}
            onClick={async () => {
              if (offline === 'saved') {
                await removeOffline(track.id);
                setOffline('idle');
                return;
              }
              setOffline('saving');
              try {
                await saveOffline(track.id);
                setOffline('saved');
                toast(t('player.downloaded'), 'success');
              } catch {
                setOffline('idle');
                toast(t('app.error'), 'error');
              }
              void listOffline();
            }}
          />
          {track.artists[0] && (
            <Action
              icon={<span aria-hidden>🎤</span>}
              label={t('track.goArtist')}
              onClick={() => {
                close();
                navigate(`/artist/${track.artists[0]?.id}`);
              }}
            />
          )}
          {track.album && (
            <Action
              icon={<span aria-hidden>💿</span>}
              label={t('track.goAlbum')}
              onClick={() => {
                close();
                navigate(`/library?album=${encodeURIComponent(track.album ?? '')}`);
              }}
            />
          )}
          {track.channel?.username && (
            <Action
              icon={<span aria-hidden>📣</span>}
              label={t('track.joinChannel', { name: track.channel.title })}
              onClick={() => {
                close();
                openTelegramLink(`https://t.me/${track.channel?.username}`);
              }}
            />
          )}
          <Action
            icon={<span aria-hidden>↗</span>}
            label={t('track.share')}
            onClick={() => {
              const link = shareLink(track.id);
              openTelegramLink(
                `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(
                  `${track.title} — ${artistNames(track)}`,
                )}`,
              );
            }}
          />
          <Action
            icon={<span aria-hidden>⚠</span>}
            label={t('track.report')}
            onClick={() => setReporting(true)}
          />
        </div>

        <SimilarStrip track={track} onPlay={close} />
      </Sheet>

      <Sheet open={open && reporting} onClose={close} title={t('track.report')}>
        <div className="flex flex-col gap-2 pb-2">
          {REPORT_REASONS.map((reason) => (
            <button
              key={reason}
              type="button"
              className="rounded-xl bg-white/8 px-3.5 py-3 text-start text-[13.5px]"
              onClick={() => {
                report.mutate(
                  { reason },
                  {
                    onSuccess: () => toast(t('track.report.sent'), 'success'),
                    onError: () => toast(t('app.error'), 'error'),
                  },
                );
                close();
              }}
            >
              {t(`track.report.${reason}`)}
            </button>
          ))}
        </div>
      </Sheet>

      <Sheet open={open && pickPlaylist} onClose={close} title={t('playlist.addTo')}>
        <button
          type="button"
          className="mb-2 flex w-full items-center gap-3 rounded-xl bg-white/8 px-3.5 py-3 text-start text-[13.5px] text-[var(--accent)]"
          onClick={() => setCreateOpen(true)}
        >
          <PlusIcon size={18} />
          {t('playlist.new')}
        </button>
        <div className="flex flex-col gap-2">
          {playlists.data
            ?.filter((playlist) => playlist.can_edit)
            .map((playlist) => (
              <button
                key={playlist.id}
                type="button"
                className="flex items-center gap-3 rounded-xl bg-white/8 px-3.5 py-3 text-start"
                onClick={() =>
                  addTo.mutate(
                    { playlistId: playlist.id, trackIds: [track.id] },
                    {
                      onSuccess: () => {
                        toast(t('playlist.added'), 'success');
                        close();
                      },
                      onError: () => toast(t('app.error'), 'error'),
                    },
                  )
                }
              >
                <Cover seed={playlist.name} size={36} glyph="≡" />
                <span className="min-w-0">
                  <span className="block truncate text-[13.5px] font-semibold">{playlist.name}</span>
                  <span className="block text-[11.5px] text-[var(--ink-faint)]">
                    {t('library.count', { count: playlist.tracks_count })}
                  </span>
                </span>
              </button>
            ))}
        </div>
      </Sheet>

      <CreatePlaylistSheet
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          close();
        }}
        trackIds={[track.id]}
      />
    </>
  );
}
