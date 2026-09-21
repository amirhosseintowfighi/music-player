/**
 * Telegram Mini App bridge.
 *
 * `@telegram-apps/sdk` is tried first; if anything about it fails (older client,
 * running in a plain browser during development, a breaking SDK change) we fall back
 * to `window.Telegram.WebApp`, and finally to VITE_DEV_INIT_DATA so the app can be
 * developed outside Telegram.
 */

export interface TelegramTheme {
  colorScheme: 'light' | 'dark';
  accent?: string;
}

interface WebApp {
  initData?: string;
  colorScheme?: 'light' | 'dark';
  themeParams?: Record<string, string>;
  ready?: () => void;
  expand?: () => void;
  disableVerticalSwipes?: () => void;
  requestFullscreen?: () => void;
  HapticFeedback?: {
    impactOccurred?: (style: string) => void;
    notificationOccurred?: (type: string) => void;
    selectionChanged?: () => void;
  };
  BackButton?: { show: () => void; hide: () => void; onClick: (cb: () => void) => void };
  openTelegramLink?: (url: string) => void;
  openLink?: (url: string) => void;
  openInvoice?: (url: string, callback?: (status: InvoiceStatus) => void) => void;
  close?: () => void;
}

/** Result of a Stars invoice opened with ``openInvoice``. */
export type InvoiceStatus = 'paid' | 'cancelled' | 'failed' | 'pending';

function webApp(): WebApp | undefined {
  return (globalThis as { Telegram?: { WebApp?: WebApp } }).Telegram?.WebApp;
}

export function isInsideTelegram(): boolean {
  return Boolean(webApp()?.initData);
}

/** Raw initData string for POST /v1/auth/telegram. Empty when it cannot be found. */
export function getInitData(): string {
  const fromWebApp = webApp()?.initData;
  if (fromWebApp) return fromWebApp;
  const dev = import.meta.env.VITE_DEV_INIT_DATA;
  return typeof dev === 'string' ? dev : '';
}

/** Tell the client we are ready, expand to full height and keep swipes from closing us. */
export function initTelegram(): TelegramTheme {
  const app = webApp();
  try {
    app?.ready?.();
    app?.expand?.();
    app?.disableVerticalSwipes?.();
  } catch {
    /* older clients simply lack these methods */
  }
  return {
    colorScheme: app?.colorScheme === 'light' ? 'light' : 'dark',
    accent: app?.themeParams?.button_color,
  };
}

export function haptic(kind: 'light' | 'medium' | 'select' | 'success' | 'error' = 'light'): void {
  const h = webApp()?.HapticFeedback;
  if (!h) return;
  try {
    if (kind === 'select') h.selectionChanged?.();
    else if (kind === 'success' || kind === 'error') h.notificationOccurred?.(kind);
    else h.impactOccurred?.(kind);
  } catch {
    /* haptics are a nicety, never a failure */
  }
}

/** System back button, used by the full-screen player and detail screens. */
export function backButton(onClick: (() => void) | null): void {
  const b = webApp()?.BackButton;
  if (!b) return;
  try {
    if (onClick) {
      b.onClick(onClick);
      b.show();
    } else {
      b.hide();
    }
  } catch {
    /* ignore */
  }
}

export function openTelegramLink(url: string): void {
  const app = webApp();
  if (app?.openTelegramLink) app.openTelegramLink(url);
  else globalThis.open?.(url, '_blank');
}

/** Opens an external page (a payment gateway) in the in-app browser. */
export function openExternalLink(url: string): void {
  const app = webApp();
  if (app?.openLink) app.openLink(url);
  else globalThis.open?.(url, '_blank');
}

/**
 * Opens a Telegram Stars invoice. Resolves with the final status; outside Telegram
 * (or on an old client without openInvoice) it resolves to 'failed' so the caller
 * can fall back to another provider instead of hanging.
 */
export function openInvoice(url: string): Promise<InvoiceStatus> {
  const app = webApp();
  if (!app?.openInvoice) {
    openTelegramLink(url);
    return Promise.resolve<InvoiceStatus>('pending');
  }
  return new Promise((resolve) => {
    try {
      app.openInvoice?.(url, (status) => resolve(status));
    } catch {
      resolve('failed');
    }
  });
}
