import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { Playlists } from '@/screens/Playlists';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

beforeEach(() => {
  __setAuthForTests('token');
  useUi.setState({ toasts: [] });
});

describe('playlist import', () => {
  it('matches pasted songs, lets you untick one, and creates the playlist', async () => {
    const found = {
      matched: 2,
      total: 3,
      skipped: 0,
      lines: [
        { line: 'Ebi - Khaneh', track: makeTrack({ id: 1, title: 'Khaneh' }), score: 1 },
        { line: 'Googoosh - Pol', track: makeTrack({ id: 2, title: 'Pol' }), score: 0.9 },
        { line: 'Nobody - Nothing', track: null, score: 0 },
      ],
    };
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/playlists': [],
      'GET /v1/folders': [],
      'GET /v1/me/pins': [],
      'POST /v1/playlists/import/match': found,
      'POST /v1/playlists': { id: 12, name: 'x', items: [] },
    });
    renderApp(<Playlists />);
    await userEvent.click(await screen.findByLabelText('وارد کردن پلی‌لیست'));
    await userEvent.type(screen.getByRole('textbox', { name: 'وارد کردن پلی‌لیست' }), 'Ebi - Khaneh');
    await userEvent.click(screen.getByText('پیدا کردن آهنگ‌ها'));
    expect(await screen.findByText(/از (3|۳) آهنگ پیدا شد/)).toBeInTheDocument();
    expect(screen.getByText('پیدا نشد')).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('Pol'));
    await userEvent.click(screen.getByText(/ساختن پلی‌لیست/));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST' && c.url === '/v1/playlists')?.body).toEqual({
        name: 'پلی‌لیست واردشده',
        track_ids: [1],
      }),
    );
  });
});
