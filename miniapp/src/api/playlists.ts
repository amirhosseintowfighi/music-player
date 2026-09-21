/** Phase 5 data hooks: playlists, likes, history and the cross-device resume point. */
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query';

import {
  del,
  get,
  patch,
  post,
  put,
  qs,
  type LikeState,
  type Page,
  type PlaybackState,
  type Playlist,
  type PlaylistDetail,
  type Track,
} from './client';

const PAGE = 30;

export const playlistKeys = {
  all: ['playlists'] as const,
  detail: (id: number) => ['playlist', id] as const,
  shared: (slug: string) => ['playlist', 'shared', slug] as const,
  likes: ['library', 'likes'] as const,
  recent: ['library', 'recent'] as const,
  playback: ['me', 'playback'] as const,
};

export function usePlaylists() {
  return useQuery({ queryKey: playlistKeys.all, queryFn: () => get<Playlist[]>('/v1/playlists') });
}

export function usePlaylist(id: number) {
  return useQuery({
    queryKey: playlistKeys.detail(id),
    queryFn: () => get<PlaylistDetail>(`/v1/playlists/${id}`),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useSharedPlaylist(slug: string) {
  return useQuery({
    queryKey: playlistKeys.shared(slug),
    queryFn: () => get<PlaylistDetail>(`/v1/playlists/shared/${slug}`),
    enabled: slug.length > 0,
  });
}

export function useCreatePlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; track_ids?: number[] }) =>
      post<PlaylistDetail>('/v1/playlists', body),
    onSuccess: () => client.invalidateQueries({ queryKey: playlistKeys.all }),
  });
}

export function useUpdatePlaylist(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; is_public?: boolean; is_collaborative?: boolean }) =>
      patch<Playlist>(`/v1/playlists/${id}`, body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: playlistKeys.all });
      void client.invalidateQueries({ queryKey: playlistKeys.detail(id) });
    },
  });
}

export function useDeletePlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => del<void>(`/v1/playlists/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: playlistKeys.all }),
  });
}

export function useAddToPlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ playlistId, trackIds }: { playlistId: number; trackIds: number[] }) =>
      post<Playlist>(`/v1/playlists/${playlistId}/tracks`, { track_ids: trackIds }),
    onSuccess: (_result, variables) => {
      void client.invalidateQueries({ queryKey: playlistKeys.all });
      void client.invalidateQueries({ queryKey: playlistKeys.detail(variables.playlistId) });
    },
  });
}

export function useRemoveFromPlaylist(playlistId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (trackId: number) => del<void>(`/v1/playlists/${playlistId}/tracks/${trackId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: playlistKeys.detail(playlistId) }),
  });
}

/** Optimistic reorder: the list moves at once and the server confirms afterwards. */
export function useMoveInPlaylist(playlistId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { track_id: number; after_track_id: number | null }) =>
      post<void>(`/v1/playlists/${playlistId}/move`, body),
    onMutate: async (body) => {
      const key = playlistKeys.detail(playlistId);
      await client.cancelQueries({ queryKey: key });
      const previous = client.getQueryData<PlaylistDetail>(key);
      if (previous) {
        const items = previous.items.filter((track) => track.id !== body.track_id);
        const moved = previous.items.find((track) => track.id === body.track_id);
        const at =
          body.after_track_id === null
            ? 0
            : items.findIndex((track) => track.id === body.after_track_id) + 1;
        if (moved) items.splice(at, 0, moved);
        client.setQueryData(key, { ...previous, items });
      }
      return { previous };
    },
    onError: (_error, _body, context) => {
      if (context?.previous) client.setQueryData(playlistKeys.detail(playlistId), context.previous);
    },
    onSettled: () => client.invalidateQueries({ queryKey: playlistKeys.detail(playlistId) }),
  });
}

export function useJoinPlaylist() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => post<Playlist>(`/v1/playlists/shared/${slug}/join`),
    onSuccess: () => client.invalidateQueries({ queryKey: playlistKeys.all }),
  });
}

export function useLikedTracks() {
  return useInfiniteQuery({
    queryKey: playlistKeys.likes,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      get<Page<Track>>(`/v1/library/likes${qs({ cursor: pageParam, limit: PAGE })}`),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useRecentTracks() {
  return useQuery({
    queryKey: playlistKeys.recent,
    queryFn: () => get<Page<Track>>('/v1/library/recent?limit=20'),
  });
}

function patchTrackEverywhere(client: QueryClient, trackId: number, liked: boolean): void {
  client.setQueriesData<unknown>({}, (data: unknown) => {
    if (!data || typeof data !== 'object') return data;
    const patchOne = (track: Track): Track => (track.id === trackId ? { ...track, liked } : track);
    const page = data as Partial<Page<Track>>;
    if (Array.isArray(page.items)) return { ...page, items: page.items.map(patchOne) };
    const infinite = data as Partial<InfiniteData<Page<Track>>>;
    if (Array.isArray(infinite.pages)) {
      return {
        ...infinite,
        pages: infinite.pages.map((one) => ({ ...one, items: one.items.map(patchOne) })),
      };
    }
    return data;
  });
}

/** Like toggle; every cached copy of the track flips immediately. */
export function useToggleLike() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ trackId, liked }: { trackId: number; liked: boolean }) =>
      liked
        ? del<LikeState>(`/v1/tracks/${trackId}/like`)
        : put<LikeState>(`/v1/tracks/${trackId}/like`),
    onMutate: ({ trackId, liked }) => patchTrackEverywhere(client, trackId, !liked),
    onError: (_error, { trackId, liked }) => patchTrackEverywhere(client, trackId, liked),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: playlistKeys.likes });
    },
  });
}

export function useRecordPlay() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      track_id: number;
      duration_played: number;
      completed: boolean;
      source: string;
      source_id: number | null;
    }) => post<void>('/v1/history', body),
    onSuccess: () => client.invalidateQueries({ queryKey: playlistKeys.recent }),
  });
}

export function useSavePlayback() {
  return useMutation({
    mutationFn: (body: Omit<PlaybackState, 'items' | 'updated_at'>) =>
      put<void>('/v1/me/playback', body),
  });
}

export function useStoredPlayback() {
  return useQuery({
    queryKey: playlistKeys.playback,
    queryFn: () => get<PlaybackState>('/v1/me/playback'),
    staleTime: 5 * 60_000,
  });
}

export function useSendToChat() {
  return useMutation({ mutationFn: (trackId: number) => post<void>(`/v1/tracks/${trackId}/send`) });
}
