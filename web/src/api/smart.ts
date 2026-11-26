/** Smart playlists (0018): rules in, songs out, rebuilt on their own. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { components } from './schema';
import { post, put, type PlaylistDetail, type Track } from './client';
import { playlistKeys } from './playlists';

export type SmartRules = components['schemas']['SmartRules'];
export type SmartPreview = { total: number; items: Track[] };

export const DEFAULT_RULES: SmartRules = {
  source: 'library',
  artist_ids: [],
  channel_ids: [],
  language: null,
  min_duration: null,
  max_duration: null,
  year_from: null,
  year_to: null,
  not_played_days: null,
  min_my_plays: null,
  sort: 'recent',
  limit: 50,
};

/** What the rules would pick right now; refetches as the rules change. */
export function useSmartPreview(rules: SmartRules, enabled = true) {
  return useQuery({
    queryKey: ['smart', 'preview', rules],
    queryFn: () => post<SmartPreview>('/v1/playlists/smart/preview', { rules }),
    enabled,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  });
}

export function useCreateSmartPlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; rules: SmartRules }) =>
      post<PlaylistDetail>('/v1/playlists/smart', body),
    onSuccess: () => client.invalidateQueries({ queryKey: playlistKeys.all }),
  });
}

export function useUpdateSmartRules(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (rules: SmartRules) => put<PlaylistDetail>(`/v1/playlists/${id}/rules`, { rules }),
    onSuccess: (detail) => {
      client.setQueryData(playlistKeys.detail(id), detail);
      void client.invalidateQueries({ queryKey: playlistKeys.all });
    },
  });
}

export function useRefreshSmartPlaylist(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => post<PlaylistDetail>(`/v1/playlists/${id}/refresh`),
    onSuccess: (detail) => {
      client.setQueryData(playlistKeys.detail(id), detail);
      void client.invalidateQueries({ queryKey: playlistKeys.all });
    },
  });
}
