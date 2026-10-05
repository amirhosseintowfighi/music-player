/** Offline downloads: IndexedDB for metadata + Cache Storage for audio blobs. */

export interface DownloadItem {
  trackId: number;
  title: string;
  status: 'queued' | 'downloading' | 'done' | 'failed';
  progress: number;
  size?: number;
  mime?: string;
  error?: string;
  blobUrl?: string | null;
}

const CACHE = 'tmusic-web-offline-v1';

export async function offlineUrl(trackId: number): Promise<string | null> {
  try {
    const cache = await caches.open(CACHE);
    const res = await cache.match(`/offline/${trackId}`);
    if (!res) return null;
    return URL.createObjectURL(await res.blob());
  } catch {
    return null;
  }
}

export async function removeOffline(trackId: number): Promise<void> {
  try {
    const c = await caches.open(CACHE);
    await c.delete(`/offline/${trackId}`);
  } catch {
    /* ignore */
  }
}

export async function saveOfflineFromTicket(trackId: number, ticket: { url: string; mime: string; size: number }): Promise<string> {
  const res = await fetch(ticket.url);
  if (!res.ok || !res.body) throw new Error('unavailable');
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) chunks.push(value);
  }
  const blob = new Blob(chunks as BlobPart[], { type: ticket.mime });
  const c = await caches.open(CACHE);
  await c.put(`/offline/${trackId}`, new Response(blob, { headers: { 'Content-Type': ticket.mime } }));
  return URL.createObjectURL(blob);
}

export async function listOfflineIds(): Promise<number[]> {
  try {
    const c = await caches.open(CACHE);
    return (await c.keys()).map((r) => Number(new URL(r.url).pathname.split('/').pop())).filter(Number.isFinite);
  } catch {
    return [];
  }
}
