/**
 * What the Mini App reported from listeners' devices: each start, each suspected
 * web-view crash (a start that never got to "ready"), and uncaught script errors.
 *
 * The first thing to read when someone says "it crashed": the platform, the Telegram
 * version and the user agent say which engine it was, and the stage says how far the
 * app got before it died.
 */
import { useState } from 'react';

import type { ClientEvent } from '@/api/client';
import { useClientLog } from '@/api/hooks';
import { Badge, Card, Empty, Spinner, Stat } from '@/components/ui';
import { formatDate } from '@/lib/format';

const KINDS = [
  ['', 'همه'],
  ['crash', 'کرش'],
  ['error', 'خطا'],
  ['boot', 'شروع'],
] as const;

const STAGES: Record<string, string> = {
  html: 'قبل از اجرای کد برنامه',
  boot: 'قبل از رندر',
  render: 'حین رندر اول',
  shell: 'بعد از نمایش صفحه',
  ready: 'سالم',
};

/** WebKit on Linux that is not Chromium: Telegram Desktop's WebKitGTK. */
function engine(ua: string): string {
  if (/Linux/.test(ua) && /AppleWebKit/.test(ua) && !/Chrome|Chromium|Android/.test(ua)) return 'WebKitGTK';
  if (/Edg\//.test(ua)) return 'WebView2';
  if (/Android/.test(ua)) return 'Android WebView';
  if (/iPhone|iPad|Macintosh/.test(ua) && !/Chrome/.test(ua)) return 'WKWebView';
  if (/Chrome/.test(ua)) return 'Chromium';
  return '—';
}

function Row({ event }: { event: ClientEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="cursor-pointer" onClick={() => setOpen((value) => !value)}>
        <td>{formatDate(new Date(event.at * 1000).toISOString())}</td>
        <td>
          <Badge tone={event.kind === 'crash' ? 'bad' : event.kind === 'error' ? 'warn' : 'ok'}>{event.kind}</Badge>
        </td>
        <td>
          {event.platform}
          {event.tg_version ? ` ${event.tg_version}` : ''}
        </td>
        <td>{engine(event.ua)}</td>
        <td>{event.kind === 'crash' ? (STAGES[event.stage] ?? event.stage) : event.stage}</td>
        <td>{event.user_id ?? '—'}</td>
        <td className="max-w-[360px] truncate">{event.message}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="font-mono text-[11px] whitespace-pre-wrap">
            {`session ${event.session}  path ${event.path || '/'}\n${event.ua}\n${event.stack}`}
          </td>
        </tr>
      )}
    </>
  );
}

export function ClientLog() {
  const [kind, setKind] = useState('');
  const [platform, setPlatform] = useState('');
  const log = useClientLog({ ...(kind ? { kind } : {}), ...(platform ? { platform } : {}) });
  const summary = Object.entries(log.data?.summary ?? {});

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {summary.map(([name, counts]) => (
          <Stat
            key={name}
            label={name}
            value={`${counts.crash ?? 0} کرش از ${counts.boot ?? 0} شروع`}
            hint={`${counts.error ?? 0} خطا · ۲۴ ساعت گذشته`}
          />
        ))}
      </div>
      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {KINDS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setKind(value)}
              className={`rounded-lg px-3 py-1.5 text-[12px] ${kind === value ? 'bg-[var(--color-ink)] text-white' : 'border border-[var(--color-line)]'}`}
            >
              {label}
            </button>
          ))}
          <select
            value={platform}
            onChange={(event) => setPlatform(event.target.value)}
            className="rounded-lg border border-[var(--color-line)] px-2 py-1.5 text-[12px]"
          >
            <option value="">همهٔ پلتفرم‌ها</option>
            {['tdesktop', 'android', 'ios', 'macos', 'weba', 'webk', 'unknown'].map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        {log.isLoading && <Spinner />}
        {log.data && log.data.items.length === 0 && <Empty text="چیزی گزارش نشده" />}
        {log.data && log.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>زمان</th>
                  <th>نوع</th>
                  <th>پلتفرم</th>
                  <th>موتور</th>
                  <th>مرحله</th>
                  <th>کاربر</th>
                  <th>پیام</th>
                </tr>
              </thead>
              <tbody>
                {log.data.items.map((event, index) => (
                  <Row key={`${event.session}-${event.at}-${index}`} event={event} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
