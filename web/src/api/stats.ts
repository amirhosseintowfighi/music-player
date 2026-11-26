/** Listening stats for the last week or month. */
import { useQuery } from '@tanstack/react-query';

import type { components } from './schema';
import { get } from './client';

export type ListeningStats = components['schemas']['ListeningStatsOut'];

export function useListeningStats(days: 7 | 30) {
  return useQuery({
    queryKey: ['me', 'stats', days],
    queryFn: () => get<ListeningStats>(`/v1/me/stats?days=${days}`),
    staleTime: 60_000,
  });
}
