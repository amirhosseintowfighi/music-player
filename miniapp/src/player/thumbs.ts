import { useQuery } from '@tanstack/react-query';

import { post } from '@/api/client';

/**
 * Artwork URLs for a list of tracks.
 *
 * Thumbnails come from the same signed-URL mechanism as audio, but through a batch
 * endpoint that does not count against the daily play limit and hands out longer-lived
 * links so lists can be scrolled and re-rendered cheaply.
 */
export function useThumbs(trackIds: number[]): Record<number, string> {
  const ids = [...new Set(trackIds)].sort((a, b) => a - b).slice(0, 100);
  const query = useQuery({
    queryKey: ['thumbs', ids],
    queryFn: () => post<{ items: Record<string, string> }>('/v1/tracks/thumbs', { ids }),
    enabled: ids.length > 0,
    staleTime: 25 * 60_000,
    gcTime: 30 * 60_000,
  });
  const items = query.data?.items;
  if (!items) return {};
  const out: Record<number, string> = {};
  for (const [id, url] of Object.entries(items)) out[Number(id)] = url;
  return out;
}
