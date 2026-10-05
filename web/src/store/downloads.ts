import { create } from 'zustand';

export interface DownloadState {
  items: Record<number, { status: 'queued' | 'downloading' | 'done' | 'failed'; progress: number; error?: string }>;
  start: (trackId: number) => void;
  setProgress: (trackId: number, progress: number) => void;
  setDone: (trackId: number) => void;
  setFailed: (trackId: number, error: string) => void;
  remove: (trackId: number) => void;
}

export const useDownloads = create<DownloadState>((set) => ({
  items: {},
  start(trackId) {
    set((s) => ({ items: { ...s.items, [trackId]: { status: 'downloading', progress: 0 } } }));
  },
  setProgress(trackId, progress) {
    set((s) => ({ items: { ...s.items, [trackId]: { ...(s.items[trackId] ?? { status: 'downloading', progress: 0 }), progress } } }));
  },
  setDone(trackId) {
    set((s) => ({ items: { ...s.items, [trackId]: { status: 'done', progress: 1 } } }));
  },
  setFailed(trackId, error) {
    set((s) => ({ items: { ...s.items, [trackId]: { status: 'failed', progress: 0, error } } }));
  },
  remove(trackId) {
    set((s) => {
      const copy = { ...s.items };
      delete copy[trackId];
      return { items: copy };
    });
  },
}));
