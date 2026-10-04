import { motion } from 'framer-motion';
import { NavLink } from 'react-router-dom';

import { HomeIcon, LibraryIcon, MoreIcon, RadioIcon, SearchIcon } from '@/components/icons';
import { bouncy, cx } from '@/components/ui';
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
      data-tour="tabbar"
      // Music's tab bar is a material with a hairline on top, edge to edge — not a
      // floating pill. The hairline is what separates it from the list behind it.
      className="glass glass-strong flex justify-around border-t border-[var(--separator)] px-2 pt-2"
      style={{ paddingBottom: 'calc(8px + var(--safe-bottom))' }}
    >
      {TABS.map(({ to, key, Icon }) => (
        <NavLink
          key={to}
          to={to}
          data-tour={to === '/' ? 'tab-home' : to === '/search' ? 'tab-search' : to === '/library' ? 'tab-library' : undefined}
          end={to === '/'}
          onClick={() => haptic('select')}
          className={({ isActive }) =>
            cx(
              'relative grid min-w-14 justify-items-center gap-1 px-3 py-1 text-[10px] font-medium',
              // The label is the tint, not a pill behind it.
              isActive ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]',
            )
          }
        >
          {({ isActive }) => (
            <>
              {/* The icon lands when its tab is picked: a small pop, not a slide. */}
              <motion.span
                animate={{ scale: isActive ? 1 : 0.94, y: isActive ? -1 : 0 }}
                whileTap={{ scale: 0.8 }}
                transition={bouncy}
                className="grid place-items-center"
              >
                <Icon size={24} strokeWidth={isActive ? 2.2 : 1.8} />
              </motion.span>
              {t(key)}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
