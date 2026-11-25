import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { DEFAULT_RULES } from '@/api/smart';
import { PlaylistScreen, Playlists } from '@/screens/Playlists';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const tracks = [makeTrack({ id: 1, title: 'خانه' }), makeTrack({ id: 2, title: 'پل' })];

const smart = {
  id: 9,
  name: 'ابی کوتاه',
  description: null,
  kind: 'smart' as const,
  is_public: false,
  is_collaborative: false,
  share_slug: null,
  share_url: null,
  tracks_count: 2,
  duration_total: 400,
  is_owner: true,
  can_edit: true,
  updated_at: '2026-11-25T10:00:00Z',
  rules: { ...DEFAULT_RULES, max_duration: 240 },
  refreshed_at: '2026-11-25T10:00:00Z',
};

beforeEach(() => {
  __setAuthForTests('token');
  usePlayer.setState({ queue: [], current: null, index: 0, isPlaying: false });
  useUi.setState({ toasts: [], upsell: null, actionTrack: null });
  window.location.hash = '#/';
});

function routes(overrides: Record<string, unknown> = {}) {
  return {
    ...authRoutes(),
    'POST /v1/tracks/thumbs': { items: {} },
    'GET /v1/playlists': [smart],
    'GET /v1/playlists/9': { ...smart, items: tracks },
    'GET /v1/library/channels': [],
    'POST /v1/playlists/smart/preview': { total: 12, items: [] },
    ...overrides,
  };
}

describe('smart playlists', () => {
  it('builds rules, shows how many songs match and saves', async () => {
    const { calls } = mockApi(routes({ 'POST /v1/playlists/smart': { ...smart, id: 10, items: [] } }));
    renderApp(<Playlists />);
    await userEvent.click(await screen.findByLabelText('پلی‌لیست هوشمند'));
    await userEvent.type(await screen.findByLabelText('نام پلی‌لیست'), 'فراموش‌شده‌ها');
    await userEvent.click(screen.getByText('لایک‌شده‌ها'));
    await userEvent.click(screen.getByText('یک ماه است گوش نداده‌ام'));
    await userEvent.click(screen.getByText('تصادفی'));
    expect(await screen.findByText(/(12|۱۲) آهنگ با این قانون‌ها جور است/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('ذخیره'));
    await waitFor(() => {
      const call = calls.find((one) => one.method === 'POST' && one.url === '/v1/playlists/smart');
      expect(call?.body).toEqual({
        name: 'فراموش‌شده‌ها',
        rules: { ...DEFAULT_RULES, source: 'likes', not_played_days: 30, sort: 'random' },
      });
    });
  });

  it('marks a smart playlist and lets its owner edit the rules and update it', async () => {
    const { calls } = mockApi(
      routes({
        'POST /v1/playlists/9/refresh': { ...smart, items: tracks },
        'PUT /v1/playlists/9/rules': { ...smart, items: tracks },
      }),
    );
    renderApp(<PlaylistScreen />, { route: '/playlist/9', path: '/playlist/:id' });
    expect(await screen.findByText(/⚡ هوشمند/)).toBeInTheDocument();
    // Songs come from the rules: no drag handles, no remove buttons.
    expect(screen.queryByLabelText(/جابه‌جایی|Reorder/)).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByLabelText('بیشتر')[0]!);
    await userEvent.click(await screen.findByText(/همین الان به‌روز کن/));
    await waitFor(() => expect(calls.some((one) => one.url === '/v1/playlists/9/refresh')).toBe(true));

    await userEvent.click(screen.getAllByLabelText('بیشتر')[0]!);
    await userEvent.click(await screen.findByText(/ویرایش قانون‌ها/));
    await userEvent.click(await screen.findByText('بالای ۵ دقیقه'));
    await userEvent.click(screen.getByText('ذخیره'));
    await waitFor(() => {
      const call = calls.find((one) => one.method === 'PUT' && one.url === '/v1/playlists/9/rules');
      expect(call?.body).toEqual({ rules: { ...DEFAULT_RULES, min_duration: 300, max_duration: null } });
    });
  });
});
