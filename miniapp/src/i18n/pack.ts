/**
 * String packs: a feature's words, shipped with the feature.
 *
 * The main dictionaries (fa.ts / en.ts) are part of the first paint, which has a
 * 200 KB budget. A screen that is loaded lazily brings its own pack instead, so its
 * strings cost nothing until someone opens it.
 */
import { useCallback } from 'react';

import { fill, useI18n, type Lang } from './index';

export type Pack<K extends string> = Record<Lang, Record<K, string>>;

/** Both languages must have every key: the English one is type-checked against the Persian. */
export function definePack<K extends string>(fa: Record<K, string>, en: Record<NoInfer<K>, string>): Pack<K> {
  return { fa, en };
}

export function usePack<K extends string>(pack: Pack<K>) {
  const { lang } = useI18n();
  return useCallback(
    (key: K, vars?: Record<string, string | number>) => fill(pack[lang][key] ?? pack.en[key] ?? key, lang, vars),
    [lang, pack],
  );
}
