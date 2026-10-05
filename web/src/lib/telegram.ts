/**
 * Telegram bridge — same surface as miniapp, but in the browser outside
 * Telegram it degrades to no-ops / window.open (no initData).
 */
export interface TelegramTheme {
  colorScheme: 'light' | 'dark';
  accent?: string;
}
interface WebApp {
  initData?: string;
  platform?: string;
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
export type InvoiceStatus = 'paid' | 'cancelled' | 'failed' | 'pending';
function webApp(): WebApp | undefined {
  return (globalThis as { Telegram?: { WebApp?: WebApp } }).Telegram?.WebApp;
}
export function isInsideTelegram(): boolean {
  return Boolean(webApp()?.initData);
}
export const BOT_USERNAME = (import.meta.env.VITE_BOT_USERNAME as string | undefined) ?? 'tmusic_bot';
export function telegramPlatform(): string {
  return webApp()?.platform ?? '';
}
export function getInitData(): string {
  const fromWebApp = webApp()?.initData;
  if (fromWebApp) return fromWebApp;
  const dev = (import.meta.env as Record<string, unknown>).VITE_DEV_INIT_DATA;
  return typeof dev === 'string' ? dev : '';
}
export function initTelegram(): TelegramTheme {
  const app = webApp();
  try {
    app?.ready?.();
    app?.expand?.();
    app?.disableVerticalSwipes?.();
  } catch {}
  return { colorScheme: app?.colorScheme === 'light' ? 'light' : 'dark', accent: app?.themeParams?.button_color };
}
export function haptic(kind: 'light' | 'medium' | 'select' | 'success' | 'error' = 'light'): void {
  const h = webApp()?.HapticFeedback;
  if (!h) return;
  try {
    if (kind === 'select') h.selectionChanged?.();
    else if (kind === 'success' || kind === 'error') h.notificationOccurred?.(kind);
    else h.impactOccurred?.(kind);
  } catch {}
}
export function backButton(onClick: (() => void) | null): void {
  const b = webApp()?.BackButton;
  if (!b) return;
  try {
    if (onClick) {
      b.onClick(onClick);
      b.show();
    } else b.hide();
  } catch {}
}
export function openTelegramLink(url: string): void {
  const app = webApp();
  if (app?.openTelegramLink) app.openTelegramLink(url);
  else globalThis.open?.(url, '_blank');
}
export function openExternalLink(url: string): void {
  const app = webApp();
  if (app?.openLink) app.openLink(url);
  else globalThis.open?.(url, '_blank');
}
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
