/**
 * What AutoMix is doing, under the title in the full player: during a transition,
 * the style and the two songs' tempo and key with the progress of the mix; between
 * transitions, the playing song's tempo and key, the way a DJ's deck shows them.
 */
import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

import type { Track } from '@/api/client';
import { useI18n } from '@/i18n';
import { camelotName } from '@/player/analysis';
import { useAudioSettings } from '@/store/audio';
import { usePlayer } from '@/store/player';

const STYLE_KEY = {
  blend: 'automix.style.blend',
  swap: 'automix.style.swap',
  echo: 'automix.style.echo',
  filter: 'automix.style.filter',
  fade: 'automix.style.fade',
} as const;

function bpm(value: number | null | undefined): string {
  return value ? String(Math.round(value)) : '—';
}

export function MixPanel({ track }: { track: Track }) {
  const { t } = useI18n();
  const on = useAudioSettings((s) => s.automix);
  const mixing = usePlayer((s) => s.mixing);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!mixing) return undefined;
    const timer = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(timer);
  }, [mixing]);
  if (!on) return null;
  if (mixing) {
    const total = Math.max(1, mixing.until - mixing.started);
    const left = Math.max(0, mixing.until - Date.now());
    return (
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className="mt-2.5 overflow-hidden rounded-2xl bg-[var(--fill)] px-3 py-2"
        role="status"
      >
        <div className="flex items-center gap-2 text-[12px]">
          <span className="font-bold text-[var(--accent)]">🎛 {t('automix.title')}</span>
          <span className="text-[var(--ink-dim)]">{t(STYLE_KEY[mixing.style])}</span>
          <span dir="ltr" className="ms-auto font-mono text-[11.5px] text-[var(--ink-dim)]">
            {bpm(mixing.fromBpm)}→{bpm(mixing.toBpm)} BPM
            {mixing.fromKey !== null && mixing.toKey !== null && ` · ${camelotName(mixing.fromKey)}→${camelotName(mixing.toKey)}`}
          </span>
        </div>
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--fill-strong)]">
          <motion.div
            className="h-full rounded-full bg-[var(--accent)]"
            initial={false}
            animate={{ width: `${Math.min(100, 100 - (left / total) * 100)}%` }}
            transition={{ duration: 0.25, ease: 'linear' }}
          />
        </div>
      </motion.div>
    );
  }
  const mix = track.mix;
  if (!mix) return null;
  return (
    <p dir="ltr" className="mt-1.5 inline-flex items-center gap-1.5 rounded-full bg-[var(--fill)] px-2.5 py-0.5 font-mono text-[11px] text-[var(--ink-dim)]">
      🎛 {bpm(mix.bpm)} BPM{mix.key !== null && mix.key !== undefined ? ` · ${camelotName(mix.key)}` : ''}
    </p>
  );
}
