/**
 * Tour persistence + step definitions, RTL-aware.
 *
 * v2 explains what the bot is for before where things are, because "what does this
 * even do" is where people got lost. A new key shows it once to people who saw v1.
 */
export const TOUR_KEY = 'tmusic.tour.v2';

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
  { id: 'welcome', titleKey: 'tour.welcome.title', bodyKey: 'tour.welcome.body', placement: 'center' },
  {
    id: 'channels',
    titleKey: 'tour.channels.title',
    bodyKey: 'tour.channels.body',
    placement: 'bottom',
    target: '[data-tour="add-channel"]',
    fallbackTarget: '[data-tour="tab-library"]',
    fallbackPlacement: 'top',
    navigateTo: '/library',
  },
  {
    id: 'home',
    titleKey: 'tour.home.title',
    bodyKey: 'tour.home.body',
    placement: 'bottom',
    target: '[data-tour="home-continue"]',
    fallbackTarget: '[data-tour="tab-home"]',
    fallbackPlacement: 'top',
    navigateTo: '/',
  },
  {
    id: 'discover',
    titleKey: 'tour.discover.title',
    bodyKey: 'tour.discover.body',
    placement: 'top',
    target: '[data-tour="tab-discover"]',
  },
  {
    id: 'search',
    titleKey: 'tour.search.title',
    bodyKey: 'tour.search.body',
    placement: 'bottom',
    target: '[data-tour="search-input"]',
    fallbackTarget: '[data-tour="tab-search"]',
    fallbackPlacement: 'top',
    navigateTo: '/search',
  },
  {
    id: 'song',
    titleKey: 'tour.play.title',
    bodyKey: 'tour.play.body',
    placement: 'top',
    target: '[data-tour="track-row"]',
    fallbackPlacement: 'center',
  },
  {
    id: 'player',
    titleKey: 'tour.player.title',
    bodyKey: 'tour.player.body',
    placement: 'top',
    target: '[data-tour="miniplayer"]',
    fallbackPlacement: 'center',
  },
  {
    id: 'library',
    titleKey: 'tour.library.title',
    bodyKey: 'tour.library.body',
    placement: 'bottom',
    target: '[data-tour="library-tabs"]',
    fallbackTarget: '[data-tour="tab-library"]',
    fallbackPlacement: 'top',
    navigateTo: '/library',
  },
  { id: 'bot', titleKey: 'tour.bot.title', bodyKey: 'tour.bot.body', placement: 'center' },
  {
    id: 'more',
    titleKey: 'tour.more.title',
    bodyKey: 'tour.more.body',
    placement: 'top',
    target: '[data-tour="tab-settings"]',
  },
  { id: 'done', titleKey: 'tour.done.title', bodyKey: 'tour.done.body', placement: 'center' },
];

/** The first visible match: the web app has the same tab in a sidebar and a bottom bar. */
export function getTargetRect(selector: string): DOMRect | null {
  try {
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 || r.height > 0) return r;
    }
    return null;
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
