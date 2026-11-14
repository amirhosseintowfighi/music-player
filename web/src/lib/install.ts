/**
 * "Install app": the browser's own install prompt, kept for when the listener asks.
 *
 * Chrome and Edge (Windows, Android, ChromeOS, macOS) fire `beforeinstallprompt` once,
 * early, when the site qualifies as an app. We hold on to it and show our own button;
 * after installing, the app opens in its own window with its own icon.
 */
import { create } from 'zustand';

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

interface InstallStore {
  prompt: InstallPromptEvent | null;
  installed: boolean;
  install: () => Promise<boolean>;
}

function runningInstalled(): boolean {
  try {
    return window.matchMedia('(display-mode: standalone)').matches || window.matchMedia('(display-mode: window-controls-overlay)').matches;
  } catch {
    return false;
  }
}

export const useInstall = create<InstallStore>((set, get) => ({
  prompt: null,
  installed: typeof window !== 'undefined' && runningInstalled(),
  async install() {
    const event = get().prompt;
    if (!event) return false;
    await event.prompt();
    const { outcome } = await event.userChoice;
    // A prompt can only be used once, whatever the answer.
    set({ prompt: null, installed: outcome === 'accepted' });
    return outcome === 'accepted';
  },
}));

export function listenForInstall(): void {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    useInstall.setState({ prompt: event as InstallPromptEvent });
  });
  window.addEventListener('appinstalled', () => useInstall.setState({ prompt: null, installed: true }));
}
