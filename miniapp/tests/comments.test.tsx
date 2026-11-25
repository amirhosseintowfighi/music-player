import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { TrackSocial } from '@/components/TrackSocial';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const counts = { fire: 2, heart: 0, cry: 0, love: 0, party: 0, clap: 0 };
const comment = {
  id: 1,
  author: { id: 5, first_name: 'سارا' },
  body: 'این تیکه عالیه',
  at_second: 75,
  created_at: new Date().toISOString(),
  mine: false,
};

beforeEach(() => {
  __setAuthForTests('token');
  useUi.setState({ toasts: [] });
  usePlayer.setState({ current: makeTrack({ id: 7 }), position: 42 });
});

describe('comments and reactions', () => {
  it('shows comments, reacts, and posts at the current moment', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/tracks/7/comments': { items: [comment], next_cursor: null, total: 1 },
      'POST /v1/tracks/7/comments': { ...comment, id: 2, mine: true },
      'GET /v1/tracks/7/reactions': { counts, mine: [] },
      'PUT /v1/tracks/7/reactions/fire': { counts: { ...counts, fire: 3 }, mine: ['fire'] },
    });
    renderApp(<TrackSocial trackId={7} />);
    expect(await screen.findByText('این تیکه عالیه')).toBeInTheDocument();
    expect(screen.getByText(/1:15/)).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('fire'));
    await waitFor(() => expect(screen.getByLabelText('fire')).toHaveAttribute('aria-pressed', 'true'));

    await userEvent.type(screen.getByLabelText('نظرت دربارهٔ این آهنگ…'), 'عالی');
    await userEvent.click(screen.getByText(/به همین لحظهٔ آهنگ/));
    await userEvent.click(screen.getByText('ارسال'));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ body: 'عالی', at_second: 42 }),
    );
  });

  it('explains why a link was refused', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/tracks/7/comments': { items: [], next_cursor: null, total: 0 },
      'GET /v1/tracks/7/reactions': { counts, mine: [] },
      'POST /v1/tracks/7/comments': new Response(
        JSON.stringify({ error: { code: 'invalid_input', message: 'x', details: { reason: 'no_links' } } }),
        { status: 422 },
      ),
    });
    renderApp(<TrackSocial trackId={7} />);
    await userEvent.type(await screen.findByLabelText('نظرت دربارهٔ این آهنگ…'), 'https://x');
    await userEvent.click(screen.getByText('ارسال'));
    await waitFor(() => expect(useUi.getState().toasts.at(-1)?.text).toBe('لینک و آیدی در نظرها مجاز نیست.'));
  });
});
