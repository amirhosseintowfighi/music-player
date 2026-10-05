/**
 * Connect's device picker: where the music is, and where it could go.
 *
 * "This device" first, then every other copy of the app this listener has open.
 * Tapping one hands the music over; a device that is already playing gets its own
 * transport, so the phone can pause the laptop.
 */
import { motion } from 'framer-motion';
import { useState } from 'react';

import type { Device } from '@/api/listening';
import { DesktopIcon, DevicesIcon, NextIcon, PhoneIcon, PrevIcon } from '@/components/icons';
import { Equalizer, PlayPauseGlyph, Sheet, Spinner, bouncy, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { artistNames } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { thisDevice, useConnect } from '@/store/connect';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

function KindIcon({ kind, size = 22 }: { kind: Device['kind']; size?: number }) {
  return kind === 'phone' || kind === 'tablet' ? <PhoneIcon size={size} /> : <DesktopIcon size={size} />;
}

function RemoteRow({ device, onPicked }: { device: Device; onPicked: () => void }) {
  const { t } = useI18n();
  const hasCurrent = usePlayer((s) => Boolean(s.current));
  const inJam = usePlayer((s) => Boolean(s.remote));
  const { transfer, control } = useConnect.getState();
  const toast = useUi((s) => s.toast);
  const [busy, setBusy] = useState(false);

  const handOver = async () => {
    if (!hasCurrent || inJam || busy) return;
    haptic('medium');
    setBusy(true);
    const ok = await transfer(device.id);
    setBusy(false);
    if (ok) {
      toast(t('connect.movedTo', { name: device.name }), 'success');
      onPicked();
    } else {
      toast(t('connect.failed'), 'error');
    }
  };

  return (
    <li className="rounded-2xl bg-[var(--fill)] p-3">
      <button
        type="button"
        onClick={() => void handOver()}
        disabled={!hasCurrent || inJam}
        className="flex w-full items-center gap-3 text-start disabled:opacity-100"
      >
        <span className={cx('grid h-10 w-10 place-items-center rounded-xl bg-[var(--fill-strong)]', device.playing && 'text-[var(--accent)]')}>
          <KindIcon kind={device.kind} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[14px] font-semibold">{device.name}</span>
          <span className="flex items-center gap-1.5 truncate text-[11.5px] text-[var(--ink-dim)]">
            {device.playing && <Equalizer playing size={10} />}
            {device.track
              ? `${device.track.title} · ${artistNames(device.track)}`
              : t('connect.idle')}
          </span>
        </span>
        {busy && <Spinner size={16} />}
      </button>
      {device.track && (
        <div dir="ltr" className="mt-2 flex items-center justify-center gap-6">
          <motion.button
            type="button"
            aria-label="previous"
            whileTap={{ scale: 0.8 }}
            transition={bouncy}
            onClick={() => void control(device.id, 'previous')}
            className="p-1.5"
          >
            <PrevIcon size={20} />
          </motion.button>
          <motion.button
            type="button"
            aria-label={device.playing ? t('common.pause') : t('common.play')}
            whileTap={{ scale: 0.8 }}
            transition={bouncy}
            onClick={() => void control(device.id, device.playing ? 'pause' : 'play')}
            className="grid h-10 w-10 place-items-center rounded-full bg-[var(--fill-strong)]"
          >
            <PlayPauseGlyph playing={device.playing} size={20} />
          </motion.button>
          <motion.button
            type="button"
            aria-label="next"
            whileTap={{ scale: 0.8 }}
            transition={bouncy}
            onClick={() => void control(device.id, 'next')}
            className="p-1.5"
          >
            <NextIcon size={20} />
          </motion.button>
        </div>
      )}
    </li>
  );
}

export function DevicesSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const devices = useConnect((s) => s.devices);
  const playingHere = usePlayer((s) => s.isPlaying);
  const inJam = usePlayer((s) => Boolean(s.remote));
  const me = thisDevice();

  return (
    <Sheet open={open} onClose={onClose} title={t('connect.title')}>
      <div className="mb-3 flex items-center gap-3 rounded-2xl bg-[color-mix(in_oklab,var(--accent)_14%,transparent)] p-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--accent)] text-[var(--accent-ink)]">
          <KindIcon kind={me.kind} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[11.5px] text-[var(--accent)]">{t('connect.thisDevice')}</span>
          <span className="block truncate text-[14px] font-semibold">{me.name}</span>
        </span>
        {playingHere && <Equalizer playing size={12} className="text-[var(--accent)]" />}
      </div>

      {devices.length > 0 ? (
        <>
          <p className="mb-2 px-1 text-[12px] text-[var(--ink-dim)]">{t('connect.others')}</p>
          <ul className="flex flex-col gap-2">
            {devices.map((device) => (
              <RemoteRow key={device.id} device={device} onPicked={onClose} />
            ))}
          </ul>
          {inJam && <p className="mt-3 px-1 text-[12px] text-[var(--ink-faint)]">{t('connect.inJam')}</p>}
        </>
      ) : (
        <div className="flex flex-col items-center gap-2 px-6 py-8 text-center">
          <DevicesIcon size={34} className="text-[var(--ink-faint)]" />
          <p className="text-[14px] font-semibold">{t('connect.none')}</p>
          <p className="text-[12.5px] leading-6 text-[var(--ink-dim)]">{t('connect.noneHint')}</p>
        </div>
      )}
    </Sheet>
  );
}
