/** Streaks and badges (0021). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { components } from './schema';
import { get, post } from './client';

export type Streak = components['schemas']['StreakOut'];
export type Achievements = components['schemas']['AchievementsOut'];

export function useStreak() {
  return useQuery({
    queryKey: ['me', 'streak'],
    queryFn: () => get<Streak>('/v1/me/streak'),
    staleTime: 5 * 60_000,
  });
}

export function useAchievements() {
  return useQuery({ queryKey: ['me', 'achievements'], queryFn: () => get<Achievements>('/v1/me/achievements') });
}

export function useMarkBadgesSeen() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => post<void>('/v1/me/achievements/seen'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['me', 'achievements'] }),
  });
}
