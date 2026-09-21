import { useState } from 'react';

import type { AdminMe } from '@/api/client';
import { useBanUser, useGift, useImpersonate, useUser, useUsers } from '@/api/hooks';
import { Badge, Button, Card, Empty, Input, Spinner } from '@/components/ui';
import { formatDate, formatDay, formatNumber } from '@/lib/format';

function can(me: AdminMe | null, permission: string): boolean {
  return Boolean(me && (me.permissions.includes('*') || me.permissions.includes(permission)));
}

function UserPanel({ id, me, onClose }: { id: number; me: AdminMe | null; onClose: () => void }) {
  const detail = useUser(id);
  const ban = useBanUser(id);
  const gift = useGift(id);
  const impersonate = useImpersonate(id);
  const [reason, setReason] = useState('');
  const [token, setToken] = useState<string | null>(null);

  if (detail.isLoading) return <Spinner />;
  const data = detail.data;
  if (!data) return <Empty text="کاربر پیدا نشد" />;

  return (
    <Card className="space-y-3">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-[15px] font-bold">
            {data.user.first_name} {data.user.username ? `@${data.user.username}` : ''}
          </h2>
          <p className="text-[12px] text-[var(--color-muted)]">
            id {data.user.id} · tg {data.user.tg_id} · از {formatDay(data.user.created_at)}
          </p>
        </div>
        <Button onClick={onClose}>بستن</Button>
      </div>

      <div className="flex flex-wrap gap-2">
        <Badge tone={data.subscription_status === 'free' ? undefined : 'ok'}>
          {data.user.plan_code} · {data.subscription_status}
        </Badge>
        {data.user.is_banned && <Badge tone="bad">مسدود</Badge>}
        {data.user.bot_blocked && <Badge tone="warn">ربات را بلاک کرده</Badge>}
        {data.subscription_expires_at && (
          <Badge>تا {formatDay(data.subscription_expires_at)}</Badge>
        )}
      </div>

      <dl className="grid grid-cols-3 gap-2 text-[12px] md:grid-cols-5">
        {Object.entries(data.stats).map(([key, value]) => (
          <div key={key} className="rounded-lg bg-gray-50 p-2">
            <dt className="text-[var(--color-muted)]">{key}</dt>
            <dd className="text-[15px] font-bold tabular-nums">{formatNumber(value)}</dd>
          </div>
        ))}
      </dl>

      {can(me, 'users.edit') && (
        <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-line)] pt-3">
          <Input
            placeholder="دلیل"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button
            tone={data.user.is_banned ? 'default' : 'danger'}
            disabled={ban.isPending}
            onClick={() => ban.mutate({ banned: !data.user.is_banned, reason })}
          >
            {data.user.is_banned ? 'رفع مسدودی' : 'مسدود کردن'}
          </Button>
          <Button disabled={gift.isPending} onClick={() => gift.mutate(30)}>
            هدیهٔ ۳۰ روزه
          </Button>
        </div>
      )}

      {can(me, 'users.impersonate') && (
        <div className="border-t border-[var(--color-line)] pt-3">
          <Button
            disabled={impersonate.isPending}
            onClick={() =>
              impersonate.mutate(undefined, { onSuccess: (data) => setToken(data.access_token) })
            }
          >
            ورود به‌جای کاربر (فقط خواندن)
          </Button>
          {token && (
            <p className="mt-2 break-all rounded-lg bg-gray-50 p-2 font-mono text-[11px]">
              {token}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

export function Users({ me }: { me: AdminMe | null }) {
  const [query, setQuery] = useState('');
  const [plan, setPlan] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const users = useUsers(query, plan, offset);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="جستجو: آیدی، یوزرنیم یا نام"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
          />
          <select
            value={plan}
            onChange={(event) => setPlan(event.target.value)}
            className="rounded-lg border border-[var(--color-line)] bg-white px-3 py-1.5 text-[13px]"
          >
            <option value="">همهٔ پلن‌ها</option>
            <option value="free">رایگان</option>
            <option value="pro_monthly">پرو ماهانه</option>
            <option value="pro_yearly">پرو سالانه</option>
          </select>
          <a
            href="/admin/users.csv"
            className="rounded-lg border border-[var(--color-line)] px-3 py-1.5 text-[13px]"
          >
            خروجی CSV
          </a>
          <span className="text-[12px] text-[var(--color-muted)]">
            {users.data ? `${formatNumber(users.data.total)} کاربر` : ''}
          </span>
        </div>
      </Card>

      {selected !== null && (
        <UserPanel id={selected} me={me} onClose={() => setSelected(null)} />
      )}

      <Card>
        {users.isLoading && <Spinner />}
        {users.data && users.data.items.length === 0 && <Empty text="کاربری پیدا نشد" />}
        {users.data && users.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>کاربر</th>
                  <th>پلن</th>
                  <th>زبان</th>
                  <th>آخرین بازدید</th>
                  <th>وضعیت</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.data.items.map((user) => (
                  <tr key={user.id}>
                    <td>
                      {user.first_name}
                      {user.username && (
                        <span className="text-[var(--color-muted)]"> @{user.username}</span>
                      )}
                    </td>
                    <td>{user.plan_code}</td>
                    <td>{user.lang}</td>
                    <td>{formatDate(user.last_seen_at)}</td>
                    <td>{user.is_banned ? <Badge tone="bad">مسدود</Badge> : <Badge tone="ok">فعال</Badge>}</td>
                    <td>
                      <Button onClick={() => setSelected(user.id)}>جزئیات</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-3 flex items-center gap-2">
          <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>
            قبلی
          </Button>
          <Button
            disabled={!users.data || offset + 25 >= users.data.total}
            onClick={() => setOffset(offset + 25)}
          >
            بعدی
          </Button>
        </div>
      </Card>
    </div>
  );
}
