import { useEffect, useState } from 'react';

import { Glass, Spinner, EmptyState } from '@/components/ui';
import { useI18n } from '@/i18n';
import { listOffline, offlineUrl, removeOffline } from '@/player/engine';
import { usePlayer } from '@/store/player';

export function Downloads() {
  const { t } = useI18n();
  const [ids, setIds] = useState<number[] | null>(null);
  const play = usePlayer((s) => s.play);
  useEffect(() => { void listOffline().then(setIds); }, []);
  if (ids === null) return <div className="grid place-items-center py-20"><Spinner /></div>;
  if (ids.length === 0) return <EmptyState title={t('web.downloads.title')} body={t('web.downloads.empty')} />;
  return (
    <div className="px-4 pt-4">
      <h1 className="mb-3 text-[18px] font-bold">{t('web.downloads.title')}</h1>
      <Glass className="divide-y divide-[var(--separator)]">
        {ids.map((id) => (
          <div key={id} className="flex items-center gap-3 px-3 py-3">
            <span className="flex-1 text-[14px]">#{id}</span>
            <span className="rounded-full bg-[var(--fill)] px-2 py-0.5 text-[11px]">{t('web.downloads.offlineBadge')}</span>
            <button type="button" className="text-[13px] text-[var(--accent)]" onClick={async () => {
              const url = await offlineUrl(id);
              if (url) { const audio = new Audio(url); void audio.play(); }
              // also wire to player queue: use track lookup
              void play;
            }}>{t('web.downloads.playOffline')}</button>
            <button type="button" className="text-[13px] text-[var(--ink-dim)]" onClick={async () => { await removeOffline(id); setIds((prev) => (prev ?? []).filter((x) => x !== id)); }}>{t('web.downloads.remove')}</button>
          </div>
        ))}
      </Glass>
    </div>
  );
}
