/**
 * Song credits: who made it, and where it came from.
 *
 * Artists by role (performed by, featuring, written by, lyrics by), then the record
 * details, then the channels that carry the file — the one place a Telegram
 * catalogue can say something Spotify cannot: who shared it first.
 */
import { useNavigate } from 'react-router-dom';

import type { Track } from '@/api/client';
import { useCredits } from '@/api/listening';
import { Sheet, Spinner } from '@/components/ui';
import { useI18n, type Key } from '@/i18n';
import { duration } from '@/lib/format';
import { openTelegramLink } from '@/lib/telegram';

const ROLES: { role: Track['artists'][number]['role']; key: Key }[] = [
  { role: 'primary', key: 'credits.primary' },
  { role: 'feature', key: 'credits.feature' },
  { role: 'composer', key: 'credits.composer' },
  { role: 'lyricist', key: 'credits.lyricist' },
];

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <span className="shrink-0 text-[12.5px] text-[var(--ink-dim)]">{label}</span>
      <span className="min-w-0 truncate text-end text-[13.5px]">{value}</span>
    </div>
  );
}

function size(bytes: number): string {
  if (bytes <= 0) return '—';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function CreditsSheet({ track, open, onClose }: { track: Track | null; open: boolean; onClose: () => void }) {
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const credits = useCredits(track?.id, open);
  const data = credits.data;
  const shown = data?.track ?? track;

  return (
    <Sheet open={open} onClose={onClose} title={t('credits.title')}>
      {!shown ? null : (
        <div className="pb-2">
          <p className="truncate px-1 text-[16px] font-bold">{shown.title}</p>

          <div className="mt-3 divide-y divide-[var(--separator)] rounded-2xl bg-[var(--fill)] px-3.5">
            {ROLES.map(({ role, key }) => {
              const people = shown.artists.filter((artist) => artist.role === role);
              if (people.length === 0) return null;
              return (
                <Row
                  key={role}
                  label={t(key)}
                  value={people.map((artist, index) => (
                    <button
                      key={artist.id}
                      type="button"
                      className="font-semibold text-[var(--accent)]"
                      onClick={() => {
                        onClose();
                        navigate(`/artist/${artist.id}`);
                      }}
                    >
                      {index > 0 ? '، ' : ''}
                      {artist.name}
                    </button>
                  ))}
                />
              );
            })}
            {shown.album && <Row label={t('credits.album')} value={shown.album} />}
            {shown.year && <Row label={t('credits.year')} value={shown.year} />}
            {data?.genre && <Row label={t('credits.genre')} value={data.genre} />}
            <Row label={t('credits.length')} value={duration(shown.duration, lang)} />
            {data && <Row label={t('credits.file')} value={`${(data.mime_type ?? '').replace('audio/', '').toUpperCase() || '—'} · ${size(data.file_size)}`} />}
          </div>

          {credits.isLoading && (
            <div className="grid place-items-center py-5">
              <Spinner />
            </div>
          )}

          {data && data.sources.length > 0 && (
            <>
              <p className="mb-2 mt-4 px-1 text-[12px] text-[var(--ink-dim)]">
                {t('credits.sources', { count: data.channels })}
              </p>
              <ul className="flex flex-col gap-1.5">
                {data.sources.map((source) => (
                  <li key={source.channel_id}>
                    <button
                      type="button"
                      disabled={!source.username}
                      onClick={() => source.username && openTelegramLink(`https://t.me/${source.username}`)}
                      className="flex w-full items-center justify-between gap-3 rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-start"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-[13.5px] font-semibold">{source.title}</span>
                        <span className="block text-[11.5px] text-[var(--ink-faint)]">
                          {source.username ? `@${source.username} · ` : ''}
                          {new Date(source.posted_at).toLocaleDateString(lang === 'fa' ? 'fa-IR' : 'en-GB')}
                        </span>
                      </span>
                      <span className="shrink-0 text-[11.5px] text-[var(--ink-dim)]">
                        {t('credits.subscribers', { count: source.subscribers_count.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en') })}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </Sheet>
  );
}
