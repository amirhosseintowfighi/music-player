/** Comments and reactions on a song (0022). */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { components } from './schema';
import { del, get, post, put, qs } from './client';

export type Comment = components['schemas']['CommentOut'];
export type CommentPage = components['schemas']['CommentPageOut'];
export type Reactions = components['schemas']['ReactionsOut'];
export type Reaction = Reactions['mine'][number];

export const REACTIONS: { key: Reaction; glyph: string }[] = [
  { key: 'fire', glyph: '🔥' },
  { key: 'heart', glyph: '❤️' },
  { key: 'love', glyph: '😍' },
  { key: 'cry', glyph: '😢' },
  { key: 'party', glyph: '🎉' },
  { key: 'clap', glyph: '👏' },
];

const keys = {
  comments: (id: number) => ['track', id, 'comments'] as const,
  reactions: (id: number) => ['track', id, 'reactions'] as const,
};

export function useComments(trackId: number, enabled = true) {
  return useInfiniteQuery({
    queryKey: keys.comments(trackId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => get<CommentPage>(`/v1/tracks/${trackId}/comments${qs({ cursor: pageParam })}`),
    getNextPageParam: (last) => last.next_cursor,
    enabled: enabled && trackId > 0,
  });
}

export function useCommentActions(trackId: number) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: keys.comments(trackId) });
  return {
    add: useMutation({
      mutationFn: (body: { body: string; at_second?: number | null }) =>
        post<Comment>(`/v1/tracks/${trackId}/comments`, body),
      onSuccess: refresh,
    }),
    remove: useMutation({ mutationFn: (id: number) => del<void>(`/v1/comments/${id}`), onSuccess: refresh }),
  };
}

export function useReactions(trackId: number, enabled = true) {
  return useQuery({
    queryKey: keys.reactions(trackId),
    queryFn: () => get<Reactions>(`/v1/tracks/${trackId}/reactions`),
    enabled: enabled && trackId > 0,
  });
}

export function useToggleReaction(trackId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ key, on }: { key: Reaction; on: boolean }) =>
      on ? put<Reactions>(`/v1/tracks/${trackId}/reactions/${key}`) : del<Reactions>(`/v1/tracks/${trackId}/reactions/${key}`),
    onSuccess: (data) => client.setQueryData(keys.reactions(trackId), data),
  });
}
