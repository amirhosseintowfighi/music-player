/** Persian-friendly formatting for the panel. */

export function formatNumber(value: number): string {
  return value.toLocaleString('fa-IR');
}

/** Rial is stored; toman is what people read. Stars stay as stars. */
export function formatMoney(amount: number, currency: string): string {
  if (currency === 'XTR') return `${amount.toLocaleString('fa-IR')} ⭐`;
  return `${Math.round(amount / 10).toLocaleString('fa-IR')} تومان`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('fa-IR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDay(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('fa-IR');
}
