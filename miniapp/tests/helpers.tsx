import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { ReactElement, ReactNode } from 'react';
import { vi } from 'vitest';

import type { Track, Channel, Me } from '@/api/client';
import { I18nProvider, type Lang } from '@/i18n';

export function makeTrack(overrides: Partial<Track> = {}): Track {
  return {
    id: 1,
    title: 'شب بارونی',
    artists: [{ id: 10, name: 'معین', role: 'primary' }],
    album: null,
    duration: 245,
    language: 'fa',
    year: null,
    has_thumb: false,
    channels_count: 2,
    playable: true,
    liked: false,
    ...overrides,
  };
}

export function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: 5,
    username: 'persianhits',
    title: 'Persian Hits',
    status: 'active',
    status_reason: null,
    progress_pct: 100,
    tracks_count: 480,
    subscribers_count: 3,
    is_featured: false,
    category_id: 1,
    source: 'mtproto',
    avatar_url: null,
    ...overrides,
  };
}

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: 7,
    tg_id: 99,
    username: 'sara',
    first_name: 'سارا',
    lang: 'fa',
    plan: 'free',
    premium_until: null,
    features: ['discover_weekly'],
    limits: { channels: 3, playlists: 5, daily_plays: 60 },
    referral_code: 'abc123',
    public_profile: true,
    ...overrides,
  };
}

/** Routes fetch() calls to canned JSON responses keyed by "METHOD /path". */
export function mockApi(routes: Record<string, unknown | ((body: unknown) => unknown)>): {
  calls: { method: string; url: string; body: unknown }[];
} {
  const calls: { method: string; url: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = (init.method ?? 'GET').toUpperCase();
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, url, body });
      const path = url.split('?')[0] ?? url;
      // Longest route first, so "/v1/playlists/7" never matches "/v1/playlists".
      const key = Object.keys(routes)
        .sort((x, y) => y.length - x.length)
        .find((candidate) => {
          const [routeMethod, routePath] = candidate.split(' ');
          if (routeMethod !== method || !routePath) return false;
          return routePath === path;
        });
      if (!key) {
        return new Response(JSON.stringify({ error: { code: 'not_found', message: url } }), { status: 404 });
      }
      const handler = routes[key];
      const payload = typeof handler === 'function' ? (handler as (b: unknown) => unknown)(body) : handler;
      if (payload instanceof Response) return payload;
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }),
  );
  return { calls };
}

export function authRoutes(me = makeMe()) {
  return {
    'POST /v1/auth/telegram': {
      access_token: 'access-1',
      access_expires_at: Math.floor(Date.now() / 1000) + 900,
      refresh_token: 'refresh-1',
      refresh_expires_at: Math.floor(Date.now() / 1000) + 86400,
      token_type: 'bearer',
      start_param: null,
      me,
    },
    'GET /v1/me': me,
  };
}

export function Providers({
  children,
  lang = 'fa' as Lang,
  route = '/',
  path,
}: {
  children: ReactNode;
  lang?: Lang;
  route?: string;
  path?: string;
}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return (
    <QueryClientProvider client={client}>
      <I18nProvider lang={lang}>
        <MemoryRouter initialEntries={[route]}>
          {path ? <Routes><Route path={path} element={children} /></Routes> : children}
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}

interface RenderAppOptions extends Omit<RenderOptions, 'wrapper'> {
  route?: string;
  path?: string;
  lang?: Lang;
  wrapper?: RenderOptions['wrapper'];
}

/** Renders one screen inside the providers, optionally at a parameterised route. */
export function renderApp(ui: ReactElement, options: RenderAppOptions = {}) {
  const { route, path, lang, wrapper, ...rest } = options;
  return render(ui, {
    wrapper:
      wrapper ??
      (({ children }) => (
        <Providers {...(lang ? { lang } : {})} {...(route ? { route } : {})} {...(path ? { path } : {})}>
          {children}
        </Providers>
      )),
    ...rest,
  });
}
