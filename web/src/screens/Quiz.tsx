import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError } from '@/api/client';
import {
  fetchClip,
  useAnswerQuiz,
  useQuizBoard,
  useStartQuiz,
  type QuizAnswer,
  type QuizGame,
} from '@/api/quiz';
import { ChevronIcon, PlayIcon } from '@/components/icons';
import { Glass, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { usePack } from '@/i18n/pack';
import { quizStrings } from '@/i18n/packs/quiz';
import { BOT_USERNAME, haptic, openTelegramLink } from '@/lib/telegram';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

/** How long a clip plays before it stops on its own. */
const CLIP_SECONDS = 15;

function Board() {
  const tp = usePack(quizStrings);
  const [scope, setScope] = useState<'friends' | 'global'>('friends');
  const board = useQuizBoard(scope);

  return (
    <Glass className="mt-4 p-3">
      <div className="flex items-center justify-between">
        <h2 className="text-[15px] font-bold">{tp('quiz.board')}</h2>
        <div className="flex gap-1.5" role="tablist">
          {(['friends', 'global'] as const).map((key) => (
            <button data-focusable
              key={key}
              type="button"
              role="tab"
              aria-selected={scope === key}
              onClick={() => setScope(key)}
              className={cx(
                'rounded-full px-3 py-1 text-[12px]',
                scope === key ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)] text-[var(--ink-dim)]',
              )}
            >
              {tp(`quiz.board.${key}`)}
            </button>
          ))}
        </div>
      </div>
      {board.data && board.data.my_best > 0 && (
        <p className="mt-1 text-[12px] text-[var(--ink-dim)]">{tp('quiz.best', { score: board.data.my_best })}</p>
      )}
      {board.isLoading ? (
        <div className="grid place-items-center py-6">
          <Spinner />
        </div>
      ) : (board.data?.entries.length ?? 0) === 0 ? (
        <p className="py-5 text-center text-[13px] text-[var(--ink-dim)]">{tp('quiz.board.empty')}</p>
      ) : (
        <ol className="mt-2 flex flex-col">
          {board.data?.entries.map((entry) => (
            <li
              key={entry.user_id}
              className={cx(
                'flex items-center gap-3 rounded-xl px-2 py-2',
                entry.is_me && 'bg-[var(--fill)] font-bold',
              )}
            >
              <span className="w-6 text-center text-[14px]">
                {entry.rank === 1 ? '🥇' : entry.rank === 2 ? '🥈' : entry.rank === 3 ? '🥉' : entry.rank}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13.5px]">
                {entry.is_me ? tp('quiz.you') : entry.first_name}
                <span className="ms-1.5 text-[11px] font-normal text-[var(--ink-faint)]">
                  {tp('quiz.board.games', { count: entry.games })}
                </span>
              </span>
              <span className="text-[13.5px] tabular-nums">{entry.best_score}</span>
            </li>
          ))}
        </ol>
      )}
    </Glass>
  );
}

/** One clip at a time, outside the main player, stopped when the question changes. */
function useClip() {
  const audio = useRef<HTMLAudioElement | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [state, setState] = useState<'idle' | 'loading' | 'playing' | 'failed'>('idle');
  const [elapsed, setElapsed] = useState(0);

  const stop = useCallback(() => {
    window.clearInterval(timer.current);
    audio.current?.pause();
    setState((current) => (current === 'failed' ? current : 'idle'));
  }, []);

  const play = useCallback(
    async (load: () => Promise<{ url: string }>) => {
      stop();
      setState('loading');
      setElapsed(0);
      try {
        const { url } = await load();
        const el = audio.current ?? new Audio();
        audio.current = el;
        el.src = url;
        el.currentTime = 0;
        await el.play();
        setState('playing');
        const started = Date.now();
        timer.current = window.setInterval(() => {
          const seconds = (Date.now() - started) / 1000;
          setElapsed(Math.min(seconds, CLIP_SECONDS));
          if (seconds >= CLIP_SECONDS) stop();
        }, 200);
      } catch {
        setState('failed');
      }
    },
    [stop],
  );

  useEffect(() => () => {
    window.clearInterval(timer.current);
    audio.current?.pause();
  }, []);

  return { play, stop, state, elapsed };
}

function Round({ game, onDone }: { game: QuizGame; onDone: (game: QuizGame) => void }) {
  const tp = usePack(quizStrings);
  const [index, setIndex] = useState(() => {
    const open = game.questions.findIndex((question) => question.answered == null);
    return open === -1 ? 0 : open;
  });
  const [score, setScore] = useState(game.score);
  const [correct, setCorrect] = useState(game.correct);
  const [result, setResult] = useState<QuizAnswer | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const answer = useAnswerQuiz(game.id);
  const clip = useClip();
  const question = game.questions[index];
  const total = game.questions.length;

  const { play: playClip } = clip;
  useEffect(() => {
    if (!question) return;
    setResult(null);
    setPicked(null);
    void playClip(() => fetchClip(game.id, question.index));
  }, [game.id, question, playClip]);

  if (!question) return null;

  const choose = (choiceId: number) => {
    if (result || answer.isPending) return;
    setPicked(choiceId);
    clip.stop();
    answer.mutate(
      { index: question.index, choiceId },
      {
        onSuccess: (outcome) => {
          haptic(outcome.correct ? 'success' : 'error');
          setResult(outcome);
          setScore(outcome.score);
          if (outcome.correct) setCorrect((n) => n + 1);
        },
      },
    );
  };

  const last = index + 1 >= total;
  const next = () => {
    if (last) onDone({ ...game, score, correct, finished: true });
    else setIndex(index + 1);
  };

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between text-[12.5px] text-[var(--ink-dim)]">
        <span>{tp('quiz.question', { n: index + 1, total })}</span>
        <span className="font-bold text-[var(--ink)]">{tp('quiz.score', { score })}</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--fill)]">
        <div
          className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-200"
          style={{ width: `${((index + (result ? 1 : 0)) / total) * 100}%` }}
        />
      </div>

      <Glass className="mt-4 flex flex-col items-center gap-3 p-5">
        <div
          className={cx(
            'grid h-24 w-24 place-items-center rounded-full bg-[var(--fill-strong)] text-[38px]',
            clip.state === 'playing' && 'animate-pulse',
          )}
          aria-hidden
        >
          🎧
        </div>
        <p className="text-[13px] text-[var(--ink-dim)]" aria-live="polite">
          {clip.state === 'loading'
            ? tp('quiz.loadingClip')
            : clip.state === 'failed'
              ? tp('quiz.clipFailed')
              : tp('quiz.listen')}
        </p>
        <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--fill)]">
          <div className="h-full bg-[var(--accent)]" style={{ width: `${(clip.elapsed / CLIP_SECONDS) * 100}%` }} />
        </div>
        {!result && clip.state !== 'playing' && clip.state !== 'loading' && (
          <button data-focusable
            type="button"
            onClick={() => void clip.play(() => fetchClip(game.id, question.index))}
            className="flex items-center gap-1.5 rounded-full bg-[var(--fill)] px-3 py-1.5 text-[12.5px]"
          >
            <PlayIcon size={14} />
            {tp('quiz.replay')}
          </button>
        )}
      </Glass>

      <div className="mt-4 grid gap-2">
        {question.choices.map((choice) => {
          const isAnswer = result?.answer_id === choice.id;
          const isWrongPick = result && picked === choice.id && !result.correct;
          return (
            <button data-focusable
              key={choice.id}
              type="button"
              disabled={Boolean(result) || answer.isPending}
              onClick={() => choose(choice.id)}
              className={cx(
                'rounded-2xl px-4 py-3 text-start transition-colors',
                isAnswer
                  ? 'bg-[#2fbf71] text-white'
                  : isWrongPick
                    ? 'bg-[#e5484d] text-white'
                    : 'bg-[var(--fill)] active:bg-[var(--fill-strong)]',
              )}
            >
              <span className="block truncate text-[14.5px] font-semibold">{choice.title}</span>
              {choice.artist && <span className="block truncate text-[12px] opacity-75">{choice.artist}</span>}
            </button>
          );
        })}
      </div>

      {result && (
        <div className="mt-4 flex items-center gap-3">
          <p className="min-w-0 flex-1 text-[14px] font-bold" aria-live="polite">
            {result.correct ? tp('quiz.right', { points: result.points }) : tp('quiz.wrong')}
            {!result.correct && result.answer && (
              <span className="block truncate text-[12.5px] font-normal text-[var(--ink-dim)]">{result.answer.title}</span>
            )}
          </p>
          <button data-focusable
            type="button"
            onClick={next}
            className="rounded-xl bg-[var(--accent)] px-5 py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)]"
          >
            {last ? tp('quiz.finish') : tp('quiz.next')}
          </button>
        </div>
      )}
    </div>
  );
}

function Result({ game, onAgain }: { game: QuizGame; onAgain: () => void }) {
  const tp = usePack(quizStrings);
  const share = () => {
    const link = `https://t.me/${BOT_USERNAME}?startapp=quiz`;
    openTelegramLink(
      `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(tp('quiz.shareText', { score: game.score }))}`,
    );
  };
  return (
    <Glass className="mt-4 flex flex-col items-center gap-2 p-6 text-center">
      <div className="text-[44px]" aria-hidden>
        {game.correct * 2 >= game.questions.length ? '🏆' : '🎵'}
      </div>
      <p className="text-[30px] font-extrabold tabular-nums">{game.score}</p>
      <p className="text-[13.5px] text-[var(--ink-dim)]">
        {tp('quiz.result', { correct: game.correct, total: game.questions.length })}
      </p>
      <div className="mt-3 flex w-full gap-2">
        <button data-focusable
          type="button"
          onClick={onAgain}
          className="flex-1 rounded-xl bg-[var(--accent)] py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)]"
        >
          {tp('quiz.again')}
        </button>
        <button data-focusable type="button" onClick={share} className="flex-1 rounded-xl bg-[var(--fill)] py-2.5 text-[13.5px]">
          {tp('quiz.share')}
        </button>
      </div>
    </Glass>
  );
}

export function QuizScreen() {
  const { t } = useI18n();
  const tp = usePack(quizStrings);
  const navigate = useNavigate();
  const start = useStartQuiz();
  const toast = useUi((s) => s.toast);
  const pauseMain = usePlayer((s) => s.isPlaying);
  const toggle = usePlayer((s) => s.toggle);
  const [game, setGame] = useState<QuizGame | null>(null);
  const [finished, setFinished] = useState<QuizGame | null>(null);
  const [tooFew, setTooFew] = useState(false);

  const begin = (source: 'library' | 'catalog') => {
    // The clip and the main player must not play over each other.
    if (pauseMain) void toggle();
    setFinished(null);
    start.mutate(source, {
      onSuccess: (created) => setGame(created),
      onError: (error) => {
        if (error instanceof ApiError && error.details.reason === 'not_enough_songs') setTooFew(true);
        else toast(t('app.error'), 'error');
      },
    });
  };

  return (
    <div className="px-4 pb-6 pt-4 tv-safe [container-type:inline-size]">
      <div className="flex items-center gap-2">
        <button data-focusable type="button" aria-label={t('common.back')} onClick={() => navigate(-1)} className="p-1.5">
          <ChevronIcon size={22} className="rtl:rotate-180" />
        </button>
        <h1 className="text-[21px] font-bold">🎮 {tp('quiz.title')}</h1>
      </div>

      {game && !finished ? (
        <Round
          key={game.id}
          game={game}
          onDone={(done) => {
            setFinished(done);
            setGame(null);
          }}
        />
      ) : (
        <>
          {finished && <Result game={finished} onAgain={() => begin(finished.source)} />}
          {!finished && (
            <Glass className="mt-4 p-4">
              <p className="text-[13.5px] leading-6 text-[var(--ink-dim)]">{tp('quiz.tagline')}</p>
              {tooFew && <p className="mt-2 text-[12.5px] text-[var(--accent)]">{tp('quiz.notEnough')}</p>}
              <div className="mt-4 flex flex-col gap-2">
                {!tooFew && (
                  <button data-focusable
                    type="button"
                    disabled={start.isPending}
                    onClick={() => begin('library')}
                    className="rounded-xl bg-[var(--accent)] py-3 text-[14px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
                  >
                    {start.isPending ? <Spinner size={16} /> : tp('quiz.playLibrary')}
                  </button>
                )}
                <button data-focusable
                  type="button"
                  disabled={start.isPending}
                  onClick={() => begin('catalog')}
                  className={cx(
                    'rounded-xl py-3 text-[14px] disabled:opacity-50',
                    tooFew ? 'bg-[var(--accent)] font-bold text-[var(--accent-ink)]' : 'bg-[var(--fill)]',
                  )}
                >
                  {tp('quiz.playCatalog')}
                </button>
              </div>
            </Glass>
          )}
          <Board />
        </>
      )}
    </div>
  );
}
