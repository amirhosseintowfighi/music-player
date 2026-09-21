import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement, ReactNode } from 'react';
import { vi } from 'vitest';

import type { AdminMe } from '@/api/client';

export function makeMe(overrides: Partial<AdminMe> = {}): AdminMe {
  return {
    id: 1,
    tg_id: 5001,
    role: 'owner',
    permissions: ['*'],
    first_name: 'Owner',
    is_active: true,
    ...overrides,
  };
}

/** Routes fetch() to canned responses keyed by "METHOD /path" (longest match wins). */
export function mockApi(routes: Record<string, unknown>): {
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
      const key = Object.keys(routes)
        .sort((a, b) => b.length - a.length)
        .find((candidate) => {
          const [routeMethod, routePath] = candidate.split(' ');
          return routeMethod === method && routePath === path;
        });
      if (!key) {
        return new Response(JSON.stringify({ error: { code: 'not_found', message: url } }), {
          status: 404,
        });
      }
      const payload = routes[key];
      if (payload instanceof Response) return payload;
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
  return { calls };
}

export function Providers({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

export function renderPanel(ui: ReactElement, options: Omit<RenderOptions, 'wrapper'> = {}) {
  return render(ui, { wrapper: ({ children }) => <Providers>{children}</Providers>, ...options });
}
