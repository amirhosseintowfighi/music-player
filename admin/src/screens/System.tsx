import { useState } from 'react';

import type { AdminMe } from '@/api/client';
import {
  useAudit,
  useHealth,
  usePatchPlan,
  usePatchProvider,
  usePlanLimits,
  useReports,
  useResolveReport,
  useSetFlag,
  useSetSetting,
  useSetting,
} from '@/api/hooks';
import { Badge, Button, Card, Empty, Input, Spinner, Stat } from '@/components/ui';
import { formatDate } from '@/lib/format';

export function Health() {
  const health = useHealth();
  if (health.isLoading) return <Spinner />;
  const data = health.data;
  if (!data) return <Empty text="داده‌ای نیست" />;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat
        label="اکانت ایندکسر"
        value={`${data.indexer_accounts.healthy}/${data.indexer_accounts.total}`}
      />
      <Stat label="نود edge" value={`${data.edges.healthy}/${data.edges.total}`} />
      <Stat
        label="کانال"
        value={`${data.channels.indexing} در حال ایندکس`}
        hint={`${data.channels.failed} ناموفق`}
      />
      <Stat
        label="صف بررسی"
        value={String(data.queues.payments_to_review)}
        hint={`${data.queues.open_reports} گزارش باز`}
      />
      <Stat label="FloodWait ۲۴ ساعت" value={`${data.floodwait_24h_s}s`} />
    </div>
  );
}

export function Reports() {
  const [status, setStatus] = useState('open');
  const reports = useReports(status);
  const resolve = useResolveReport();
  const [note, setNote] = useState('');

  return (
    <Card className="space-y-3">
      <div className="flex items-center gap-2">
        {['open', 'in_review', 'actioned', 'dismissed'].map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setStatus(option)}
            className={
              option === status
                ? 'rounded-full bg-[var(--color-accent)] px-3 py-1 text-[12px] text-white'
                : 'rounded-full border border-[var(--color-line)] px-3 py-1 text-[12px]'
            }
          >
            {option}
          </button>
        ))}
      </div>
      {reports.isLoading && <Spinner />}
      {reports.data && reports.data.length === 0 && <Empty text="گزارشی نیست" />}
      {reports.data?.map((report) => (
        <div key={report.id} className="rounded-lg border border-[var(--color-line)] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-[13px] font-bold">
                {report.entity_type} #{report.entity_id} · {report.reason}
              </p>
              <p className="text-[12px] text-[var(--color-muted)]">{report.details}</p>
              <p className="text-[11px] text-amber-700">مهلت: {formatDate(report.due_at)}</p>
            </div>
            {status === 'open' && (
              <div className="flex items-center gap-2">
                <Input
                  placeholder="توضیح"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
                <Button
                  tone="danger"
                  onClick={() =>
                    resolve.mutate({
                      id: report.id,
                      status: 'actioned',
                      resolution: note,
                      hide_entity: true,
                    })
                  }
                >
                  حذف محتوا
                </Button>
                <Button
                  onClick={() =>
                    resolve.mutate({
                      id: report.id,
                      status: 'dismissed',
                      resolution: note,
                      hide_entity: false,
                    })
                  }
                >
                  رد گزارش
                </Button>
              </div>
            )}
          </div>
        </div>
      ))}
    </Card>
  );
}

export function Settings({ me }: { me: AdminMe | null }) {
  const setFlag = useSetFlag();
  const setSetting = useSetSetting();
  const patchPlan = usePatchPlan();
  const patchProvider = usePatchProvider();
  const [price, setPrice] = useState('');
  const [trialDays, setTrialDays] = useState('');
  const [done, setDone] = useState('');

  const editable = Boolean(
    me && (me.permissions.includes('*') || me.permissions.includes('system.edit')),
  );
  if (!editable) return <Empty text="اجازهٔ ویرایش تنظیمات را نداری" />;

  return (
    <div className="space-y-3">
      <Card className="space-y-2">
        <h2 className="text-[14px] font-bold">قیمت پلن پرو ماهانه (تومان)</h2>
        <div className="flex items-center gap-2">
          <Input
            aria-label="price"
            value={price}
            onChange={(event) => setPrice(event.target.value.replace(/\D/g, ''))}
          />
          <Button
            tone="primary"
            disabled={!price || patchPlan.isPending}
            onClick={() =>
              patchPlan.mutate(
                { code: 'pro_monthly', changes: { prices: { IRR: Number(price) * 10 } } },
                { onSuccess: () => setDone('قیمت ذخیره شد') },
              )
            }
          >
            ذخیره
          </Button>
        </div>
      </Card>

      <Card className="space-y-2">
        <h2 className="text-[14px] font-bold">روزهای دورهٔ آزمایشی</h2>
        <div className="flex items-center gap-2">
          <Input
            aria-label="trial"
            value={trialDays}
            onChange={(event) => setTrialDays(event.target.value.replace(/\D/g, ''))}
          />
          <Button
            tone="primary"
            disabled={!trialDays}
            onClick={() =>
              setSetting.mutate(
                { key: 'trial_days', value: Number(trialDays) },
                { onSuccess: () => setDone('ذخیره شد') },
              )
            }
          >
            ذخیره
          </Button>
        </div>
      </Card>

      <Card className="space-y-2">
        <h2 className="text-[14px] font-bold">درگاه‌های پرداخت</h2>
        <p className="text-[12px] text-[var(--color-muted)]">
          کلید درگاه فقط در env سرور است و از این‌جا قابل تغییر نیست.
        </p>
        <div className="flex flex-wrap gap-2">
          {['stars', 'zarinpal', 'idpay', 'nextpay', 'card2card'].map((code) => (
            <span key={code} className="flex items-center gap-1">
              <Button
                onClick={() =>
                  patchProvider.mutate(
                    { code, changes: { is_enabled: true } },
                    { onSuccess: () => setDone(`${code} فعال شد`) },
                  )
                }
              >
                فعال {code}
              </Button>
              <Button
                onClick={() =>
                  patchProvider.mutate(
                    { code, changes: { is_enabled: false } },
                    { onSuccess: () => setDone(`${code} غیرفعال شد`) },
                  )
                }
              >
                خاموش
              </Button>
            </span>
          ))}
        </div>
      </Card>

      <LimitsCard onDone={setDone} />
      <RequiredChannelsCard onDone={setDone} />

      <Card className="space-y-2">
        <h2 className="text-[14px] font-bold">فلگ‌ها</h2>
        <div className="flex flex-wrap gap-2">
          {[
            'maintenance_mode',
            'ai_search',
            'crawler_enabled',
            'mtproto_fallback',
            'telegram_search',
          ].map((key) => (
            <span key={key} className="flex items-center gap-1">
              <Button
                onClick={() =>
                  setFlag.mutate({ key, value: true }, { onSuccess: () => setDone(`${key} روشن`) })
                }
              >
                {key} روشن
              </Button>
              <Button
                onClick={() =>
                  setFlag.mutate({ key, value: false }, { onSuccess: () => setDone(`${key} خاموش`) })
                }
              >
                خاموش
              </Button>
            </span>
          ))}
        </div>
      </Card>

      {done && <Badge tone="ok">{done}</Badge>}
    </div>
  );
}

export function Audit() {
  const [action, setAction] = useState('');
  const audit = useAudit(action ? { action } : {});
  return (
    <Card>
      <div className="mb-3 flex items-center gap-2">
        <Input
          placeholder="فیلتر بر اساس action"
          value={action}
          onChange={(event) => setAction(event.target.value)}
        />
      </div>
      {audit.isLoading && <Spinner />}
      {audit.data && audit.data.length === 0 && <Empty text="رویدادی نیست" />}
      {audit.data && audit.data.length > 0 && (
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>زمان</th>
                <th>کنشگر</th>
                <th>عمل</th>
                <th>موجودیت</th>
                <th>جزئیات</th>
              </tr>
            </thead>
            <tbody>
              {audit.data.map((entry) => (
                <tr key={entry.id}>
                  <td>{formatDate(entry.created_at)}</td>
                  <td>
                    {entry.actor_type}
                    {entry.actor_id ? ` #${entry.actor_id}` : ''}
                  </td>
                  <td>{entry.action}</td>
                  <td>
                    {entry.entity} {entry.entity_id}
                  </td>
                  <td className="font-mono text-[11px]">{JSON.stringify(entry.payload)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}


/** The free plan's ceilings. Premium is unlimited and has nothing to edit. */
const LIMIT_LABELS: [string, string][] = [
  ['daily_plays', 'پخش در روز'],
  ['playlists', 'پلی‌لیست'],
  ['channels', 'کانال'],
  ['library', 'ترک ذخیره‌شده'],
];

function LimitsCard({ onDone }: { onDone: (text: string) => void }) {
  const plans = usePlanLimits();
  const patchPlan = usePatchPlan();
  const [draft, setDraft] = useState<Record<string, string>>({});

  const free = plans.data?.find((plan) => plan.code === 'free');
  const value = (key: string) =>
    draft[key] ?? String(free?.limits[key] ?? '');

  return (
    <Card className="space-y-3">
      <h2 className="text-[14px] font-bold">محدودیت‌های پلن رایگان</h2>
      <p className="text-[12px] text-[var(--color-muted)]">
        ۱- یعنی بدون محدودیت. پلن‌های پرمیوم هیچ سقفی ندارند.
      </p>
      {!free && <Spinner />}
      {free && (
        <div className="space-y-2">
          {LIMIT_LABELS.map(([key, label]) => (
            <div key={key} className="flex items-center gap-2">
              <span className="w-32 text-[13px]">{label}</span>
              <Input
                aria-label={key}
                value={value(key)}
                onChange={(event) =>
                  setDraft((d) => ({ ...d, [key]: event.target.value.replace(/[^\d-]/g, '') }))
                }
              />
            </div>
          ))}
          <div className="flex items-center gap-2">
            <span className="w-32 text-[13px]">دانلود آفلاین</span>
            <Badge tone={free.limits.download ? 'ok' : 'warn'}>
              {free.limits.download ? 'مجاز' : 'فقط پرمیوم'}
            </Badge>
            <Button
              onClick={() =>
                patchPlan.mutate(
                  {
                    code: 'free',
                    changes: { limits: { ...free.limits, download: !free.limits.download } },
                  },
                  {
                    onSuccess: () => {
                      onDone('ذخیره شد');
                      void plans.refetch();
                    },
                  },
                )
              }
            >
              تغییر
            </Button>
          </div>
          <Button
            tone="primary"
            disabled={patchPlan.isPending}
            onClick={() => {
              const limits: Record<string, number | boolean> = { ...free.limits };
              for (const [key] of LIMIT_LABELS) {
                const raw = draft[key];
                if (raw !== undefined && raw !== '') limits[key] = Number(raw);
              }
              patchPlan.mutate(
                { code: 'free', changes: { limits } },
                {
                  onSuccess: () => {
                    onDone('محدودیت‌ها ذخیره شد');
                    setDraft({});
                    void plans.refetch();
                  },
                },
              );
            }}
          >
            ذخیره
          </Button>
        </div>
      )}
    </Card>
  );
}

/** Channels every listener must join before the Mini App opens. */
function RequiredChannelsCard({ onDone }: { onDone: (text: string) => void }) {
  const setting = useSetting('required_channels');
  const setSetting = useSetSetting();
  const [draft, setDraft] = useState<string | null>(null);

  const current = Array.isArray(setting.data?.value) ? (setting.data.value as string[]) : [];
  const text = draft ?? current.join('\n');

  return (
    <Card className="space-y-2">
      <h2 className="text-[14px] font-bold">کانال‌های جوین اجباری</h2>
      <p className="text-[12px] text-[var(--color-muted)]">
        هر خط یک یوزرنیم، بدون @. ربات باید در هر کدام ادمین باشد، وگرنه تلگرام جواب
        نمی‌دهد و آن کانال نادیده گرفته می‌شود (کسی پشت در نمی‌ماند). خالی یعنی بدون جوین اجباری.
      </p>
      <textarea
        aria-label="required-channels"
        rows={4}
        value={text}
        onChange={(event) => setDraft(event.target.value)}
        className="w-full rounded-lg border border-[var(--color-line)] bg-transparent p-2 font-mono text-[12px]"
        dir="ltr"
      />
      <Button
        tone="primary"
        disabled={setSetting.isPending}
        onClick={() =>
          setSetting.mutate(
            {
              key: 'required_channels',
              value: text
                .split(/[\n,]/)
                .map((name) => name.trim().replace(/^@/, ''))
                .filter(Boolean),
            },
            {
              onSuccess: () => {
                onDone('کانال‌ها ذخیره شد');
                setDraft(null);
                void setting.refetch();
              },
            },
          )
        }
      >
        ذخیره
      </Button>
    </Card>
  );
}
