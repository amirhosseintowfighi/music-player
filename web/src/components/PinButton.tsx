import { motion } from 'framer-motion';

import { ApiError } from '@/api/client';
import { MAX_PINS, usePins, useTogglePin, type Pin } from '@/api/listening';
import { PinIcon } from '@/components/icons';
import { bouncy, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { useUi } from '@/store/ui';

/** Pin to the top of the library (up to four, like Spotify). */
export function PinButton({ kind, refId, className }: { kind: Pin['kind']; refId: number; className?: string }) {
  const { t } = useI18n();
  const pins = usePins();
  const toggle = useTogglePin();
  const toast = useUi((s) => s.toast);
  const pinned = pins.data?.some((pin) => pin.kind === kind && pin.ref_id === refId) ?? false;
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.85 }}
      transition={bouncy}
      aria-pressed={pinned}
      aria-label={pinned ? t('pin.remove') : t('pin.add')}
      disabled={toggle.isPending || !pins.data}
      onClick={() =>
        toggle.mutate(
          { kind, refId, pinned },
          {
            onSuccess: () => toast(pinned ? t('pin.removed') : t('pin.added')),
            onError: (error) =>
              toast(
                error instanceof ApiError && error.status === 409 ? t('pin.full', { count: MAX_PINS }) : t('app.error'),
                'error',
              ),
          },
        )
      }
      className={cx('p-1.5', pinned ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]', className)}
    >
      <PinIcon size={20} filled={pinned} />
    </motion.button>
  );
}
