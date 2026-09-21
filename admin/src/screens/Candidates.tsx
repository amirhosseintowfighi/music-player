import { useState } from 'react';

import type { Candidate } from '@/api/client';
import { useCandidates, useImportChannels, useReviewCandidate } from '@/api/hooks';
import { Badge, Button, Card, Empty, Spinner } from '@/components/ui';
import { formatDate, formatNumber } from '@/lib/format';

const SOURCE_LABEL: Record<string, string> = {
  seed: 'دستی',
  user: 'درخواست کاربر',
  crawl_mention: 'منشن در کرال',
  crawl_forward: 'فوروارد',
};

const STATUS_TABS = [
  { value: 'pending', label: 'در انتظار' },
  { value: 'approved', label: 'تأییدشده' },
  { value: 'rejected', label: 'ردشده' },
];

function scoreTone(score: number): 'ok' | 'warn' | 'bad' {
  if (score >= 50) return 'ok';
  return score >= 20 ? 'warn' : 'bad';
}

function Row({
  candidate,
  selected,
  onSelect,
  onApprove,
  onReject,
  busy,
}: {
  candidate: Candidate;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}) {
  const ratio = candidate.audio_ratio;
  return (
    <tr className="border-t border-[var(--color-line)] align-top">
      <td className="p-2">
        <input
          type="checkbox"
          checked={selected}
          aria-label={`انتخاب ${candidate.username}`}
          onChange={(event) => onSelect(event.target.checked)}
        />
      </td>
      <td className="p-2">
        <a
          className="font-medium text-[var(--color-accent)]"
          href={`https://t.me/s/${candidate.username}`}
          target="_blank"
          rel="noreferrer"
        >
          @{candidate.username}
        </a>
        <p className="text-[11px] text-[var(--color-muted)]">
          {candidate.title ?? '—'} · {SOURCE_LABEL[candidate.source] ?? candidate.source}
        </p>
      </td>
      <td className="p-2">
        <Badge tone={scoreTone(candidate.score)}>{formatNumber(Math.round(candidate.score))}</Badge>
      </td>
      <td className="p-2 text-[12px] tabular-nums">
        {candidate.tracks_estimate === null || candidate.tracks_estimate === undefined
          ? '—'
          : `~${formatNumber(candidate.tracks_estimate)}`}
      </td>
      <td className="p-2 text-[12px] tabular-nums">
        {ratio === null || ratio === undefined ? '—' : `${formatNumber(Math.round(ratio * 100))}٪`}
      </td>
      <td className="p-2 text-[12px] tabular-nums">
        {candidate.posts_per_day ? formatNumber(Math.round(candidate.posts_per_day * 10) / 10) : '—'}
      </td>
      <td className="p-2 text-[12px] tabular-nums">{formatNumber(candidate.requested_by)}</td>
      <td className="p-2 text-[11px] text-[var(--color-muted)]">
        {candidate.probed_at ? formatDate(candidate.probed_at) : 'هنوز بررسی نشده'}
      </td>
      <td className="p-2">
        {candidate.status === 'pending' ? (
          <div className="flex gap-1">
            <Button tone="primary" onClick={onApprove} disabled={busy}>
              تأیید
            </Button>
            <Button tone="danger" onClick={onReject} disabled={busy}>
              رد
            </Button>
          </div>
        ) : (
          <span className="text-[11px] text-[var(--color-muted)]">
            {candidate.reject_reason ?? candidate.status}
          </span>
        )}
      </td>
    </tr>
  );
}

function ImportBox() {
  const [text, setText] = useState('');
  const importer = useImportChannels();
  const result = importer.data;
  return (
    <Card className="mb-4">
      <h2 className="mb-2 text-[13px] font-bold">افزودن دسته‌ای کانال</h2>
      <p className="mb-2 text-[11px] text-[var(--color-muted)]">
        هر خط یک یوزرنیم، لینک t.me یا یک ستون CSV. این‌ها مستقیم وارد صف کرال می‌شوند.
      </p>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={4}
        aria-label="لیست کانال‌ها"
        placeholder={'@music_channel\nhttps://t.me/another'}
        className="w-full rounded-lg border border-[var(--color-line)] p-2 text-[13px]"
      />
      <div className="mt-2 flex items-center gap-2">
        <Button
          tone="primary"
          disabled={!text.trim() || importer.isPending}
          onClick={() => importer.mutate(text, { onSuccess: () => setText('') })}
        >
          وارد کن
        </Button>
        {result && (
          <span className="text-[12px] text-[var(--color-muted)]">
            {formatNumber(result.created)} اضافه شد · {formatNumber(result.existing)} از قبل بود ·{' '}
            {formatNumber(result.blocked)} مسدود
            {result.invalid.length > 0 && ` · نامعتبر: ${result.invalid.join('، ')}`}
          </span>
        )}
      </div>
    </Card>
  );
}

export function Candidates() {
  const [status, setStatus] = useState('pending');
  const [selected, setSelected] = useState<number[]>([]);
  const queue = useCandidates(status);
  const review = useReviewCandidate();

  const toggle = (id: number, checked: boolean) =>
    setSelected((current) =>
      checked ? [...current, id] : current.filter((entry) => entry !== id),
    );

  const rows = queue.data?.items ?? [];

  return (
    <div>
      <ImportBox />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {STATUS_TABS.map((tab) => (
          <Button
            key={tab.value}
            tone={tab.value === status ? 'primary' : 'default'}
            onClick={() => {
              setStatus(tab.value);
              setSelected([]);
            }}
          >
            {tab.label}
          </Button>
        ))}
        <span className="text-[12px] text-[var(--color-muted)]">
          {formatNumber(queue.data?.total ?? 0)} مورد
        </span>
        {selected.length > 0 && (
          <Button
            tone="danger"
            disabled={review.isPending}
            onClick={() =>
              review.mutate(
                { approve: false, ids: selected, reason: 'bulk reject' },
                { onSuccess: () => setSelected([]) },
              )
            }
          >
            رد گروهی ({formatNumber(selected.length)})
          </Button>
        )}
      </div>

      {queue.isLoading ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <Empty text="کاندیدی نیست" />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-right text-[13px]">
            <thead className="text-[11px] text-[var(--color-muted)]">
              <tr>
                <th className="p-2" />
                <th className="p-2">کانال</th>
                <th className="p-2">امتیاز</th>
                <th className="p-2">ترک</th>
                <th className="p-2">سهم صوتی</th>
                <th className="p-2">پست/روز</th>
                <th className="p-2">درخواست</th>
                <th className="p-2">بررسی</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((candidate) => (
                <Row
                  key={candidate.id}
                  candidate={candidate}
                  selected={selected.includes(candidate.id)}
                  onSelect={(checked) => toggle(candidate.id, checked)}
                  busy={review.isPending}
                  onApprove={() => review.mutate({ approve: true, ids: [candidate.id] })}
                  onReject={() =>
                    review.mutate({ approve: false, ids: [candidate.id], reason: 'rejected' })
                  }
                />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
