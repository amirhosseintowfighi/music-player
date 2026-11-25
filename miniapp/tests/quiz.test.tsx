import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { QuizScreen } from '@/screens/Quiz';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

function choice(id: number, title: string) {
  return { id, title, artist: 'ابی' };
}

const game = {
  id: 5,
  source: 'library' as const,
  score: 0,
  correct: 0,
  finished: false,
  max_score: 400,
  questions: [
    { index: 0, choices: [choice(1, 'خانه'), choice(2, 'پل'), choice(3, 'باران'), choice(4, 'شب')], points: 0 },
    { index: 1, choices: [choice(2, 'پل'), choice(1, 'خانه'), choice(4, 'شب'), choice(3, 'باران')], points: 0 },
  ],
};

const board = { scope: 'friends', entries: [], my_games: 0, my_best: 0, my_week_best: 0 };

beforeEach(() => {
  __setAuthForTests('token');
  useUi.setState({ toasts: [] });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
});

const clip = { url: 'https://edge/s/1?t=x', thumb_url: null, expires_at: 0, size: 1, mime: 'audio/mpeg' };

describe('guess the song', () => {
  it('plays a round: clip, answer, score, result', async () => {
    const { calls } = mockApi({
      ...authRoutes(),
      'GET /v1/quiz/board': board,
      'POST /v1/quiz': game,
      'POST /v1/quiz/5/questions/0/clip': clip,
      'POST /v1/quiz/5/questions/1/clip': clip,
      'POST /v1/quiz/5/questions/0/answer': {
        correct: true,
        answer_id: 1,
        points: 180,
        score: 180,
        finished: false,
        answer: makeTrack({ id: 1, title: 'خانه' }),
      },
      'POST /v1/quiz/5/questions/1/answer': {
        correct: false,
        answer_id: 2,
        points: 0,
        score: 180,
        finished: true,
        answer: makeTrack({ id: 2, title: 'پل' }),
      },
    });
    renderApp(<QuizScreen />);
    await userEvent.click(await screen.findByText('از کانال‌های من'));
    await waitFor(() => expect(calls.some((c) => c.url === '/v1/quiz/5/questions/0/clip')).toBe(true));

    await userEvent.click(await screen.findByText('خانه'));
    expect(await screen.findByText(/درسته!/)).toBeInTheDocument();
    expect(calls.find((c) => c.url === '/v1/quiz/5/questions/0/answer')?.body).toEqual({ choice_id: 1 });

    await userEvent.click(screen.getByText('بعدی'));
    await userEvent.click(await screen.findByText('شب'));
    expect(await screen.findByText('نه! جواب درست:')).toBeInTheDocument();
    await userEvent.click(screen.getByText('دیدن نتیجه'));
    expect(await screen.findByText(/۱۸۰|180/)).toBeInTheDocument();
    expect(screen.getByText('دوستانت را به چالش بکش')).toBeInTheDocument();
  });

  it('offers popular songs when the library is too small', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/quiz/board': board,
      'POST /v1/quiz': new Response(
        JSON.stringify({
          error: { code: 'invalid_input', message: 'x', details: { reason: 'not_enough_songs' } },
        }),
        { status: 422 },
      ),
    });
    renderApp(<QuizScreen />);
    await userEvent.click(await screen.findByText('از کانال‌های من'));
    expect(await screen.findByText(/آهنگ کافی ندارند/)).toBeInTheDocument();
    expect(screen.queryByText('از کانال‌های من')).not.toBeInTheDocument();
  });
});
