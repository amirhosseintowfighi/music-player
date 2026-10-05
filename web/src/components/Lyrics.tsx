import { motion } from 'framer-motion';
import { useEffect, useMemo, useRef } from 'react';

import { activeLine, parseLrc, useLyrics } from '@/api/listening';
import { Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { haptic } from '@/lib/telegram';
import { usePlayer } from '@/store/player';

/**
 * Lyrics in the full player, the way Music shows them: big lines, the one being sung
 * lit and the rest dimmed, scrolling on their own. Tap a line to jump to it.
 *
 * Words without timings are shown as plain, scrollable text.
 */
export function LyricsView({ trackId }: { trackId: number }) {
  const { t } = useI18n();
  const query = useLyrics(trackId);
  const position = usePlayer((s) => s.position);
  const lines = useMemo(() => (query.data?.synced ? parseLrc(query.data.synced) : []), [query.data?.synced]);
  // A little ahead: a line should light up as it starts, not a beat after.
  const current = activeLine(lines, position + 0.25);
  const box = useRef<HTMLDivElement>(null);
  const userScrolled = useRef(0);

  useEffect(() => {
    const node = box.current?.querySelector<HTMLElement>(`[data-line="${current}"]`);
    // Leave the listener alone for a few seconds after they scroll themselves.
    if (!node || Date.now() - userScrolled.current < 4000) return;
    node.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [current]);

  if (query.isLoading) {
    return (
      <div className="grid h-full place-items-center">
        <Spinner />
      </div>
    );
  }
  if (!query.data?.found || (!lines.length && !query.data.plain)) {
    return (
      <div className="grid h-full place-items-center px-6 text-center text-[14px] text-[var(--ink-dim)]">
        {t('lyrics.none')}
      </div>
    );
  }

  return (
    <div
      ref={box}
      className="no-scrollbar h-full overflow-y-auto px-1 py-[35%] [mask-image:linear-gradient(transparent,#000_18%,#000_82%,transparent)]"
      onTouchMove={() => {
        userScrolled.current = Date.now();
      }}
      onWheel={() => {
        userScrolled.current = Date.now();
      }}
    >
      {lines.length > 0 ? (
        lines.map((line, index) => (
          <motion.button
            key={`${line.at}-${index}`}
            type="button"
            data-line={index}
            onClick={() => {
              haptic('select');
              userScrolled.current = 0;
              usePlayer.getState().seek(line.at);
            }}
            animate={{
              opacity: index === current ? 1 : index < current ? 0.32 : 0.5,
              scale: index === current ? 1 : 0.97,
            }}
            transition={{ type: 'spring', stiffness: 220, damping: 26 }}
            className={cx(
              'block w-full origin-[var(--origin)] py-2 text-start text-[24px] font-extrabold leading-[1.25] tracking-tight',
              !line.text && 'text-[18px]',
            )}
            style={{ ['--origin' as string]: document.dir === 'rtl' ? 'right' : 'left' }}
            dir="auto"
          >
            {line.text || '♪'}
          </motion.button>
        ))
      ) : (
        <p dir="auto" className="whitespace-pre-line text-[19px] font-bold leading-[1.6] text-[var(--ink-dim)]">
          {query.data.plain}
        </p>
      )}
      <p className="pt-6 text-[11px] text-[var(--ink-faint)]">{t('lyrics.source')}</p>
    </div>
  );
}
