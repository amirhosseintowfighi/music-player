import { motion } from 'framer-motion';
import { NavLink } from 'react-router-dom';

import { HomeIcon, LibraryIcon, MoreIcon, RadioIcon, SearchIcon } from '@/components/icons';
import { cx, spring } from '@/components/ui';
import { useI18n, type Key } from '@/i18n';
import { haptic } from '@/lib/telegram';

const TABS: { to: string; key: Key; Icon: typeof HomeIcon }[] = [
  { to: '/', key: 'tab.home', Icon: HomeIcon },
  { to: '/discover', key: 'tab.discover', Icon: RadioIcon },
  { to: '/search', key: 'tab.search', Icon: SearchIcon },
  { to: '/library', key: 'tab.library', Icon: LibraryIcon },
  { to: '/settings', key: 'tab.settings', Icon: MoreIcon },
];

export function TabBar() {
  const { t } = useI18n();
  return (
    <nav
      className="glass glass-edge glass-spec glass-strong flex justify-around rounded-[var(--radius-glass-lg)] px-2 pt-2.5"
      style={{ paddingBottom: 'calc(10px + var(--safe-bottom))', ['--spec' as string]: 0.4 }}
    >
      {TABS.map(({ to, key, Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          onClick={() => haptic('select')}
          className={({ isActive }) =>
            cx(
              'relative grid min-w-14 justify-items-center gap-1 rounded-2xl px-3 py-1.5 text-[10.5px]',
              isActive ? 'text-[var(--accent)]' : 'text-[var(--ink-faint)]',
            )
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <motion.span
                  layoutId="tab-pill"
                  transition={spring}
                  className="absolute inset-0 -z-10 rounded-2xl bg-[color-mix(in_oklab,var(--accent)_14%,transparent)]"
                />
              )}
              <Icon size={21} />
              {t(key)}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
