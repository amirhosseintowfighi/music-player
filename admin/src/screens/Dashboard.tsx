import { useState } from "react";

import { useMetric, useOverview, useRetention } from "@/api/hooks";
import { Bars, Card, Empty, Spinner, Stat } from "@/components/ui";
import { formatMoney, formatNumber } from "@/lib/format";

const METRICS = [
  { id: "users", label: "کاربران جدید" },
  { id: "plays", label: "پخش" },
  { id: "revenue", label: "درآمد" },
] as const;

export function Dashboard() {
  const overview = useOverview();
  const [metric, setMetric] = useState<(typeof METRICS)[number]["id"]>("users");
  const series = useMetric(metric, 30);
  const retention = useRetention();

  if (overview.isLoading) return <Spinner />;
  const data = overview.data;
  if (!data) return <Empty text="داده‌ای نیست" />;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="DAU"
          value={formatNumber(data.dau)}
          hint={`WAU ${formatNumber(data.wau)}`}
        />
        <Stat
          label="MAU"
          value={formatNumber(data.mau)}
          hint={`امروز +${data.new_users_today}`}
        />
        <Stat
          label="مشترک فعال"
          value={formatNumber(data.paying_users)}
          hint={`${data.trials} آزمایشی`}
        />
        <Stat
          label="تبدیل"
          value={`${data.conversion_pct}%`}
          hint={`ریزش ${data.churn_30d_pct}%`}
        />
        <Stat label="ترک" value={formatNumber(data.tracks)} />
        <Stat label="کانال فعال" value={formatNumber(data.channels)} />
        <Stat label="پخش امروز" value={formatNumber(data.plays_today)} />
        <Stat
          label="درآمد ۳۰ روز"
          value={
            data.revenue_30d.length
              ? data.revenue_30d
                  .map((row) => formatMoney(row.amount, row.currency))
                  .join(" · ")
              : "—"
          }
          hint={data.revenue_30d.map((row) => row.provider).join(" · ")}
        />
      </div>

      <Card>
        <div className="mb-3 flex items-center gap-2">
          {METRICS.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setMetric(option.id)}
              className={
                option.id === metric
                  ? "rounded-full bg-[var(--color-accent)] px-3 py-1 text-[12px] text-white"
                  : "rounded-full border border-[var(--color-line)] px-3 py-1 text-[12px]"
              }
            >
              {option.label}
            </button>
          ))}
        </div>
        {series.isLoading ? <Spinner /> : <Bars points={series.data ?? []} />}
      </Card>

      <Card>
        <h2 className="mb-3 text-[14px] font-bold">ماندگاری هفتگی</h2>
        {retention.isLoading && <Spinner />}
        {retention.data && retention.data.length > 0 ? (
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>هفته</th>
                  <th>اندازه</th>
                  {[0, 1, 2, 3, 4, 5].map((week) => (
                    <th key={week}>W{week}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {retention.data.map((cohort) => (
                  <tr key={cohort.cohort}>
                    <td>{cohort.cohort}</td>
                    <td>{cohort.size}</td>
                    {[0, 1, 2, 3, 4, 5].map((week) => {
                      const retained = cohort.weeks[String(week)] ?? 0;
                      const percent = cohort.size
                        ? Math.round((retained / cohort.size) * 100)
                        : 0;
                      return (
                        <td key={week}>{retained ? `${percent}%` : "—"}</td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !retention.isLoading && <Empty text="هنوز کوهورتی نیست" />
        )}
      </Card>
    </div>
  );
}
