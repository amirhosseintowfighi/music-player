import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { ExtrasRow } from '@/components/ExtrasRow';
import { AchievementsScreen } from '@/screens/Achievements';

import { authRoutes, mockApi, renderApp } from './helpers';

const streak = { current: 4, longest: 9, today_done: false, week: [false, false, false, true, true, true, true] };

beforeEach(() => __setAuthForTests('token'));

describe('streaks and badges', () => {
  it('shows the streak, earned and unearned badges, and marks new ones seen', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'POST /v1/me/achievements/seen': null,
      'GET /v1/me/achievements': {
        streak,
        earned: 1,
        total: 2,
        badges: [
          { code: 'first_play', goal: 1, progress: 1, earned: true, earned_at: '2026-11-20T10:00:00Z', new: true },
          { code: 'streak_7', goal: 7, progress: 4, earned: false, earned_at: null, new: false },
        ],
      },
    });
    renderApp(<AchievementsScreen />);
    expect(await screen.findByText('اولین آهنگ')).toBeInTheDocument();
    expect(screen.getByText('تازه!')).toBeInTheDocument();
    expect(screen.getByText(/امروز هنوز گوش نداده‌ای/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '4');
    await waitFor(() => expect(calls.some((c) => c.url === '/v1/me/achievements/seen')).toBe(true), {
      timeout: 4000,
    });
  });

  it('puts the streak first on Home', async () => {
    mockApi({ ...authRoutes(), 'GET /v1/me/streak': streak });
    renderApp(<ExtrasRow />);
    expect(await screen.findByText(/🔥 (4|۴) روز/)).toBeInTheDocument();
  });
});
