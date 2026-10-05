import { useEffect, useState } from 'react';

import { get } from '@/api/client';
import type { Track } from '@/api/client';
import { Glass, Spinner, EmptyState, Cover } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { listOffline, removeOffline } from '@/player/engine';
import { usePlayer } from '@/store/player';

export function Downloads() {
  const { t } = useI18n();
  const [ids, setIds] = useState<number[] | null>(null);
  const [tracks, setTracks] = useState<Record<number, Track>>({});
  const play = usePlayer((s) => s.play);

  useEffect(() => {
    void listOffline().then((list) => {
      setIds(list);
      // Fetch metadata for known offline ids so the list shows titles
      void Promise.all(list.map((id) => get<Track>(`/v1/tracks/${id}`).catch(() => null))).then((results) => {
        const map: Record<number, Track> = {};
        for (const track of results) if (track) map[track.id] = track;
        setTracks(map);
      });
    });
  }, []);

  if (ids === null) return <div className="grid place-items-center py-20"><Spinner /></div>;
  if (ids.length === 0) return <EmptyState title={t('web.downloads.title')} body={t('web.downloads.empty')} />;

  return (
    <div className="mx-auto max-w-3xl px-4 pt-4">
      <h1 className="mb-3 text-[18px] font-bold">{t('web.downloads.title')}</h1>
      <Glass className="divide-y divide-[var(--separator)] overflow-hidden">
        {ids.map((id) => {
          const track = tracks[id];
          return (
            <div key={id} className="flex items-center gap-3 px-3 py-3">
              <Cover seed={id} size={44} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[14px] font-semibold">{track?.title ?? `#${id}`}</span>
                <span className="block truncate text-[12px] text-[var(--ink-dim)]">{track ? artistNames(track) : t('web.downloads.offlineBadge')}</span>
              </span>
              <span className="hidden rounded-full bg-[var(--fill)] px-2 py-0.5 text-[11px] sm:inline">{t('web.downloads.offlineBadge')}</span>
              {track && (
                <button type="button" className="shrink-0 rounded-full bg-[var(--accent)] px-3 py-1.5 text-[12px] font-bold text-[var(--accent-ink)]" onClick={() => void play({ queue: [track], index: 0, source: 'offline' })}>
                  {t('web.downloads.playOffline')}
                </button>
              )}
              <button type="button" className="shrink-0 px-2 text-[12px] text-[var(--ink-dim)] hover:text-[var(--ink)]" onClick={async () => { await removeOffline(id); setIds((prev) => (prev ?? []).filter((x) => x !== id)); }}>
                {t('web.downloads.remove')}
              </button>
            </div>
          );
        })}
      </Glass>
    </div>
  );
}
