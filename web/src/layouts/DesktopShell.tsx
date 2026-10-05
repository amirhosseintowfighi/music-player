import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';

import { HomeIcon, LibraryIcon, MoreIcon, RadioIcon, SearchIcon } from '@/components/icons';
import { cx } from '@/components/ui';
import { useI18n, type Key } from '@/i18n';
import { Header } from './Header';

const TABS: { to: string; key: Key; Icon: typeof HomeIcon }[] = [
  { to: '/', key: 'tab.home', Icon: HomeIcon },
  { to: '/discover', key: 'tab.discover', Icon: RadioIcon },
  { to: '/search', key: 'tab.search', Icon: SearchIcon },
  { to: '/library', key: 'tab.library', Icon: LibraryIcon },
  { to: '/downloads', key: 'web.nav.downloads', Icon: MoreIcon },
  { to: '/settings', key: 'tab.settings', Icon: MoreIcon },
];

export function DesktopShell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  return (
    <>
      {/* md+ only: narrow Apple-like sidebar + minimal header. Mobile BottomNav stays elsewhere. */}
      <aside className="tv-sidebar hidden w-[220px] shrink-0 border-e border-[var(--separator)] px-3 py-6 md:block lg:w-[240px]">
        <div className="px-2 text-[15px] font-extrabold tracking-tight">Music</div>
        <nav className="mt-6 flex flex-col gap-0.5">
          {TABS.map(({ to, key, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cx(
                  'tv-focus flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13.5px] focus:outline-none',
                  isActive ? 'bg-[var(--fill)] font-semibold text-[var(--ink)]' : 'text-[var(--ink-dim)] hover:bg-[var(--fill)]',
                )
              }
              data-focusable
            >
              <Icon size={18} />
              {t(key)}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        {/* Content column is a container for container queries */}
        <div className="tv-safe min-w-0 flex-1 [container-type:inline-size] [container-name:content]">{children}</div>
      </div>
    </>
  );
}
