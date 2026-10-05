import { useState } from 'react';

import { ApiError } from '@/api/client';
import { useCreatePlaylist } from '@/api/playlists';
import { Sheet, Spinner } from '@/components/ui';
import { useI18n } from '@/i18n';
import { useUi } from '@/store/ui';

export function CreatePlaylistSheet({
  open,
  onClose,
  trackIds = [],
}: {
  open: boolean;
  onClose: () => void;
  trackIds?: number[];
}) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const create = useCreatePlaylist();
  const toast = useUi((s) => s.toast);
  const showUpsell = useUi((s) => s.showUpsell);

  return (
    <Sheet open={open} onClose={onClose} title={t('playlist.new')}>
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t('playlist.namePlaceholder')}
          aria-label={t('playlist.name')}
          className="min-w-0 flex-1 rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[14px] outline-none"
        />
        <button
          type="button"
          disabled={!name.trim() || create.isPending}
          className="min-w-20 rounded-xl bg-[var(--accent)] px-4 text-[13.5px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
          onClick={() =>
            create.mutate(
              { name: name.trim(), track_ids: trackIds },
              {
                onSuccess: () => {
                  toast(t('playlist.created'), 'success');
                  setName('');
                  onClose();
                },
                onError: (error) => {
                  if (error instanceof ApiError && error.isPlanLimit) {
                    onClose();
                    showUpsell({ kind: 'playlists', limit: Number(error.details.limit ?? 0) });
                  } else {
                    toast(t('app.error'), 'error');
                  }
                },
              },
            )
          }
        >
          {create.isPending ? <Spinner size={16} /> : t('common.save')}
        </button>
      </div>
    </Sheet>
  );
}
