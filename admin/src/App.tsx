import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { HashRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom';

import { fetchMe, saveToken, setUnauthorizedHandler, type AdminMe } from '@/api/client';
import { Spinner, cx } from '@/components/ui';
import { Broadcasts } from '@/screens/Broadcasts';
import { Candidates } from '@/screens/Candidates';
import { Crawler } from '@/screens/Crawler';
import { Metadata } from '@/screens/Metadata';
import { Dashboard } from '@/screens/Dashboard';
import { Login } from '@/screens/Login';
import { Payments } from '@/screens/Payments';
import { Audit, Health, Reports, Settings } from '@/screens/System';
import { Users } from '@/screens/Users';

const NAV = [
  { to: '/', label: 'داشبورد', permission: 'dashboard.view' },
  { to: '/users', label: 'کاربران', permission: 'users.view' },
  { to: '/payments', label: 'پرداخت‌ها', permission: 'payments.review' },
  { to: '/reports', label: 'گزارش‌ها', permission: 'content.view' },
  { to: '/candidates', label: 'کانال‌های پیشنهادی', permission: 'content.view' },
  { to: '/crawler', label: 'کرالر', permission: 'system.view' },
  { to: '/metadata', label: 'بازبینی متادیتا', permission: 'content.view' },
  { to: '/broadcasts', label: 'پیام همگانی', permission: 'broadcast.send' },
  { to: '/health', label: 'سلامت', permission: 'system.view' },
  { to: '/settings', label: 'تنظیمات', permission: 'system.edit' },
  { to: '/audit', label: 'لاگ', permission: 'system.view' },
];

function allowed(me: AdminMe | null, permission: string): boolean {
  return Boolean(me && (me.permissions.includes('*') || me.permissions.includes(permission)));
}

function Shell({ me, onLogout }: { me: AdminMe; onLogout: () => void }) {
  return (
    <div className="mx-auto max-w-6xl p-4">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1">
          {NAV.filter((entry) => allowed(me, entry.permission)).map((entry) => (
            <NavLink
              key={entry.to}
              to={entry.to}
              end={entry.to === '/'}
              className={({ isActive }) =>
                cx(
                  'rounded-lg px-3 py-1.5 text-[13px]',
                  isActive
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'border border-[var(--color-line)] bg-white',
                )
              }
            >
              {entry.label}
            </NavLink>
          ))}
        </div>
        <div className="flex items-center gap-2 text-[12px] text-[var(--color-muted)]">
          <span>
            {me.role} · tg {me.tg_id}
          </span>
          <button
            type="button"
            onClick={onLogout}
            className="rounded-lg border border-[var(--color-line)] bg-white px-3 py-1.5"
          >
            خروج
          </button>
        </div>
      </header>

      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/users" element={<Users me={me} />} />
        <Route path="/payments" element={<Payments />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/candidates" element={<Candidates />} />
        <Route path="/crawler" element={<Crawler />} />
        <Route path="/metadata" element={<Metadata />} />
        <Route path="/broadcasts" element={<Broadcasts />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings me={me} />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}

export function App() {
  const [me, setMe] = useState<AdminMe | null>(null);
  const [loading, setLoading] = useState(true);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false } },
      }),
  );

  const reload = () => {
    setLoading(true);
    void fetchMe().then((found) => {
      setMe(found);
      setLoading(false);
    });
  };

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    reload();
    return () => setUnauthorizedHandler(null);
  }, []);

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner />
      </div>
    );
  }

  return (
    <QueryClientProvider client={client}>
      {me ? (
        <HashRouter>
          <Shell
            me={me}
            onLogout={() => {
              saveToken(null);
              setMe(null);
            }}
          />
        </HashRouter>
      ) : (
        <Login onLoggedIn={reload} />
      )}
    </QueryClientProvider>
  );
}
