import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiError, post, type Track } from '@/api/client';
import { useCreatePlaylist } from '@/api/playlists';
import { Sheet, Spinner, cx } from '@/components/ui';
import { useI18n } from '@/i18n';
import { usePack } from '@/i18n/pack';
import { importStrings } from '@/i18n/packs/import';
import { artistNames } from '@/lib/format';
import { useUi } from '@/store/ui';

type ImportResult = {
  lines: { line: string; track?: Track | null; score: number }[];
  matched: number;
  total: number;
  skipped: number;
};

/** Paste a list (or pick an exported CSV), check what was found, make a playlist of it. */
export function ImportPlaylistSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const tp = usePack(importStrings);
  const navigate = useNavigate();
  const toast = useUi((s) => s.toast);
  const showUpsell = useUi((s) => s.showUpsell);
  const [body, setBody] = useState('');
  const [name, setName] = useState('');
  const [result, setResult] = useState<ImportResult | null>(null);
  const [kept, setKept] = useState<Set<number>>(new Set());
  const match = useMutation({ mutationFn: (text: string) => post<ImportResult>('/v1/playlists/import/match', { text }) });
  const create = useCreatePlaylist();

  const find = () =>
    match.mutate(body, {
      onSuccess: (found) => {
        setResult(found);
        setKept(new Set(found.lines.flatMap((line) => (line.track ? [line.track.id] : []))));
      },
      onError: () => toast(t('app.error'), 'error'),
    });

  const save = () => {
    const ids = (result?.lines ?? []).flatMap((line) => (line.track && kept.has(line.track.id) ? [line.track.id] : []));
    create.mutate(
      { name: name.trim() || tp('import.defaultName'), track_ids: ids.slice(0, 500) },
      {
        onSuccess: (playlist) => {
          toast(tp('import.done'), 'success');
          setBody('');
          setResult(null);
          onClose();
          navigate(`/playlist/${playlist.id}`);
        },
        onError: (error) => {
          if (error instanceof ApiError && error.isPlanLimit) {
            onClose();
            showUpsell({ kind: 'playlists', limit: Number(error.details.limit ?? 0) });
          } else toast(t('app.error'), 'error');
        },
      },
    );
  };

  return (
    <Sheet open={open} onClose={onClose} title={tp('import.title')}>
      {!result ? (
        <>
          <p className="text-[12.5px] leading-5 text-[var(--ink-dim)]">{tp('import.hint')}</p>
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder={tp('import.placeholder')}
            aria-label={tp('import.title')}
            rows={8}
            className="mt-3 w-full resize-none rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[13.5px] leading-6 outline-none"
          />
          <div className="mt-2 flex items-center gap-2">
            <label className="cursor-pointer rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[12.5px]">
              {tp('import.file')}
              <input
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void file.text().then(setBody);
                }}
              />
            </label>
            <button
              type="button"
              disabled={!body.trim() || match.isPending}
              onClick={find}
              className="flex-1 rounded-xl bg-[var(--accent)] py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
            >
              {match.isPending ? <Spinner size={16} /> : tp('import.find')}
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-[13.5px] font-bold">{tp('import.result', { matched: result.matched, total: result.total })}</p>
          {result.skipped > 0 && (
            <p className="text-[11.5px] text-[var(--ink-faint)]">{tp('import.skipped', { count: result.skipped })}</p>
          )}
          <ul className="mt-2 max-h-[40vh] overflow-y-auto">
            {result.lines.map((line, index) => (
              <li key={index} className="flex items-center gap-2.5 border-b border-[var(--separator)] py-2 last:border-0">
                {line.track ? (
                  <input
                    type="checkbox"
                    aria-label={line.track.title}
                    checked={kept.has(line.track.id)}
                    onChange={() => {
                      const next = new Set(kept);
                      const id = line.track?.id ?? 0;
                      if (next.has(id)) next.delete(id);
                      else next.add(id);
                      setKept(next);
                    }}
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                ) : (
                  <span className="w-4 text-center text-[var(--ink-faint)]">✕</span>
                )}
                <span className="min-w-0 flex-1">
                  <span className={cx('block truncate text-[13px]', !line.track && 'text-[var(--ink-faint)]')}>
                    {line.track ? line.track.title : line.line}
                  </span>
                  <span className="block truncate text-[11px] text-[var(--ink-faint)]">
                    {line.track ? `${artistNames(line.track)} · ${line.line}` : tp('import.notFound')}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={tp('import.defaultName')}
            aria-label={tp('import.name')}
            className="mt-3 w-full rounded-xl bg-[var(--fill)] px-3.5 py-2.5 text-[14px] outline-none"
          />
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={() => setResult(null)} className="rounded-xl bg-[var(--fill)] px-4 py-2.5 text-[13px]">
              {tp('import.back')}
            </button>
            <button
              type="button"
              disabled={kept.size === 0 || create.isPending}
              onClick={save}
              className="flex-1 rounded-xl bg-[var(--accent)] py-2.5 text-[13.5px] font-bold text-[var(--accent-ink)] disabled:opacity-50"
            >
              {create.isPending ? <Spinner size={16} /> : tp('import.create', { count: kept.size })}
            </button>
          </div>
        </>
      )}
    </Sheet>
  );
}
