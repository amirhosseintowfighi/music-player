import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { StatsScreen } from '@/screens/Stats';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const week = {
  days: 7,
  start: '2026-11-19',
  minutes: 120,
  plays: 40,
  songs: 25,
  artists: 9,
  previous_minutes: 100,
  daily_minutes: [10, 0, 30, 20, 15, 25, 20],
  hourly_plays: Array.from({ length: 24 }, (_, hour) => (hour === 22 ? 12 : 1)),
  top_tracks: [{ track: makeTrack({ id: 1, title: 'خانه' }), plays: 8 }],
  top_artists: [{ id: 3, name: 'ابی', plays: 12 }],
  sources: { playlist: 30, search: 10 },
};

beforeEach(() => __setAuthForTests('token'));

describe('listening stats', () => {
  it('shows the totals, the trend, the charts and the tops', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/me/stats': (_: unknown) => week,
      'POST /v1/tracks/thumbs': { items: {} },
    });
    renderApp(<StatsScreen />);
    expect(await screen.findByText(/۲۰٪ بیشتر از دورهٔ قبل/)).toBeInTheDocument();
    expect(screen.getByText('خانه')).toBeInTheDocument();
    expect(screen.getByText('ابی')).toBeInTheDocument();
    // Seven day bars and twenty-four hour bars, each with its value for screen readers.
    expect(screen.getAllByRole('button', { name: /دقیقه$/ })).toHaveLength(7);
    expect(screen.getAllByRole('button', { name: /پخش$/ })).toHaveLength(24);
    await userEvent.click(screen.getByText('۳۰ روز'));
    expect(calls.some((c) => c.url === '/v1/me/stats?days=30')).toBe(true);
  });

  it('says so when nothing was played', async () => {
    mockApi({ ...authRoutes(), 'GET /v1/me/stats': { ...week, plays: 0, minutes: 0 } });
    renderApp(<StatsScreen />);
    expect(await screen.findByText(/چیزی گوش نداده‌ای/)).toBeInTheDocument();
  });
});
