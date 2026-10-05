/** Tour persistence + step definitions – 7 steps, Liquid Glass, RTL-aware */
export const TOUR_KEY = 'tmusic.tour.v1';

export function hasSeenTour(): boolean {
  try {
    return localStorage.getItem(TOUR_KEY) === '1';
  } catch {
    return true;
  }
}
export function markTourSeen(): void {
  try {
    localStorage.setItem(TOUR_KEY, '1');
  } catch { /* ignore */ }
}
export function resetTour(): void {
  try {
    localStorage.removeItem(TOUR_KEY);
  } catch { /* ignore */ }
}

/** Dispatched on `window` to re-open the tour without a page reload (Settings → Show tour). */
export const TOUR_REPLAY_EVENT = 'tour:replay' as const;

export type TourPlacement = 'top' | 'bottom' | 'center';

export interface TourStep {
  id: string;
  titleKey: string;
  bodyKey: string;
  placement: TourPlacement;
  /** primary selector */
  target?: string;
  /** fallback selector when primary not found */
  fallbackTarget?: string;
  fallbackPlacement?: TourPlacement;
  navigateTo?: string;
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    titleKey: 'tour.welcome.title',
    bodyKey: 'tour.welcome.body',
    placement: 'center',
  },
  {
    id: 'home',
    titleKey: 'tour.home.title',
    bodyKey: 'tour.home.body',
    placement: 'bottom',
    target: '[data-tour="home-continue"]',
    fallbackTarget: '[data-tour="home-header"]',
    fallbackPlacement: 'center',
    navigateTo: '/',
  },
  {
    id: 'search',
    titleKey: 'tour.search.title',
    bodyKey: 'tour.search.body',
    placement: 'bottom',
    target: '[data-tour="search-input"]',
    fallbackTarget: '[data-tour="tab-search"]',
    fallbackPlacement: 'center',
    navigateTo: '/search',
  },
  {
    id: 'play',
    titleKey: 'tour.play.title',
    bodyKey: 'tour.play.body',
    placement: 'top',
    target: '[data-tour="track-row"]',
    fallbackTarget: '[data-tour="miniplayer"]',
    fallbackPlacement: 'center',
  },
  {
    id: 'library',
    titleKey: 'tour.library.title',
    bodyKey: 'tour.library.body',
    placement: 'bottom',
    target: '[data-tour="library-tabs"]',
    fallbackTarget: '[data-tour="tab-library"]',
    fallbackPlacement: 'center',
    navigateTo: '/library',
  },
  {
    id: 'channels',
    titleKey: 'tour.channels.title',
    bodyKey: 'tour.channels.body',
    placement: 'bottom',
    target: '[data-tour="add-channel"]',
    fallbackTarget: '[data-tour="home-featured"]',
    fallbackPlacement: 'center',
    navigateTo: '/library',
  },
  {
    id: 'done',
    titleKey: 'tour.done.title',
    bodyKey: 'tour.done.body',
    placement: 'center',
  },
];

export function getTargetRect(selector: string): DOMRect | null {
  try {
    const el = document.querySelector(selector) as HTMLElement | null;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    return r;
  } catch {
    return null;
  }
}

export function resolveStepRect(step: TourStep): { rect: DOMRect | null; placement: TourPlacement; selector: string | null } {
  if (!step.target) return { rect: null, placement: step.placement, selector: null };
  const primary = getTargetRect(step.target);
  if (primary) return { rect: primary, placement: step.placement, selector: step.target };
  if (step.fallbackTarget) {
    const fb = getTargetRect(step.fallbackTarget);
    if (fb) return { rect: fb, placement: step.fallbackPlacement ?? 'center', selector: step.fallbackTarget };
  }
  return { rect: null, placement: 'center', selector: null };
}

export function getStepTarget(step: TourStep): string | null {
  return step.target ?? null;
}
