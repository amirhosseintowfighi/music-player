/** Profiles, following, the friends feed, Wrapped and notification settings. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { del, get, put, qs, type Track } from './client';
import { keys } from './hooks';
import type { components } from './schema';

export type Profile = components['schemas']['ProfileOut'];
export type Connection = components['schemas']['ConnectionOut'];
export type FriendActivity = components['schemas']['FriendActivityOut'];
export type Wrapped = components['schemas']['WrappedOut'];
export type NotificationPrefs = components['schemas']['NotificationPrefsOut'];

export const socialKeys = {
  profile: (id: number | 'me') => ['profile', id] as const,
  feed: ['social', 'feed'] as const,
  connections: (id: number, direction: string) => ['connections', id, direction] as const,
  wrapped: (year: number | undefined) => ['wrapped', year ?? 'current'] as const,
  notifications: ['me', 'notifications'] as const,
};

export function useProfile(userId: number | 'me') {
  return useQuery({
    queryKey: socialKeys.profile(userId),
    queryFn: () =>
      get<Profile>(userId === 'me' ? '/v1/me/profile' : `/v1/users/${userId}/profile`),
  });
}

export function useConnections(userId: number, direction: 'followers' | 'following') {
  return useQuery({
    queryKey: socialKeys.connections(userId, direction),
    queryFn: () => get<Connection[]>(`/v1/users/${userId}/${direction}`),
  });
}

export function useFriendsFeed() {
  return useQuery({
    queryKey: socialKeys.feed,
    queryFn: () => get<FriendActivity[]>('/v1/social/feed'),
    staleTime: 60_000,
  });
}

/** Optimistic: a follow button that waits for the network feels broken. */
export function useToggleFollow(userId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (following: boolean) =>
      following
        ? del<{ following: boolean }>(`/v1/users/${userId}/follow`)
        : put<{ following: boolean }>(`/v1/users/${userId}/follow`, {}),
    onMutate: async (following) => {
      await client.cancelQueries({ queryKey: socialKeys.profile(userId) });
      const previous = client.getQueryData<Profile>(socialKeys.profile(userId));
      if (previous) {
        client.setQueryData<Profile>(socialKeys.profile(userId), {
          ...previous,
          is_following: !following,
          followers: Math.max(0, previous.followers + (following ? -1 : 1)),
        });
      }
      return { previous };
    },
    onError: (_error, _following, context) => {
      if (context?.previous) client.setQueryData(socialKeys.profile(userId), context.previous);
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: socialKeys.profile(userId) });
      void client.invalidateQueries({ queryKey: socialKeys.feed });
    },
  });
}

export function useWrapped(year?: number) {
  return useQuery({
    queryKey: socialKeys.wrapped(year),
    queryFn: () => get<Wrapped>(`/v1/me/wrapped${qs({ year })}`),
  });
}

export function useNotificationPrefs() {
  return useQuery({
    queryKey: socialKeys.notifications,
    queryFn: () => get<NotificationPrefs>('/v1/me/notifications'),
  });
}

export function useSetNotificationPrefs() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (prefs: Record<string, boolean>) =>
      put<NotificationPrefs>('/v1/me/notifications', { prefs }),
    onSuccess: (data) => client.setQueryData(socialKeys.notifications, data),
  });
}

export function useSetPublicProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (isPublic: boolean) => put<void>('/v1/me/public-profile', { public: isPublic }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.me });
      void client.invalidateQueries({ queryKey: socialKeys.profile('me') });
    },
  });
}

export function feedTracks(feed: FriendActivity[] | undefined): Track[] {
  return (feed ?? []).map((entry) => entry.track);
}
