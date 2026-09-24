import { formatNumber, translate, type Lang } from '@/i18n';

/** 245 → "4:05" (Persian digits in the Persian UI). */
export function duration(seconds: number, lang: Lang = 'fa'): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const digits = (n: number, pad = 2) =>
    lang === 'fa'
      ? formatNumber(n, 'fa').padStart(pad, formatNumber(0, 'fa'))
      : String(n).padStart(pad, '0');
  const head = h > 0 ? `${digits(h, 1)}:${digits(m)}` : digits(m, 1);
  return `${head}:${digits(s)}`;
}

/** Coarse "x ago" label; exact timestamps are never shown in lists. */
export function relativeTime(iso: string | null | undefined, lang: Lang, now = Date.now()): string {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const minutes = Math.floor((now - then) / 60_000);
  if (minutes < 1) return translate(lang, 'common.now');
  if (minutes < 60) return translate(lang, 'common.minutesAgo', { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return translate(lang, 'common.hoursAgo', { count: hours });
  const days = Math.floor(hours / 24);
  if (days === 1) return translate(lang, 'common.yesterday');
  return translate(lang, 'common.daysAgo', { count: days });
}

// Music never invents colour for a track it has no artwork for: the placeholder is
// a neutral tile with a glyph, and the only colour on the screen belongs to real
// covers. These are the same grey in both themes, at the two ends of a soft gradient
// so a wall of placeholders still reads as separate tiles.
const COVER_PALETTE = [
  ['rgba(120,120,128,0.22)', 'rgba(120,120,128,0.13)'],
  ['rgba(120,120,128,0.26)', 'rgba(120,120,128,0.16)'],
  ['rgba(120,120,128,0.19)', 'rgba(120,120,128,0.11)'],
] as const;

/** Stable placeholder tint for tracks without artwork. */
export function coverColors(seed: number | string): readonly [string, string] {
  const key = typeof seed === 'number' ? seed : [...seed].reduce((a, c) => a + c.charCodeAt(0), 0);
  return COVER_PALETTE[Math.abs(key) % COVER_PALETTE.length] as readonly [string, string];
}

/**
 * Who to credit under a title.
 *
 * When no artist could be parsed, the source channel is shown instead of "Unknown
 * artist" (ADR-003 phase 13): it is true, it is useful, and it is the difference
 * between a catalogue that looks abandoned and one that looks curated.
 */
export function artistNames(track: {
  artists: { name: string }[];
  channel?: { title?: string | null; username?: string | null } | null;
}): string {
  const named = track.artists.map((a) => a.name).join('، ');
  if (named) return named;
  const channel = track.channel;
  return channel?.title || (channel?.username ? `@${channel.username}` : '');
}

/** Absolute day, used for subscription expiry (never relative: "in 3 days" is ambiguous). */
export function formatDate(iso: string, lang: Lang): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString(lang === 'fa' ? 'fa-IR' : 'en-GB', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/** A year is an identifier, not a quantity: localised digits, never grouped. */
export function formatYear(year: number, lang: Lang): string {
  return year.toLocaleString(lang === 'fa' ? 'fa-IR' : 'en-US', { useGrouping: false });
}
