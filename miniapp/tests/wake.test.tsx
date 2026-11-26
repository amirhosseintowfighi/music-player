import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { localOffset } from '@/api/alarms';
import { AlarmsScreen, WakeScreen } from '@/screens/Wake';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const alarm = {
  id: 3,
  minute_of_day: 420,
  days: 31,
  tz_offset_minutes: 210,
  label: 'باشگاه',
  source: 'likes' as const,
  playlist_id: null,
  enabled: true,
  next_fire_at: '2026-11-26T03:30:00Z',
  last_fired_at: null,
};

beforeEach(() => {
  __setAuthForTests('token');
  useUi.setState({ toasts: [] });
  usePlayer.setState({ queue: [], current: null, index: 0, isPlaying: false });
});

function routes(overrides: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'GET /v1/alarms': [alarm],
    'GET /v1/playlists': [],
    'GET /v1/library/likes': { items: [], next_cursor: null },
    'POST /v1/tracks/thumbs': { items: {} },
    ...overrides,
  };
}

describe('alarms', () => {
  it('lists alarms and creates one in the local time zone', async () => {
    const { calls } = mockApi(routes({ 'POST /v1/alarms': { ...alarm, id: 4 } }));
    renderApp(<AlarmsScreen />);
    expect(await screen.findByText('07:00')).toBeInTheDocument();
    expect(screen.getByText(/باشگاه · روزهای کاری/)).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('زنگ جدید'));
    await userEvent.click(await screen.findByText('آهنگ‌های صبح‌هایم'));
    await userEvent.click(screen.getByText('ذخیره'));
    await waitFor(() => {
      const call = calls.find((one) => one.method === 'POST' && one.url === '/v1/alarms');
      expect(call?.body).toEqual({
        minute_of_day: 420,
        days: 127,
        tz_offset_minutes: localOffset(),
        label: null,
        source: 'daylist',
        playlist_id: null,
      });
    });
  });

  it('switches an alarm off', async () => {
    const { calls } = mockApi(routes({ 'PATCH /v1/alarms/3': { ...alarm, enabled: false, next_fire_at: null } }));
    renderApp(<AlarmsScreen />);
    await userEvent.click(await screen.findByRole('switch', { name: '07:00' }));
    await waitFor(() =>
      expect(calls.find((one) => one.method === 'PATCH')?.body).toMatchObject({ enabled: false }),
    );
  });

  it('starts focus mode with a countdown', async () => {
    mockApi(routes());
    renderApp(<AlarmsScreen />);
    await userEvent.click(await screen.findByText('بدون موزیک'));
    await userEvent.click(screen.getByText('شروع تمرکز'));
    expect(await screen.findByText('وقت تمرکز')).toBeInTheDocument();
    expect(screen.getByText(/25:00|24:59/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('پایان'));
    expect(await screen.findByText('شروع تمرکز')).toBeInTheDocument();
  });

  it('wakes up: plays the alarm music, quiet at first', async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    usePlayer.setState({ play, volume: 0.8 });
    mockApi(routes({ 'GET /v1/alarms/3/wake': { alarm, items: [makeTrack({ id: 9, title: 'صبح' })] } }));
    renderApp(<WakeScreen />, { route: '/wake/3', path: '/wake/:id' });
    expect(await screen.findByText('صبح بخیر ☀️')).toBeInTheDocument();
    await waitFor(() => expect(play).toHaveBeenCalled());
    expect(play.mock.calls[0]?.[0].queue[0].id).toBe(9);
    expect(usePlayer.getState().volume).toBeLessThan(0.2);
  });
});
