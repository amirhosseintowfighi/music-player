import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window !== 'undefined' ? window.matchMedia(query).matches : false));
  useEffect(() => {
    const m = window.matchMedia(query);
    const onChange = () => setMatches(m.matches);
    m.addEventListener('change', onChange);
    setMatches(m.matches);
    return () => m.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

export function useIsTv(): boolean {
  return useMediaQuery('(min-width: 1600px) and (pointer: coarse)');
}
