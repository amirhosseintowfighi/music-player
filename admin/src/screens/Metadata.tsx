import { useState } from 'react';

import type { MetadataRow } from '@/api/client';
import { useFixMetadata, useMetadataQueue } from '@/api/hooks';
import { Badge, Button, Card, Empty, Input, Spinner } from '@/components/ui';
import { formatNumber } from '@/lib/format';

const SOURCES = [
  { value: 'all', label: 'همه' },
  { value: 'reported', label: 'گزارش کاربران' },
  { value: 'unsure', label: 'پارسر مطمئن نبود' },
];

/**
 * One row, with the correction inline.
 *
 * The bulk switch is the reason this page is worth building: a channel posts the same
 * mangled artist name hundreds of times, so fixing one row can fix all of them.
 */
function Row({ row }: { row: MetadataRow }) {
  const fix = useFixMetadata();
  const [title, setTitle] = useState(row.title);
  const [artist, setArtist] = useState(row.artists);
  const [bulk, setBulk] = useState(false);
  const [done, setDone] = useState(0);

  const changed = title !== row.title || artist !== row.artists;

  return (
    <tr className="border-t border-[var(--color-line)] align-top">
      <td className="p-2">
        <Input
          value={title}
          aria-label={`عنوان ${row.id}`}
          onChange={(event) => setTitle(event.target.value)}
        />
        <p className="mt-1 text-[11px] text-[var(--color-muted)]">
          {row.channel_title ?? '—'}
          {row.file_name ? ` · ${row.file_name}` : ''}
        </p>
      </td>
      <td className="p-2">
        <Input
          value={artist}
          aria-label={`خواننده ${row.id}`}
          placeholder="بدون خواننده"
          onChange={(event) => setArtist(event.target.value)}
        />
        <label className="mt-1 flex items-center gap-1 text-[11px] text-[var(--color-muted)]">
          <input type="checkbox" checked={bulk} onChange={(e) => setBulk(e.target.checked)} />
          روی همهٔ ترک‌های این خواننده اعمال شود
        </label>
      </td>
      <td className="p-2">
        <Badge tone={row.metadata_confidence < 40 ? 'bad' : 'warn'}>
          {formatNumber(row.metadata_confidence)}
        </Badge>
        {row.reports > 0 && (
          <p className="mt-1 text-[11px] text-red-600">
            {formatNumber(row.reports)} گزارش
          </p>
        )}
      </td>
      <td className="p-2">
        <Button
          tone="primary"
          disabled={!changed || fix.isPending}
          onClick={() =>
            fix.mutate(
              {
                id: row.id,
                title: title !== row.title ? title : undefined,
                artist: artist !== row.artists ? artist : undefined,
                apply_to_artist: bulk,
              },
              { onSuccess: (result) => setDone(result.tracks) },
            )
          }
        >
          ذخیره
        </Button>
        {done > 0 && (
          <p className="mt-1 text-[11px] text-[var(--color-muted)]">
            {formatNumber(done)} ترک اصلاح شد
          </p>
        )}
      </td>
    </tr>
  );
}

export function Metadata() {
  const [source, setSource] = useState('all');
  const queue = useMetadataQueue(source);
  const rows = queue.data ?? [];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {SOURCES.map((entry) => (
          <Button
            key={entry.value}
            tone={entry.value === source ? 'primary' : 'default'}
            onClick={() => setSource(entry.value)}
          >
            {entry.label}
          </Button>
        ))}
        <span className="text-[12px] text-[var(--color-muted)]">
          {formatNumber(rows.length)} ترک در صف
        </span>
      </div>

      {queue.isLoading ? (
        <Spinner />
      ) : rows.length === 0 ? (
        <Empty text="چیزی برای بررسی نیست" />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-right text-[13px]">
            <thead className="text-[11px] text-[var(--color-muted)]">
              <tr>
                <th className="p-2">عنوان</th>
                <th className="p-2">خواننده</th>
                <th className="p-2">اطمینان</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Row key={row.id} row={row} />
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
