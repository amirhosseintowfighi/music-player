/** Guess the song (0019). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { components } from './schema';
import { get, post, type StreamTicket } from './client';

export type QuizGame = components['schemas']['QuizGameOut'];
export type QuizAnswer = components['schemas']['QuizAnswerOut'];
export type QuizBoard = components['schemas']['QuizBoardOut'];

export const quizKeys = {
  board: (scope: 'friends' | 'global') => ['quiz', 'board', scope] as const,
};

export function useStartQuiz() {
  return useMutation({
    mutationFn: (source: 'library' | 'catalog') => post<QuizGame>('/v1/quiz', { source }),
  });
}

export function fetchClip(gameId: number, index: number): Promise<StreamTicket> {
  return post<StreamTicket>(`/v1/quiz/${gameId}/questions/${index}/clip`);
}

export function useAnswerQuiz(gameId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ index, choiceId }: { index: number; choiceId: number }) =>
      post<QuizAnswer>(`/v1/quiz/${gameId}/questions/${index}/answer`, { choice_id: choiceId }),
    onSuccess: (answer) => {
      if (answer.finished) void client.invalidateQueries({ queryKey: ['quiz', 'board'] });
    },
  });
}

export function useQuizBoard(scope: 'friends' | 'global') {
  return useQuery({
    queryKey: quizKeys.board(scope),
    queryFn: () => get<QuizBoard>(`/v1/quiz/board?scope=${scope}`),
  });
}
