import { useCallback, useEffect, useRef } from 'react';

/**
 * Minimal spatial/D-pad navigation.
 * - Container holds focusable items with `[data-focusable]` or any `[tabindex]`.
 * - Roving tabindex: only focused item is 0, rest -1 (optional wrap).
 * - Arrow keys move focus, Enter/Space clicks, Escape dispatches `tv:back`.
 * No external dep; <3KB, TV-only via `tv.css` selectors.
 */
export function useSpatialNav<T extends HTMLElement>(opts?: { wrap?: boolean }) {
  const wrap = opts?.wrap ?? true;
  const containerRef = useRef<T>(null);

  const focusables = useCallback((): HTMLElement[] => {
    const root = containerRef.current;
    if (!root) return [];
    const nodes = Array.from(root.querySelectorAll<HTMLElement>('[data-focusable], [tabindex]:not([tabindex=\"-1\"])'));
    // Prefer data-focusable, but keep any focusable already in DOM
    // Filter disabled/hidden
    return nodes.filter((n) => !n.hasAttribute('disabled') && n.getAttribute('aria-hidden') !== 'true' && n.offsetParent !== null);
  }, []);

  const move = useCallback(
    (dir: 1 | -1) => {
      const items = focusables();
      if (!items.length) return;
      const active = document.activeElement as HTMLElement | null;
      let idx = items.findIndex((n) => n === active);
      if (idx === -1) {
        // If focus is inside container but not on an item, pick nearest
        items[0]?.focus();
        return;
      }
      let next = idx + dir;
      if (next < 0 || next >= items.length) {
        if (!wrap) return;
        next = next < 0 ? items.length - 1 : 0;
      }
      items[next]?.focus();
    },
    [focusables, wrap],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const key = e.key;
      if (key === 'ArrowRight' || key === 'ArrowDown') {
        e.preventDefault();
        move(1);
      } else if (key === 'ArrowLeft' || key === 'ArrowUp') {
        e.preventDefault();
        move(-1);
      } else if (key === 'Enter' || key === ' ') {
        // Let native click happen; ensure focus item is activated
        const active = document.activeElement as HTMLElement | null;
        if (active && containerRef.current?.contains(active)) {
          // Space would scroll — prevent, then click
          if (key === ' ') e.preventDefault();
          active.click();
        }
      } else if (key === 'Escape' || key === 'Backspace') {
        // TV Back — bubble as custom event so Shell/FullPlayer can close
        const root = containerRef.current;
        if (root && root.contains(document.activeElement as Node | null)) {
          root.dispatchEvent(new CustomEvent('tv:back', { bubbles: true }));
        }
      }
    },
    [move],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener('keydown', onKeyDown as EventListener);
    return () => el.removeEventListener('keydown', onKeyDown as EventListener);
  }, [onKeyDown]);

  // Keep roving tabindex in sync when DOM changes (optional light touch)
  useEffect(() => {
    const root = containerRef.current;
    if (!root || typeof MutationObserver === 'undefined') return;
    let raf = 0;
    const sync = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const items = focusables();
        const active = document.activeElement as HTMLElement | null;
        const hasFocusInside = !!active && root.contains(active);
        items.forEach((n) => {
          if (hasFocusInside) n.tabIndex = n === active ? 0 : -1;
          else n.tabIndex = n.tabIndex < 0 ? -1 : 0;
        });
        // If nothing focused and container is focus scope, make first 0
        if (!hasFocusInside && items.length) {
          const firstZero = items.find((n) => n.tabIndex === 0);
          if (!firstZero) items[0]!.tabIndex = 0;
        }
      });
    };
    sync();
    const mo = new MutationObserver(sync);
    mo.observe(root, { childList: true, subtree: true });
    return () => {
      mo.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [focusables]);

  return { containerRef } as const;
}
