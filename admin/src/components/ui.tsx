import type { ReactNode } from 'react';

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('rounded-xl border border-[var(--color-line)] bg-white p-4', className)}>
      {children}
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <p className="text-[12px] text-[var(--color-muted)]">{label}</p>
      <p className="mt-1 text-[22px] font-bold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-[var(--color-muted)]">{hint}</p>}
    </Card>
  );
}

export function Button({
  children,
  onClick,
  tone = 'default',
  disabled,
  type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: 'default' | 'primary' | 'danger';
  disabled?: boolean;
  type?: 'button' | 'submit';
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        'rounded-lg px-3 py-1.5 text-[13px] font-medium disabled:opacity-50',
        tone === 'primary' && 'bg-[var(--color-accent)] text-white',
        tone === 'danger' && 'bg-red-600 text-white',
        tone === 'default' && 'border border-[var(--color-line)] bg-white',
      )}
    >
      {children}
    </button>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cx(
        'rounded-lg border border-[var(--color-line)] bg-white px-3 py-1.5 text-[13px] outline-none',
        props.className,
      )}
    />
  );
}

export function Badge({ children, tone }: { children: ReactNode; tone?: 'ok' | 'warn' | 'bad' }) {
  return (
    <span
      className={cx(
        'rounded-full px-2 py-0.5 text-[11px] font-medium',
        tone === 'ok' && 'bg-emerald-100 text-emerald-800',
        tone === 'warn' && 'bg-amber-100 text-amber-800',
        tone === 'bad' && 'bg-red-100 text-red-800',
        !tone && 'bg-gray-100 text-gray-700',
      )}
    >
      {children}
    </span>
  );
}

export function Spinner() {
  return (
    <span
      role="status"
      aria-label="loading"
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-line)] border-t-[var(--color-accent)]"
    />
  );
}

/** Minimal inline bar chart: a dependency-free sparkline is enough for these counts. */
export function Bars({ points, format }: { points: { day: string; value: number }[]; format?: (n: number) => string }) {
  const max = Math.max(1, ...points.map((point) => point.value));
  return (
    <div className="flex h-24 items-end gap-[3px]" role="img" aria-label="chart">
      {points.map((point) => (
        <div
          key={point.day}
          title={`${point.day}: ${format ? format(point.value) : point.value}`}
          className="flex-1 rounded-t bg-[var(--color-accent)]"
          style={{ height: `${Math.max(2, (point.value / max) * 100)}%` }}
        />
      ))}
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <p className="py-8 text-center text-[13px] text-[var(--color-muted)]">{text}</p>;
}
