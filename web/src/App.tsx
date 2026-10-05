import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { BrowserRouter, Route, Routes, Navigate, useNavigate } from 'react-router-dom';

import { ApiError, hasRefreshToken, loginWithWidget, clearTokens, setUnauthorizedHandler } from '@/api/client';
import { I18nProvider, useI18n } from '@/i18n';
import { useUi } from '@/store/ui';
import { usePlayer } from '@/store/player';
import { applyPerf } from '@/lib/perf';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: (c, e) => (e instanceof ApiError && e.status < 500 ? false : c < 2), refetchOnWindowFocus: false },
  },
});

function Landing() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const botUsername = (import.meta.env.VITE_BOT_USERNAME as string | undefined) ?? '';

  useEffect(() => {
    if (!botUsername) return;
    // Load widget script lazily and wire callback
    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.async = true;
    script.setAttribute('data-telegram-login', botUsername);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-onauth', '__onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    document.getElementById('tg-login')?.appendChild(script);
    const handler = async (e: Event) => {
      const detail = (e as CustomEvent).detail as Record<string, unknown>;
      setBusy(true);
      setError(null);
      try {
        await loginWithWidget(detail);
        navigate('/', { replace: true });
      } catch (err) {
        setError(err instanceof ApiError ? `${err.code}: ${err.message}` : 'Login failed');
      } finally {
        setBusy(false);
      }
    };
    window.addEventListener('telegram-auth', handler as EventListener);
    return () => {
      window.removeEventListener('telegram-auth', handler as EventListener);
      script.remove();
    };
  }, [botUsername, navigate]);

  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col items-center justify-center px-6 py-12 text-center">
      <h1 className="text-3xl font-bold">{t('web.landing.title')}</h1>
      <p className="mt-3 max-w-xl text-[14px] leading-7 text-[var(--ink-dim)]">{t('web.landing.body')}</p>
      <div id="tg-login" className="mt-8 min-h-[44px]">
        {!botUsername && <p className="text-[13px] text-[var(--ink-dim)]">Set VITE_BOT_USERNAME to enable Telegram Login</p>}
      </div>
      {busy && <p className="mt-2 text-[13px] text-[var(--ink-dim)]">…</p>}
      {error && <p className="mt-2 text-[13px] text-red-400">{error}</p>}
      <p className="mt-6 text-[12px] text-[var(--ink-faint)]">Works on phone, tablet and desktop · PWA installable</p>
    </div>
  );
}

function Shell() {
  const current = usePlayer((s) => s.current);
  return (
    <div className="mx-auto flex min-h-screen max-w-6xl">
      <aside className="hidden w-[240px] shrink-0 border-e border-[var(--separator)] p-4 md:block">
        <div className="text-sm font-bold">Music</div>
        <nav className="mt-6 space-y-1 text-sm">
          <a href="/" className="block rounded-lg px-3 py-2 hover:bg-[var(--fill)]">Home</a>
          <a href="/search" className="block rounded-lg px-3 py-2 hover:bg-[var(--fill)]">Search</a>
          <a href="/library" className="block rounded-lg px-3 py-2 hover:bg-[var(--fill)]">Library</a>
          <a href="/downloads" className="block rounded-lg px-3 py-2 hover:bg-[var(--fill)]">Downloads</a>
        </nav>
      </aside>
      <main className="flex-1 p-4 pb-[88px]">
        <Routes>
          <Route path="/" element={<div className="text-[14px] text-[var(--ink-dim)]">Home — coming next</div>} />
          <Route path="/downloads" element={<DownloadsStub />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {current && <div className="fixed inset-x-0 bottom-0 border-t border-[var(--separator)] bg-[var(--card)] p-3 text-sm">Now playing: {current.title}</div>}
    </div>
  );
}

function DownloadsStub() {
  return <div className="text-sm text-[var(--ink-dim)]">Downloads — IndexedDB + Cache Storage (next phase)</div>;
}

function Gate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'checking' | 'authed' | 'landing'>('checking');
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!hasRefreshToken()) {
        if (!cancelled) setState('landing');
        return;
      }
      try {
        // try to refresh silently via client authorize path
        const { get } = await import('@/api/client');
        await get('/v1/me');
        if (!cancelled) setState('authed');
      } catch {
        if (!cancelled) setState('landing');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  if (state === 'checking') return <div className="grid min-h-screen place-items-center text-sm text-[var(--ink-dim)]">…</div>;
  if (state === 'landing') return <Landing />;
  return <>{children}</>;
}

export function App() {
  const lang = useUi((s) => s.lang);
  const theme = useUi((s) => s.theme);
  const lowPerf = useUi((s) => s.lowPerf);
  useEffect(() => {
    applyPerf(lowPerf);
    setUnauthorizedHandler(() => {
      clearTokens();
      queryClient.clear();
      location.hash = '#/';
      location.reload();
    });
    return () => setUnauthorizedHandler(null);
  }, [lowPerf]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const content = useMemo(
    () => (
      <BrowserRouter>
        <Gate>
          <Shell />
        </Gate>
      </BrowserRouter>
    ),
    [],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider lang={lang}>{content}</I18nProvider>
    </QueryClientProvider>
  );
}
