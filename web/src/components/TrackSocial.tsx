import { useMemo, useState } from 'react';

import { ApiError } from '@/api/client';
import { REACTIONS, useCommentActions, useComments, useReactions, useToggleReaction } from '@/api/comments';
import { Sheet, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { usePack } from '@/i18n/pack';
import { talkStrings } from '@/i18n/packs/talk';
import { relativeTime } from '@/lib/format';
import { haptic } from '@/lib/telegram';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

export function ReactionBar({ trackId }: { trackId: number }) {
  const tp = usePack(talkStrings);
  const reactions = useReactions(trackId);
  const toggle = useToggleReaction(trackId);
  const mine = new Set(reactions.data?.mine ?? []);
  return (
    <div className="no-scrollbar flex gap-1.5 overflow-x-auto" role="group" aria-label={tp('talk.react')}>
      {REACTIONS.map(({ key, glyph }) => {
        const on = mine.has(key);
        const count = reactions.data?.counts[key] ?? 0;
        return (
          <button data-focusable
            key={key}
            type="button"
            aria-pressed={on}
            aria-label={key}
            onClick={() => {
              haptic('select');
              toggle.mutate({ key, on: !on });
            }}
            className={cx(
              'flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1.5 text-[13px] transition-transform active:scale-90',
              on ? 'bg-[var(--accent)] text-[var(--accent-ink)]' : 'bg-[var(--fill)]',
            )}
          >
            <span aria-hidden>{glyph}</span>
            {count > 0 && <span className="text-[11.5px] tabular-nums">{count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** Reactions, then the conversation about a song, with a composer at the bottom. */
export function TrackSocial({ trackId }: { trackId: number }) {
  const { t, lang } = useI18n();
  const tp = usePack(talkStrings);
  const toast = useUi((s) => s.toast);
  const comments = useComments(trackId);
  const { add, remove } = useCommentActions(trackId);
  const playingHere = usePlayer((s) => s.current?.id === trackId);
  const position = usePlayer((s) => Math.floor(s.position));
  const seek = usePlayer((s) => s.seek);
  const [body, setBody] = useState('');
  const [atMoment, setAtMoment] = useState(false);
  const items = useMemo(() => comments.data?.pages.flatMap((page) => page.items) ?? [], [comments.data]);
  const total = comments.data?.pages[0]?.total ?? 0;

  const send = () => {
    add.mutate(
      { body: body.trim(), at_second: playingHere && atMoment ? position : null },
      {
        onSuccess: () => {
          setBody('');
          setAtMoment(false);
        },
        onError: (error) => {
          if (error instanceof ApiError && error.details.reason === 'no_links') toast(tp('talk.noLinks'), 'error');
          else if (error instanceof ApiError && error.status === 429) toast(tp('talk.slowDown'), 'error');
          else toast(t('app.error'), 'error');
        },
      },
    );
  };

  return (
    <div>
      <ReactionBar trackId={trackId} />
      <p className="mt-4 text-[13px] font-bold">{tp('talk.count', { count: total })}</p>

      <div className="mt-2 flex items-end gap-2">
        <textarea
          value={body}
          maxLength={500}
          rows={2}
          onChange={(event) => setBody(event.target.value)}
          placeholder={tp('talk.placeholder')}
          aria-label={tp('talk.placeholder')}
          className="min-w-0 flex-1 resize-none rounded-xl bg-[var(--fill)] px-3 py-2 text-[13.5px] outline-none"
        />
        <button data-focusable
          type="button"
          disabled={!body.trim() || add.isPending}
          onClick={send}
          className="rounded-xl bg-[var(--accent)] px-4 py-2.5 text-[13px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
        >
          {add.isPending ? <Spinner size={14} /> : tp('talk.send')}
        </button>
      </div>
      {playingHere && (
        <label className="mt-1.5 flex items-center gap-2 text-[12px] text-[var(--ink-dim)]">
          <input type="checkbox" checked={atMoment} onChange={(event) => setAtMoment(event.target.checked)} className="accent-[var(--accent)]" />
          {tp('talk.pinTime', { time: clock(position) })}
        </label>
      )}

      <ul className="mt-3 flex flex-col">
        {comments.isLoading && <Spinner />}
        {!comments.isLoading && items.length === 0 && (
          <li className="py-6 text-center text-[13px] text-[var(--ink-faint)]">{tp('talk.empty')}</li>
        )}
        {items.map((comment) => (
          <li key={comment.id} className="border-b border-[var(--separator)] py-2.5 last:border-0">
            <div className="flex items-center gap-2 text-[12px]">
              <span className="font-bold">{comment.author.first_name}</span>
              <span className="text-[var(--ink-faint)]">{relativeTime(comment.created_at, lang)}</span>
              {comment.at_second != null && (
                <button data-focusable
                  type="button"
                  disabled={!playingHere}
                  onClick={() => seek(comment.at_second ?? 0)}
                  className="rounded-full bg-[var(--fill)] px-2 py-0.5 text-[11px] text-[var(--accent)] disabled:text-[var(--ink-dim)]"
                >
                  {tp('talk.atTime', { time: clock(comment.at_second) })}
                </button>
              )}
              {comment.mine && (
                <button data-focusable type="button" onClick={() => remove.mutate(comment.id)} className="ms-auto text-[11px] text-[#ff9a9a]">
                  {tp('talk.delete')}
                </button>
              )}
            </div>
            <p className="mt-1 whitespace-pre-wrap break-words text-[13.5px] leading-6">{comment.body}</p>
          </li>
        ))}
      </ul>
      {comments.hasNextPage && (
        <button data-focusable
          type="button"
          onClick={() => void comments.fetchNextPage()}
          className="mt-2 w-full rounded-xl bg-[var(--fill)] py-2 text-[12.5px]"
        >
          {comments.isFetchingNextPage ? <Spinner size={14} /> : tp('talk.more')}
        </button>
      )}
    </div>
  );
}

export function CommentsSheet({ trackId, open, onClose }: { trackId: number; open: boolean; onClose: () => void }) {
  const tp = usePack(talkStrings);
  return (
    <Sheet open={open} onClose={onClose} title={tp('talk.title')}>
      {open && <TrackSocial trackId={trackId} />}
    </Sheet>
  );
}
