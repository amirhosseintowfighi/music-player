import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react';

import { fa } from './fa';
import { en } from './en';

export type Lang = 'fa' | 'en';
export type Dict = Record<keyof typeof fa, string>;
export type Key = keyof typeof fa;

const DICTS: Record<Lang, Dict> = { fa, en };

interface Ctx {
  lang: Lang;
  dir: 'rtl' | 'ltr';
  t: (key: Key, vars?: Record<string, string | number>) => string;
  n: (value: number) => string;
}

const I18nContext = createContext<Ctx | null>(null);

/** Persian digits when the UI is Persian; grouped Latin digits otherwise. */
export function formatNumber(value: number, lang: Lang): string {
  return new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US').format(value);
}

export function translate(lang: Lang, key: Key, vars?: Record<string, string | number>): string {
  const template = DICTS[lang][key] ?? DICTS.en[key] ?? String(key);
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = vars[name];
    return value === undefined ? match : typeof value === 'number' ? formatNumber(value, lang) : value;
  });
}

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const t = useCallback((key: Key, vars?: Record<string, string | number>) => translate(lang, key, vars), [lang]);
  const n = useCallback((value: number) => formatNumber(value, lang), [lang]);
  const value = useMemo<Ctx>(() => ({ lang, dir: lang === 'fa' ? 'rtl' : 'ltr', t, n }), [lang, t, n]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n outside I18nProvider');
  return ctx;
}
