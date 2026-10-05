/** Discover feed, trending, similar tracks and radio. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { get, post, qs, type Page, type Track } from './client';
import type { components } from './schema';

export type Section = components['schemas']['SectionOut'];
export type Discover = components['schemas']['DiscoverOut'];
export type TrendWindow = '24h' | '7d' | '30d';
export type TrendKind = 'plays' | 'most_added' | 'rising';

export const discoverKeys = {
  feed: ['discover'] as const,
  trending: (window: TrendWindow, kind: TrendKind) => ['trending', window, kind] as const,
  similar: (id: number) => ['track', id, 'similar'] as const,
  radio: (id: number) => ['track', id, 'radio'] as const,
};

export function useDiscover() {
  return useQuery({
    queryKey: discoverKeys.feed,
    queryFn: () => get<Discover>('/v1/discover'),
    staleTime: 5 * 60_000,
  });
}

export function useTrending(window: TrendWindow, kind: TrendKind) {
  return useQuery({
    queryKey: discoverKeys.trending(window, kind),
    queryFn: () => get<Page<Track>>(`/v1/trending${qs({ window, kind, limit: 40 })}`),
    staleTime: 5 * 60_000,
  });
}

export function useSimilar(trackId: number | null) {
  return useQuery({
    queryKey: discoverKeys.similar(trackId ?? 0),
    queryFn: () => get<Page<Track>>(`/v1/tracks/${trackId}/similar`),
    enabled: trackId !== null,
  });
}

/** Loaded on demand: starting a station is an explicit action, never a background fetch. */
export async function fetchRadio(trackId: number): Promise<Track[]> {
  const page = await get<Page<Track>>(`/v1/tracks/${trackId}/radio${qs({ limit: 50 })}`);
  return page.items;
}

export function useRefreshDiscover() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => post<Discover>('/v1/discover/refresh', {}),
    onSuccess: (data) => client.setQueryData(discoverKeys.feed, data),
  });
}
