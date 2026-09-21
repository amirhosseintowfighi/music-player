import { useState } from 'react';

import {
  useCrawlChannels,
  useCrawlerHealth,
  useParserHealth,
  useRecrawl,
  useResolverStatus,
} from '@/api/hooks';
import { Badge, Button, Card, Empty, Spinner, Stat } from '@/components/ui';
import { formatDate, formatDay, formatNumber } from '@/lib/format';

const CRAWL_TONE: Record<string, 'ok' | 'warn' | 'bad'> = {
  idle: 'ok',
  running: 'ok',
  error: 'bad',
  preview_disabled: 'warn',
};

const CRAWL_LABEL: Record<string, string> = {
  idle: 'آماده',
  running: 'در حال کرال',
  error: 'خطا',
  preview_disabled: 'پیش‌نمایش ندارد',
};

const FILTERS = [
  { value: '', label: 'همه' },
  { value: 'running', label: 'در حال کرال' },
  { value: 'error', label: 'خطا' },
  { value: 'preview_disabled', label: 'بدون پیش‌نمایش' },
];

function ParserMonitor() {
  const parser = useParserHealth();
  if (parser.isLoading) return <Spinner />;
  const data = parser.data;
  if (!data) return <Empty text="داده‌ای نیست" />;
  const pct = (value: number | null | undefined) =>
    value === null || value === undefined ? '—' : `${formatNumber(Math.round(value * 100))}٪`;

  return (
    <Card className="mb-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[13px] font-bold">سلامت پارسر</h2>
        {data.alert ? (
          <Badge tone="bad">افت ناگهانی استخراج — پارسر را بررسی کن</Badge>
        ) : (
          <Badge tone="ok">طبیعی</Badge>
        )}
      </div>
      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="نرخ استخراج امروز" value={pct(data.today_rate)} />
        <Stat label="میانگین روزهای قبل" value={pct(data.expected_rate)} />
        <Stat
          label="صفحه‌های امروز"
          value={formatNumber(data.days[0]?.pages ?? 0)}
          hint={`${formatNumber(data.days[0]?.empty_pages ?? 0)} صفحهٔ بی‌نتیجه`}
        />
      </div>
      <table className="w-full text-right text-[12px]">
        <thead className="text-[11px] text-[var(--color-muted)]">
          <tr>
            <th className="p-1">روز</th>
            <th className="p-1">صفحه</th>
            <th className="p-1">پیام</th>
            <th className="p-1">ترک</th>
            <th className="p-1">نرخ</th>
          </tr>
        </thead>
        <tbody>
          {data.days.map((day) => (
            <tr key={day.day} className="border-t border-[var(--color-line)]">
              <td className="p-1">{formatDay(day.day)}</td>
              <td className="p-1 tabular-nums">{formatNumber(day.pages)}</td>
              <td className="p-1 tabular-nums">{formatNumber(day.messages)}</td>
              <td className="p-1 tabular-nums">{formatNumber(day.audio_items)}</td>
              <td className="p-1 tabular-nums">{pct(day.rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ResolverPanel() {
  const resolver = useResolverStatus();
  const data = resolver.data;
  if (!data) return null;
  return (
    <Card className="mb-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[13px] font-bold">وضعیت resolver</h2>
        {data.breaker_open ? (
          <Badge tone="bad">
            مدار باز است — {formatNumber(data.consecutive_failures)} خطای پشت‌سرهم
          </Badge>
        ) : (
          <Badge tone="ok">سالم</Badge>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="resolve نشده" value={formatNumber(data.unresolved)} />
        <Stat label="در صف" value={formatNumber(data.pending)} />
        <Stat label="ناموفق" value={formatNumber(data.failed)} />
        <Stat label="resolve شده" value={formatNumber(data.resolved)} />
        <Stat label="اکانت سالم" value={formatNumber(data.accounts)} />
      </div>
    </Card>
  );
}

export function Crawler() {
  const [filter, setFilter] = useState('');
  const health = useCrawlerHealth();
  const channels = useCrawlChannels(filter);
  const recrawl = useRecrawl();

  return (
    <div>
      {health.data && (
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-6">
          <Stat label="کانال" value={formatNumber(health.data.channels)} />
          <Stat label="در حال کرال" value={formatNumber(health.data.running)} />
          <Stat label="کامل‌شده" value={formatNumber(health.data.completed)} />
          <Stat label="خطا" value={formatNumber(health.data.errored)} />
          <Stat label="بدون پیش‌نمایش" value={formatNumber(health.data.preview_disabled)} />
          <Stat label="نوبت‌رسیده" value={formatNumber(health.data.due)} />
        </div>
      )}

      <ParserMonitor />
      <ResolverPanel />

      <div className="mb-3 flex flex-wrap gap-2">
        {FILTERS.map((entry) => (
          <Button
            key={entry.value || 'all'}
            tone={entry.value === filter ? 'primary' : 'default'}
            onClick={() => setFilter(entry.value)}
          >
            {entry.label}
          </Button>
        ))}
      </div>

      {channels.isLoading ? (
        <Spinner />
      ) : (channels.data?.length ?? 0) === 0 ? (
        <Empty text="کانالی نیست" />
      ) : (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-right text-[13px]">
            <thead className="text-[11px] text-[var(--color-muted)]">
              <tr>
                <th className="p-2">کانال</th>
                <th className="p-2">وضعیت</th>
                <th className="p-2">پیشرفت</th>
                <th className="p-2">ترک</th>
                <th className="p-2">آخرین کرال</th>
                <th className="p-2">نوبت بعدی</th>
                <th className="p-2">خطا</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {(channels.data ?? []).map((channel) => (
                <tr key={channel.id} className="border-t border-[var(--color-line)] align-top">
                  <td className="p-2">
                    <span className="font-medium">@{channel.username ?? channel.id}</span>
                    <p className="text-[11px] text-[var(--color-muted)]">{channel.title ?? '—'}</p>
                  </td>
                  <td className="p-2">
                    <Badge tone={CRAWL_TONE[channel.crawl_status] ?? 'warn'}>
                      {CRAWL_LABEL[channel.crawl_status] ?? channel.crawl_status}
                    </Badge>
                  </td>
                  <td className="p-2 text-[12px] tabular-nums">
                    {formatNumber(channel.progress_pct)}٪
                    <p className="text-[11px] text-[var(--color-muted)]">
                      {channel.oldest_crawled_msg_id
                        ? formatNumber(channel.oldest_crawled_msg_id)
                        : '—'}{' '}
                      …{' '}
                      {channel.newest_crawled_msg_id
                        ? formatNumber(channel.newest_crawled_msg_id)
                        : '—'}
                    </p>
                  </td>
                  <td className="p-2 tabular-nums">{formatNumber(channel.tracks_count)}</td>
                  <td className="p-2 text-[11px]">{formatDate(channel.last_crawl_at)}</td>
                  <td className="p-2 text-[11px]">{formatDate(channel.next_crawl_at)}</td>
                  <td className="p-2 text-[11px] text-red-600">
                    {channel.crawl_error ?? ''}
                    {channel.fail_count > 0 && ` (${formatNumber(channel.fail_count)})`}
                  </td>
                  <td className="p-2">
                    <div className="flex gap-1">
                      <Button
                        disabled={recrawl.isPending}
                        onClick={() => recrawl.mutate({ id: channel.id, full: false })}
                      >
                        کرال مجدد
                      </Button>
                      <Button
                        tone="danger"
                        disabled={recrawl.isPending}
                        onClick={() => recrawl.mutate({ id: channel.id, full: true })}
                      >
                        از ابتدا
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
