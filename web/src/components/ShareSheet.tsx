import type { Track } from '@/api/client';
import { Sheet } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { buildShareUrl, shareTrack } from '@/lib/share';
import { drawCard } from '@/lib/shareCard';
import { BOT_USERNAME, openExternalLink, openTelegramLink } from '@/lib/telegram';
import { useUi } from '@/store/ui';

export function ShareSheet({
  open,
  onClose,
  track,
  coverSrc,
}: {
  open: boolean;
  onClose: () => void;
  track: Track | null;
  coverSrc?: string;
}) {
  const { t } = useI18n();
  const toast = useUi((s) => s.toast);

  if (!track) return null;

  const url = buildShareUrl(track.id, BOT_USERNAME);
  const text = `${track.title} — ${artistNames(track) || ''}`.trim();

  return (
    <Sheet open={open} onClose={onClose} title={t('share.choose')}>
      <div className="flex flex-col gap-2 pb-2">
        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-xl bg-[var(--fill)] px-3.5 py-3 text-start text-[13.5px]"
          onClick={() => {
            openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`);
            onClose();
          }}
        >
          ✈️ {t('share.telegram')}
        </button>

        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-xl bg-[var(--fill)] px-3.5 py-3 text-start text-[13.5px]"
          onClick={() => {
            const waUrl = `https://wa.me/?text=${encodeURIComponent(`${text} ${url}`)}`;
            openExternalLink(waUrl);
            onClose();
          }}
        >
          💬 {t('share.whatsapp')}
        </button>

        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-xl bg-[var(--fill)] px-3.5 py-3 text-start text-[13.5px]"
          onClick={async () => {
            try {
              if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(url);
                toast(t('share.copied'), 'success');
              } else {
                await navigator.clipboard.writeText(url);
                toast(t('share.copied'), 'success');
              }
            } catch {
              toast(t('app.error'), 'error');
            }
            onClose();
          }}
        >
          🔗 {t('share.copyLink')}
        </button>

        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-xl bg-[var(--fill)] px-3.5 py-3 text-start text-[13.5px]"
          onClick={async () => {
            try {
              const canvas = await drawCard(track, coverSrc, BOT_USERNAME);
              if (!canvas) {
                toast(t('app.error'), 'error');
                onClose();
                return;
              }
              const blob: Blob | null = await new Promise<Blob | null>((resolve) =>
                canvas.toBlob((b) => resolve(b), 'image/png'),
              );
              if (!blob) {
                toast(t('app.error'), 'error');
                onClose();
                return;
              }
              const href = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = href;
              a.download = `${track.title.slice(0, 40) || 'song'}.png`;
              document.body.appendChild(a);
              a.click();
              a.remove();
              setTimeout(() => URL.revokeObjectURL(href), 10_000);
              toast(t('share.saved'), 'success');
            } catch {
              toast(t('app.error'), 'error');
            }
            onClose();
          }}
        >
          ⬇️ {t('share.download')}
        </button>

        {/* Instagram Story hint: OS file share is the path there */}
        <button
          type="button"
          className="flex w-full items-center gap-3 rounded-xl bg-[var(--fill)] px-3.5 py-3 text-start text-[13.5px]"
          onClick={async () => {
            const outcome = await shareTrack(track, coverSrc);
            if (outcome === 'shared') {
              toast(t('share.saved'), 'success');
              onClose();
            } else {
              toast(t('share.storyHint'), 'success');
              onClose();
            }
          }}
        >
          📸 {t('share.storyHint')}
        </button>
      </div>
    </Sheet>
  );
}
