import type { Channel, UserChannel } from '@/api/client';
import { Cover, Glass } from '@/components/ui';
import { useI18n } from '@/i18n';

function statusText(channel: Channel, t: ReturnType<typeof useI18n>['t']): string {
  switch (channel.status) {
    case 'indexing':
      return t('channel.status.indexing', { percent: channel.progress_pct });
    case 'pending':
      return t('channel.status.pending');
    case 'paused':
      return t('channel.status.paused');
    case 'failed':
      return t('channel.status.failed');
    case 'blacklisted':
      return t('channel.status.blacklisted');
    default:
      return t('channel.status.active', { tracks: channel.tracks_count });
  }
}

export function ChannelCard({
  channel,
  onClick,
}: {
  channel: Channel | UserChannel;
  onClick: () => void;
}) {
  const { t } = useI18n();
  const busy = channel.status === 'indexing' || channel.status === 'pending';
  return (
    <Glass as="button" className="w-[212px] shrink-0 p-3 text-start" onClick={onClick}>
      <div className="flex items-center gap-2.5">
        <Cover src={channel.avatar_url} seed={channel.username ?? channel.id} size={44} glyph="📻" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-semibold">{channel.title ?? `@${channel.username ?? ''}`}</p>
          <p className="truncate text-[11.5px] text-[var(--ink-faint)]">{statusText(channel, t)}</p>
          {busy && (
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--fill-strong)]">
              <div
                className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-500"
                style={{ width: `${Math.max(3, channel.progress_pct)}%` }}
              />
            </div>
          )}
        </div>
      </div>
    </Glass>
  );
}
