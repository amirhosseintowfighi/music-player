import { useState } from 'react';

import { usePendingPayments, useReviewPayment } from '@/api/hooks';
import { Button, Card, Empty, Input, Spinner } from '@/components/ui';
import { formatDate, formatMoney } from '@/lib/format';

export function Payments() {
  const pending = usePendingPayments();
  const review = useReviewPayment();
  const [reason, setReason] = useState('');

  if (pending.isLoading) return <Spinner />;
  const rows = pending.data ?? [];
  if (rows.length === 0) return <Empty text="پرداختی در انتظار بررسی نیست" />;

  return (
    <div className="space-y-3">
      {rows.map((payment) => (
        <Card key={payment.id} className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-[14px] font-bold">
                {formatMoney(payment.amount, payment.currency)} · {payment.plan_code}
              </p>
              <p className="text-[12px] text-[var(--color-muted)]">
                {payment.first_name}
                {payment.username ? ` @${payment.username}` : ''} · tg {payment.tg_id} ·{' '}
                {formatDate(payment.created_at)}
              </p>
              {payment.review_due_at && (
                <p className="text-[11px] text-amber-700">
                  مهلت بررسی: {formatDate(payment.review_due_at)}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Input
                placeholder="دلیل رد"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
              <Button
                tone="primary"
                disabled={review.isPending}
                onClick={() => review.mutate({ id: payment.id, approve: true })}
              >
                تأیید
              </Button>
              <Button
                tone="danger"
                disabled={review.isPending}
                onClick={() => review.mutate({ id: payment.id, approve: false, reason })}
              >
                رد
              </Button>
            </div>
          </div>
          {payment.receipt_file_id ? (
            <p className="rounded-lg bg-gray-50 p-2 font-mono text-[11px]">
              رسید: {payment.receipt_file_id}
            </p>
          ) : (
            <p className="text-[12px] text-[var(--color-muted)]">هنوز رسیدی نفرستاده</p>
          )}
        </Card>
      ))}
    </div>
  );
}
