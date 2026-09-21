import { useState } from 'react';

import { useBroadcastStatus, useBroadcasts, useCreateBroadcast, useEstimate } from '@/api/hooks';
import { Badge, Button, Card, Empty, Input, Spinner } from '@/components/ui';
import { formatDate, formatNumber } from '@/lib/format';

interface VariantDraft {
  text: string;
  button_text: string;
  weight: number;
}

const EMPTY: VariantDraft = { text: '', button_text: '', weight: 1 };

export function Broadcasts() {
  const list = useBroadcasts();
  const create = useCreateBroadcast();
  const estimate = useEstimate();
  const setStatus = useBroadcastStatus();

  const [plan, setPlan] = useState('');
  const [lang, setLang] = useState('');
  const [inactiveDays, setInactiveDays] = useState('');
  const [variants, setVariants] = useState<VariantDraft[]>([{ ...EMPTY }]);
  const [error, setError] = useState('');

  const target: Record<string, unknown> = {};
  if (plan) target.plan = plan;
  if (lang) target.lang = lang;
  if (inactiveDays) target.inactive_days = Number(inactiveDays);

  function submit(): void {
    setError('');
    const payload = {
      target,
      variants: variants
        .filter((variant) => variant.text.trim())
        .map((variant) => ({
          text: variant.text,
          weight: variant.weight,
          ...(variant.button_text ? { button_text: variant.button_text } : {}),
        })),
    };
    if (payload.variants.length === 0) {
      setError('حداقل یک متن لازم است');
      return;
    }
    create.mutate(payload, {
      onSuccess: () => setVariants([{ ...EMPTY }]),
      onError: (err) => setError(err instanceof Error ? err.message : 'خطا'),
    });
  }

  return (
    <div className="space-y-4">
      <Card className="space-y-3">
        <h2 className="text-[14px] font-bold">پیام همگانی جدید</h2>

        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="plan"
            value={plan}
            onChange={(event) => setPlan(event.target.value)}
            className="rounded-lg border border-[var(--color-line)] bg-white px-3 py-1.5 text-[13px]"
          >
            <option value="">همهٔ پلن‌ها</option>
            <option value="free">رایگان</option>
            <option value="pro_monthly">پرو ماهانه</option>
          </select>
          <select
            aria-label="lang"
            value={lang}
            onChange={(event) => setLang(event.target.value)}
            className="rounded-lg border border-[var(--color-line)] bg-white px-3 py-1.5 text-[13px]"
          >
            <option value="">هر زبان</option>
            <option value="fa">فارسی</option>
            <option value="en">English</option>
          </select>
          <Input
            placeholder="غیرفعال از X روز"
            value={inactiveDays}
            onChange={(event) => setInactiveDays(event.target.value.replace(/\D/g, ''))}
          />
          <Button
            disabled={estimate.isPending}
            onClick={() => estimate.mutate(target)}
          >
            تخمین مخاطب
          </Button>
          {estimate.data && (
            <Badge tone="ok">{formatNumber(estimate.data.total)} گیرنده</Badge>
          )}
        </div>

        {variants.map((variant, index) => (
          <div key={index} className="space-y-2 rounded-lg border border-[var(--color-line)] p-3">
            <div className="flex items-center justify-between">
              <span className="text-[12px] text-[var(--color-muted)]">
                نسخهٔ {String.fromCharCode(65 + index)}
              </span>
              {variants.length > 1 && (
                <Button onClick={() => setVariants(variants.filter((_, i) => i !== index))}>
                  حذف
                </Button>
              )}
            </div>
            <textarea
              aria-label={`variant-${index}`}
              value={variant.text}
              onChange={(event) =>
                setVariants(
                  variants.map((item, i) =>
                    i === index ? { ...item, text: event.target.value } : item,
                  ),
                )
              }
              rows={3}
              className="w-full rounded-lg border border-[var(--color-line)] p-2 text-[13px]"
            />
            <Input
              placeholder="متن دکمه (اختیاری)"
              value={variant.button_text}
              onChange={(event) =>
                setVariants(
                  variants.map((item, i) =>
                    i === index ? { ...item, button_text: event.target.value } : item,
                  ),
                )
              }
            />
          </div>
        ))}

        <div className="flex items-center gap-2">
          {variants.length < 4 && (
            <Button onClick={() => setVariants([...variants, { ...EMPTY }])}>
              افزودن نسخهٔ A/B
            </Button>
          )}
          <Button tone="primary" disabled={create.isPending} onClick={submit}>
            ساختن پیش‌نویس
          </Button>
          {error && <span className="text-[12px] text-red-600">{error}</span>}
        </div>
      </Card>

      <Card>
        <h2 className="mb-3 text-[14px] font-bold">پیام‌ها</h2>
        {list.isLoading && <Spinner />}
        {list.data && list.data.length === 0 && <Empty text="هنوز پیامی نساخته‌ای" />}
        {list.data && list.data.length > 0 && (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>وضعیت</th>
                  <th>پیشرفت</th>
                  <th>بلاک‌شده</th>
                  <th>ساخته‌شده</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.data.map((broadcast) => {
                  const done = broadcast.sent + broadcast.failed + broadcast.blocked;
                  const percent = broadcast.total
                    ? Math.round((done / broadcast.total) * 100)
                    : 0;
                  return (
                    <tr key={broadcast.id}>
                      <td>{broadcast.id}</td>
                      <td>
                        <Badge
                          tone={
                            broadcast.status === 'completed'
                              ? 'ok'
                              : broadcast.status === 'running'
                                ? 'warn'
                                : undefined
                          }
                        >
                          {broadcast.status}
                        </Badge>
                      </td>
                      <td>
                        {percent}% ({formatNumber(broadcast.sent)}/{formatNumber(broadcast.total)})
                      </td>
                      <td>{formatNumber(broadcast.blocked)}</td>
                      <td>{formatDate(broadcast.created_at)}</td>
                      <td className="flex gap-1">
                        {broadcast.status !== 'completed' && (
                          <>
                            <Button
                              onClick={() =>
                                setStatus.mutate({ id: broadcast.id, status: 'scheduled' })
                              }
                            >
                              شروع
                            </Button>
                            <Button
                              onClick={() =>
                                setStatus.mutate({ id: broadcast.id, status: 'paused' })
                              }
                            >
                              توقف
                            </Button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
