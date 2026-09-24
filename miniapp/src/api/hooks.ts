import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';

import {
  del,
  get,
  patch,
  post,
  qs,
  type Album,
  type Artist,
  type ArtistPage,
  type Category,
  type Channel,
  type Me,
  type Page,
  type SearchResult,
  type Suggestions,
  type Track,
  type UserChannel,
} from './client';

export interface TrackFilters {
  channel_id?: number | null;
  artist_id?: number | null;
  album?: string | null;
  language?: string | null;
  year?: number | null;
  min_duration?: number | null;
  max_duration?: number | null;
}

const PAGE = 30;

/** Flattens an infinite query into a plain list for rendering. */
export function flatten<T>(data: InfiniteData<Page<T>> | undefined): T[] {
  return data?.pages.flatMap((page) => page.items) ?? [];
}

export const keys = {
  me: ['me'] as const,
  channels: ['library', 'channels'] as const,
  featured: (categoryId: number | null) => ['channels', 'featured', categoryId] as const,
  categories: ['channels', 'categories'] as const,
  channel: (id: number) => ['channel', id] as const,
  channelTracks: (id: number) => ['channel', id, 'tracks'] as const,
  libraryTracks: (filters: TrackFilters) => ['library', 'tracks', filters] as const,
  libraryArtists: ['library', 'artists'] as const,
  libraryAlbums: ['library', 'albums'] as const,
  track: (id: number) => ['track', id] as const,
  artist: (id: number) => ['artist', id] as const,
  artistTracks: (id: number) => ['artist', id, 'tracks'] as const,
  search: (q: string, scope: string, filters: TrackFilters) => ['search', q, scope, filters] as const,
  suggest: (q: string) => ['search', 'suggest', q] as const,
};

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => get<Me>('/v1/me'), staleTime: 60_000 });
}

export function useMyChannels() {
  return useQuery({
    queryKey: keys.channels,
    queryFn: () => get<UserChannel[]>('/v1/library/channels'),
    // Channels being indexed report progress; poll while any is still working.
    refetchInterval: (query) =>
      query.state.data?.some((c) => c.status === 'pending' || c.status === 'indexing') ? 5_000 : false,
  });
}

export function useAddChannel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (ref: string) => post<{ channel: Channel; created: boolean }>('/v1/library/channels', { ref }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.channels });
      void client.invalidateQueries({ queryKey: ['library'] });
    },
  });
}

export function useRemoveChannel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => del<void>(`/v1/library/channels/${id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.channels });
      void client.invalidateQueries({ queryKey: ['library'] });
    },
  });
}

export function useCategories() {
  return useQuery({
    queryKey: keys.categories,
    queryFn: () => get<Category[]>('/v1/channels/categories'),
    staleTime: 60 * 60_000,
  });
}

export function useFeaturedChannels(categoryId: number | null) {
  return useInfiniteQuery({
    queryKey: keys.featured(categoryId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      get<Page<Channel>>(`/v1/channels/featured${qs({ category_id: categoryId, cursor: pageParam, limit: 20 })}`),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useChannel(id: number) {
  return useQuery({ queryKey: keys.channel(id), queryFn: () => get<Channel>(`/v1/channels/${id}`) });
}

export function useChannelTracks(id: number) {
  return useInfiniteQuery({
    queryKey: keys.channelTracks(id),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      get<Page<Track>>(`/v1/channels/${id}/tracks${qs({ cursor: pageParam, limit: PAGE })}`),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useLibraryTracks(filters: TrackFilters = {}) {
  return useInfiniteQuery({
    queryKey: keys.libraryTracks(filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      get<Page<Track>>(`/v1/library/tracks${qs({ ...filters, cursor: pageParam, limit: PAGE })}`),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useLibraryArtists() {
  return useInfiniteQuery({
    queryKey: keys.libraryArtists,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => get<Page<Artist>>(`/v1/library/artists${qs({ cursor: pageParam, limit: 50 })}`),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useLibraryAlbums() {
  return useQuery({ queryKey: keys.libraryAlbums, queryFn: () => get<Album[]>('/v1/library/albums?limit=50') });
}

export function useTrack(id: number) {
  return useQuery({
    queryKey: keys.track(id),
    queryFn: () => get<Track>(`/v1/tracks/${id}`),
    enabled: Number.isFinite(id) && id > 0,
  });
}

export function useReportTrack(id: number) {
  return useMutation({
    mutationFn: (body: { reason: string; details?: string }) =>
      post<{ id: number; status: string }>(`/v1/tracks/${id}/report`, body),
  });
}

export function useArtist(id: number) {
  return useQuery({ queryKey: keys.artist(id), queryFn: () => get<Artist>(`/v1/artists/${id}`) });
}

export function useArtistPage(id: number) {
  return useQuery({
    queryKey: [...keys.artist(id), 'page'],
    queryFn: () => get<ArtistPage>(`/v1/artists/${id}/page`),
  });
}

export function useArtistTracks(id: number) {
  return useInfiniteQuery({
    queryKey: keys.artistTracks(id),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      get<Page<Track>>(`/v1/artists/${id}/tracks${qs({ cursor: pageParam, limit: PAGE })}`),
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useSearch(query: string, scope: 'library' | 'global', filters: TrackFilters = {}) {
  return useQuery({
    queryKey: keys.search(query, scope, filters),
    queryFn: () => get<SearchResult>(`/v1/search${qs({ q: query, scope, ...filters, limit: 30 })}`),
    enabled: query.trim().length > 0,
    placeholderData: (previous) => previous,
  });
}

export function useSuggestions(query: string) {
  return useQuery({
    queryKey: keys.suggest(query),
    queryFn: () => get<Suggestions>(`/v1/search/suggest${qs({ q: query })}`),
    staleTime: 30_000,
  });
}

export function useClearSearchHistory() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => del<void>('/v1/search/history'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['search', 'suggest'] }),
  });
}

export function useSetLanguage() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (lang: 'fa' | 'en') => patch<Me>('/v1/me/lang', { lang }),
    onSuccess: (me) => client.setQueryData(keys.me, me),
  });
}
