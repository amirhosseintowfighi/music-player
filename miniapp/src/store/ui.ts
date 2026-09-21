import { create } from 'zustand';

import type { Track } from '@/api/client';
import type { Lang } from '@/i18n';
import { applyPerf, deviceLooksSlow, readPerfPreference, savePerfPreference, type PerfMode } from '@/lib/perf';

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'error' | 'success';
}

export interface Upsell {
  kind: string;
  limit: number;
}

interface UiState {
  lang: Lang;
  theme: 'dark' | 'light';
  perf: PerfMode;
  lowPerf: boolean;
  toasts: Toast[];
  upsell: Upsell | null;
  playerOpen: boolean;
  actionTrack: Track | null;

  setLang: (lang: Lang) => void;
  setTheme: (theme: 'dark' | 'light') => void;
  setPerf: (mode: PerfMode) => void;
  markSlowDevice: () => void;
  toast: (text: string, kind?: Toast['kind']) => void;
  dismissToast: (id: number) => void;
  showUpsell: (upsell: Upsell | null) => void;
  setPlayerOpen: (open: boolean) => void;
  setActionTrack: (track: Track | null) => void;
}

let toastId = 0;

function resolveLow(mode: PerfMode): boolean {
  return mode === 'low' || (mode === 'auto' && deviceLooksSlow());
}

export const useUi = create<UiState>((set, get) => {
  const perf = readPerfPreference();
  return {
    lang: 'fa',
    theme: 'dark',
    perf,
    lowPerf: resolveLow(perf),
    toasts: [],
    upsell: null,
    playerOpen: false,
    actionTrack: null,

    setLang(lang) {
      document.documentElement.lang = lang;
      document.documentElement.dir = lang === 'fa' ? 'rtl' : 'ltr';
      set({ lang });
    },
    setTheme(theme) {
      document.documentElement.dataset.theme = theme;
      set({ theme });
    },
    setPerf(mode) {
      savePerfPreference(mode);
      const low = resolveLow(mode);
      applyPerf(low);
      set({ perf: mode, lowPerf: low });
    },
    markSlowDevice() {
      if (get().perf !== 'auto' || get().lowPerf) return;
      applyPerf(true);
      set({ lowPerf: true });
    },
    toast(text, kind = 'info') {
      const id = ++toastId;
      set({ toasts: [...get().toasts, { id, text, kind }] });
      setTimeout(() => get().dismissToast(id), 3200);
    },
    dismissToast(id) {
      set({ toasts: get().toasts.filter((t) => t.id !== id) });
    },
    showUpsell(upsell) {
      set({ upsell });
    },
    setPlayerOpen(open) {
      set({ playerOpen: open });
    },
    setActionTrack(track) {
      set({ actionTrack: track });
    },
  };
});
