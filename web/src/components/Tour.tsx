import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { createPortal } from 'react-dom';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useI18n } from '@/i18n';
import { hasSeenTour, markTourSeen, TOUR_REPLAY_EVENT, resolveStepRect, TOUR_STEPS } from '@/lib/tour';
import { haptic } from '@/lib/telegram';
import { useUi } from '@/store/ui';

const GAP = 12;
const PAD = 8;
const RADIUS = 16;

function usePrefersReducedMotion(): boolean {
  const [v, setV] = useState(false);
  useEffect(() => {
    const m = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setV(m.matches);
    on();
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return v;
}

export default function Tour() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const lowPerf = useUi((s) => s.lowPerf);
  const reduced = usePrefersReducedMotion();
  const [ready, setReady] = useState(false);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [placement, setPlacement] = useState(TOUR_STEPS[0]?.placement ?? 'center');
  const [tooltipPos, setTooltipPos] = useState<{ top?: number; bottom?: number; left: number } | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const skipRef = useRef<HTMLButtonElement>(null);

  const step = TOUR_STEPS[index] ?? TOUR_STEPS[0]!;
  const isCenter = !rect;

  // Auto-open only once: hasSeenTour() guards it.
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (cancelled) return;
      if (hasSeenTour()) return;
      setReady(true);
    }, 600);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // Replay without reload: Settings → "Show tour" (tab More). Listens for
  // `window.dispatchEvent(new CustomEvent(TOUR_REPLAY_EVENT))`.
  useEffect(() => {
    const replay = () => {
      setIndex(0);
      setRect(null);
      setPlacement(TOUR_STEPS[0]?.placement ?? 'center');
      setTooltipPos(null);
      setReady(true);
    };
    window.addEventListener(TOUR_REPLAY_EVENT, replay as EventListener);
    return () => window.removeEventListener(TOUR_REPLAY_EVENT, replay as EventListener);
  }, []);

  const updateRect = useCallback(() => {
    const s = TOUR_STEPS[index];
    if (!s) return;
    if (s.navigateTo && location.pathname !== s.navigateTo) {
      navigate(s.navigateTo);
      setTimeout(() => {
        const resolved = resolveStepRect(s);
        setRect(resolved.rect);
        setPlacement(resolved.placement);
        if (resolved.rect) {
          try {
            const el = document.querySelector((resolved.selector ?? s.target ?? '') as string) as HTMLElement | null;
            el?.scrollIntoView({ block: 'center', behavior: reduced || lowPerf ? 'auto' : 'smooth' });
          } catch { /* ignore */ }
        }
      }, 220);
      return;
    }
    const resolved = resolveStepRect(s);
    setRect(resolved.rect);
    setPlacement(resolved.placement);
    if (resolved.rect) {
      try {
        const el = document.querySelector((resolved.selector ?? s.target ?? '') as string) as HTMLElement | null;
        el?.scrollIntoView({ block: 'center', behavior: reduced || lowPerf ? 'auto' : 'smooth' });
      } catch { /* ignore */ }
    }
  }, [index, location.pathname, navigate, reduced, lowPerf]);

  useLayoutEffect(() => {
    if (!ready) return;
    updateRect();
  }, [ready, updateRect]);

  useEffect(() => {
    if (!ready) return;
    const on = () => {
      let raf = 0;
      const tick = () => {
        raf = 0;
        const s = TOUR_STEPS[index];
        if (!s) return;
        const r = resolveStepRect(s);
        setRect(r.rect);
        setPlacement(r.placement);
      };
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    window.addEventListener('resize', on);
    window.addEventListener('scroll', on, { passive: true, capture: true } as AddEventListenerOptions);
    return () => {
      window.removeEventListener('resize', on);
      window.removeEventListener('scroll', on, true);
    };
  }, [ready, index]);

  useLayoutEffect(() => {
    if (!ready) return;
    if (!rect) { setTooltipPos({ left: 16 }); return; }
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const holeTop = Math.max(PAD, rect.top - PAD);
    const holeBottom = Math.min(vh - PAD, rect.bottom + PAD);
    const ttH = tooltipRef.current?.offsetHeight ?? 220;
    const place = placement;
    let top: number | undefined;
    let bottom: number | undefined;
    if (place === 'bottom') {
      top = holeBottom + GAP;
      if (top + ttH > vh - 12) { bottom = vh - holeTop + GAP; top = undefined; }
    } else if (place === 'top') {
      bottom = vh - holeTop + GAP;
      if (holeTop - GAP - ttH < 12) { top = holeBottom + GAP; bottom = undefined; }
    } else {
      top = Math.max(16, Math.min(vh - ttH - 16, (vh - ttH) / 2));
    }
    const left = Math.max(16, Math.min(vw - 16 - 320, (vw - 320) / 2));
    if (top !== undefined) setTooltipPos({ top, left });
    else if (bottom !== undefined) setTooltipPos({ bottom, left });
    else setTooltipPos({ left });
  }, [ready, rect, placement, index]);

  const close = useCallback(() => {
    markTourSeen();
    setReady(false);
    try { haptic('light'); } catch { /* ignore */ }
  }, []);

  const next = useCallback(() => {
    try { haptic('select'); } catch { /* ignore */ }
    if (index >= TOUR_STEPS.length - 1) { close(); return; }
    setIndex((i) => i + 1);
  }, [index, close]);

  const skip = useCallback(() => {
    try { haptic('light'); } catch { /* ignore */ }
    close();
  }, [close]);

  useEffect(() => {
    if (!ready) return;
    const isRtl = document.dir === 'rtl' || document.documentElement.dir === 'rtl';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); skip(); return; }
      if (e.key === 'ArrowRight') { e.preventDefault(); if (isRtl) next(); else setIndex((i) => Math.max(0, i - 1)); return; }
      if (e.key === 'ArrowLeft') { e.preventDefault(); if (isRtl) setIndex((i) => Math.max(0, i - 1)); else next(); return; }
      if (e.key === 'Tab') {
        const a = skipRef.current; const b = nextRef.current;
        if (!a || !b) return;
        const cur = document.activeElement;
        if (e.shiftKey) { if (cur === a) { e.preventDefault(); b.focus(); } }
        else { if (cur === b) { e.preventDefault(); a.focus(); } }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [ready, next, skip]);

  useEffect(() => {
    if (!ready) return;
    const id = setTimeout(() => nextRef.current?.focus(), 120);
    return () => clearTimeout(id);
  }, [ready, index]);

  if (!ready) return null;

  const overlayBg: React.CSSProperties = lowPerf
    ? { background: 'color-mix(in oklab, var(--bg-0) 72%, transparent)' }
    : { background: 'color-mix(in oklab, var(--bg-0) 72%, transparent)', backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)' };

  const holeStyle: React.CSSProperties | null = rect ? {
    position: 'fixed',
    left: Math.max(0, rect.left - PAD),
    top: Math.max(0, rect.top - PAD),
    width: rect.width + PAD * 2,
    height: rect.height + PAD * 2,
    borderRadius: RADIUS,
    border: '2px solid var(--accent)',
    boxShadow: '0 0 0 1px color-mix(in oklab, var(--accent) 40%, transparent), 0 8px 32px color-mix(in oklab, var(--accent) 22%, transparent)',
    pointerEvents: 'none',
  } : null;

  // isCenter used `transform: translate(-50%,-50%)` which framer-motion overwrites
  // (it owns `transform`), so the card ended up with its top-left at 50%/50% —
  // visibly off-center to the bottom-right. Center with inset+margin instead.
  const tipStyle: React.CSSProperties = isCenter
    ? {
      width: 'calc(100vw - 32px)',
      maxWidth: 320,
      left: 0,
      right: 0,
      top: 0,
      bottom: 0,
      margin: 'auto',
      height: 'fit-content',
      maxHeight: 'calc(100vh - 32px)',
    }
    : {
      width: 'calc(100vw - 32px)',
      maxWidth: 320,
      left: tooltipPos?.left ?? 16,
      ...(tooltipPos?.top !== undefined ? { top: tooltipPos.top } : {}),
      ...(tooltipPos?.bottom !== undefined ? { bottom: tooltipPos.bottom } : {}),
    };

  const hasHole = Boolean(rect && holeStyle);
  const vw = typeof window !== 'undefined' ? window.innerWidth : 390;
  const topH = rect ? Math.max(0, rect.top - PAD) : 0;
  const bottomTop = rect ? rect.bottom + PAD : 0;
  const leftW = rect ? Math.max(0, rect.left - PAD) : 0;
  const rightW = rect ? Math.max(0, vw - rect.right - PAD) : 0;
  const midH = rect ? rect.height + PAD * 2 : 0;
  const midTop = rect ? Math.max(0, rect.top - PAD) : 0;

  const content = (
    <MotionConfig reducedMotion={reduced ? 'always' : 'never'}>
      <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-body" data-testid="tour-overlay">
        {hasHole ? (
          <>
            <div style={{ position: 'fixed', left: 0, top: 0, right: 0, height: topH, ...overlayBg }} aria-hidden />
            <div style={{ position: 'fixed', left: 0, top: bottomTop, right: 0, bottom: 0, ...overlayBg }} aria-hidden />
            <div style={{ position: 'fixed', left: 0, top: midTop, width: leftW, height: midH, ...overlayBg }} aria-hidden />
            <div style={{ position: 'fixed', right: 0, top: midTop, width: rightW, height: midH, ...overlayBg }} aria-hidden />
            <motion.div layout={Boolean(!reduced && !lowPerf)} transition={reduced ? { duration: 0 } : { type: 'spring', stiffness: 320, damping: 30 }} style={holeStyle ?? undefined} aria-hidden />
          </>
        ) : (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} style={overlayBg} className="absolute inset-0" aria-hidden onClick={skip} />
        )}
        <button type="button" aria-label="Skip" onClick={skip} className="absolute inset-0" style={{ background: 'transparent' }} tabIndex={-1} />
        <AnimatePresence mode="wait">
          <motion.div
            key={String(index)}
            ref={tooltipRef}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.98 }}
            animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: -6, scale: 0.98 }}
            transition={reduced ? { duration: 0.12 } : { duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            className="fixed z-[61] max-w-[320px] rounded-[20px] border border-[var(--separator)] bg-[var(--card)] p-4 shadow-xl"
            style={tipStyle}
            role="document"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2">
              <h2 id="tour-title" className="text-[15px] font-bold leading-6">{t(step.titleKey as never)}</h2>
              <span className="shrink-0 text-[11px] font-medium text-[var(--ink-faint)]">{index + 1} / {TOUR_STEPS.length}</span>
            </div>
            <p id="tour-body" className="mt-1.5 text-[13px] leading-6 text-[var(--ink-dim)]">{t(step.bodyKey as never)}</p>
            <div className="mt-3 flex items-center gap-1.5" aria-hidden>
              {TOUR_STEPS.map((s, i) => (
                <span key={s.id} className={i === index ? 'h-1.5 w-5 rounded-full bg-[var(--accent)]' : 'h-1.5 w-1.5 rounded-full bg-[var(--fill-strong)]'} />
              ))}
            </div>
            <div className="mt-4 flex items-center justify-between gap-2">
              <button ref={skipRef} type="button" onClick={skip} className="rounded-full bg-[var(--fill)] px-4 py-2 text-[13px] font-medium">{t('tour.skip' as never)}</button>
              <button ref={nextRef} type="button" onClick={next} className="rounded-full bg-[var(--accent)] px-5 py-2 text-[13px] font-bold text-[var(--accent-ink)]">{index >= TOUR_STEPS.length - 1 ? (t('tour.done.cta' as never)) : (t('tour.next' as never))}</button>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>
    </MotionConfig>
  );

  return createPortal(content, document.body);
}