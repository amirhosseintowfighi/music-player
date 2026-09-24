import { AnimatePresence, motion } from 'framer-motion';
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';

import { useI18n } from '@/i18n';
import { coverColors } from '@/lib/format';
import { haptic, openExternalLink } from '@/lib/telegram';
import { useUi } from '@/store/ui';

export const spring = { type: 'spring', stiffness: 380, damping: 34, mass: 0.9 } as const;

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

/**
 * The glass surface. `spec` moves the specular highlight (0..1); lists pass a value
 * derived from the item's position so the sheen travels while scrolling.
 */
export function Glass({
  children,
  className,
  strong,
  spec = 0.3,
  style,
  onClick,
  as = 'div',
}: {
  children?: ReactNode;
  className?: string;
  strong?: boolean;
  spec?: number;
  style?: CSSProperties;
  onClick?: () => void;
  as?: 'div' | 'button' | 'li';
}) {
  const Tag = motion[as] as typeof motion.div;
  return (
    <Tag
      // Content sits on a solid grouped surface, the way every Apple list does:
      // blur belongs to chrome that floats over scrolling content (the tab bar and
      // the now-playing bar, which use the `glass` utilities directly), not to the
      // thing being read. Translucent rows were the reason the list was hard to read.
      className={cx(
        'rounded-[var(--radius-glass)] bg-[var(--card)]',
        strong && 'bg-[var(--card-strong)] shadow-[0_1px_3px_rgba(0,0,0,0.06)]',
        className,
      )}
      style={{ ...style, ['--spec' as string]: spec }}
      onClick={
        onClick
          ? () => {
              haptic('light');
              onClick();
            }
          : undefined
      }
      whileTap={onClick ? { scale: 0.985 } : undefined}
      transition={spring}
    >
      {children}
    </Tag>
  );
}

/** Artwork with a deterministic gradient placeholder while (or instead of) an image. */
export function Cover({
  src,
  seed,
  size = 48,
  radius = 12,
  className,
  glyph = '♪',
}: {
  src?: string | null;
  seed: number | string;
  size?: number;
  radius?: number;
  className?: string;
  glyph?: string;
}) {
  const [failed, setFailed] = useState(false);
  const [c1, c2] = coverColors(seed);
  return (
    <div
      className={cx('relative shrink-0 overflow-hidden grid place-items-center', className)}
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        background: `linear-gradient(140deg, ${c1}, ${c2})`,
      }}
    >
      {src && !failed ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <span style={{ fontSize: size * 0.4, opacity: 0.85 }}>{glyph}</span>
      )}
    </div>
  );
}

export function SectionHead({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) {
  return (
    <div className="mt-6 mb-2.5 flex items-center justify-between px-1">
      <h2 className="text-[16px] font-bold">{title}</h2>
      {action && (
        <button type="button" className="text-[13px] text-[var(--accent)]" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}

export function Spinner({ size = 20 }: { size?: number }) {
  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-block animate-spin rounded-full border-2 border-[var(--separator)] border-t-[var(--accent)]"
      style={{ width: size, height: size }}
    />
  );
}

export function EmptyState({ title, body, cta, onCta }: { title: string; body?: string; cta?: string; onCta?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 px-8 py-14 text-center">
      <div className="text-4xl opacity-60">♪</div>
      <p className="text-[15px] font-bold">{title}</p>
      {body && <p className="text-[13px] leading-6 text-[var(--ink-dim)]">{body}</p>}
      {cta && (
        <button
          type="button"
          onClick={onCta}
          className="mt-2 rounded-full bg-[var(--accent)] px-5 py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)]"
        >
          {cta}
        </button>
      )}
    </div>
  );
}

/** Bottom sheet used for filters, the queue, track actions and the paywall. */
export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-[color-mix(in_oklab,var(--bg-0)_70%,transparent)]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={title}
            // A sheet is content, not chrome: solid, so the list inside it is read
            // against a known colour instead of against whatever it covers.
            className="fixed inset-x-0 bottom-0 z-50 max-h-[85vh] overflow-y-auto rounded-t-[var(--radius-glass-lg)] bg-[var(--card)] px-4 pt-3 shadow-[0_-8px_30px_rgba(0,0,0,0.18)]"
            style={{ paddingBottom: 'calc(20px + var(--safe-bottom))' }}
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={spring}
            drag="y"
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0, bottom: 0.4 }}
            onDragEnd={(_, info) => {
              if (info.offset.y > 120) onClose();
            }}
          >
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-[var(--fill-strong)]" />
            {title && <h3 className="mb-3 px-1 text-[15px] font-bold">{title}</h3>}
            {children}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

export function Toasts() {
  const toasts = useUi((s) => s.toasts);
  const dismiss = useUi((s) => s.dismissToast);
  return (
    <div className="pointer-events-none fixed inset-x-3 z-60 flex flex-col items-center gap-2" style={{ bottom: 'calc(var(--chrome-h) + 12px)' }}>
      <AnimatePresence>
        {toasts.map((toast) => (
          <motion.button
            key={toast.id}
            type="button"
            className={cx(
              'glass glass-edge glass-strong pointer-events-auto max-w-sm rounded-full px-4 py-2.5 text-[13px]',
              toast.kind === 'error' && 'text-[#ff9a9a]',
              toast.kind === 'success' && 'text-[var(--accent)]',
            )}
            initial={{ opacity: 0, y: 14, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={spring}
            onClick={() => dismiss(toast.id)}
          >
            {toast.text}
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  );
}

/** Aurora: the artwork-tinted background layer. */
export function Aurora() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed -inset-x-[20%] -top-[20%] -z-10 h-[70vh] transition-[background] duration-700"
      style={{
        background:
          'radial-gradient(40% 50% at 20% 20%, color-mix(in oklab, var(--art-1) 55%, transparent), transparent 70%),' +
          'radial-gradient(45% 55% at 80% 10%, color-mix(in oklab, var(--art-2) 45%, transparent), transparent 70%),' +
          'radial-gradient(60% 60% at 50% 60%, color-mix(in oklab, var(--art-3) 28%, transparent), transparent 75%)',
        filter: 'blur(40px) saturate(150%)',
      }}
    />
  );
}

/** Infinite-scroll sentinel. */
export function LoadMore({ onVisible, enabled }: { onVisible: () => void; enabled: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || !enabled || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onVisible();
      },
      { rootMargin: '400px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [onVisible, enabled]);
  return <div ref={ref} className="h-8" />;
}

export function ErrorNote({ onRetry }: { onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <p className="text-[14px] text-[var(--ink-dim)]">{t('app.error')}</p>
      {onRetry && (
        <button type="button" className="text-[13px] text-[var(--accent)]" onClick={onRetry}>
          {t('app.retry')}
        </button>
      )}
    </div>
  );
}

/** Who made this. Shown at the bottom of Settings and on the join screen. */
export function Credit() {
  const { t } = useI18n();
  return (
    // One line, its own space, and the two halves kept on one baseline: the credit
    // was squeezed between a list and the tab bar and read as a broken sentence.
    <p className="mt-6 mb-2 flex flex-wrap items-baseline justify-center gap-1 px-4 pb-2 text-center text-[12px] leading-6 text-[var(--ink-dim)]">
      <span>{t('credit.by')}</span>
      <button
        type="button"
        className="font-semibold text-[var(--accent)] underline underline-offset-4"
        onClick={() => openExternalLink('https://virgule.studio')}
      >
        {t('credit.virgule')}
      </button>
    </p>
  );
}
