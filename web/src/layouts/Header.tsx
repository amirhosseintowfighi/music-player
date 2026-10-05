import { useUi } from '@/store/ui';

export function Header() {
  const lang = useUi((s) => s.lang);
  const setLang = useUi((s) => s.setLang);
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);

  return (
    <header
      className="tv-header sticky top-0 z-20 hidden h-12 items-center gap-3 border-b border-[var(--separator)] bg-[var(--glass-bg)] px-4 backdrop-blur-[var(--glass-blur)] md:flex"
      style={{ paddingTop: 'max(0px, env(safe-area-inset-top, 0px))' }}
    >
      <div className="min-w-0 flex-1" />
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="tv-focus rounded-full border border-[var(--separator)] px-3 py-1.5 text-[12px] focus:outline-none"
          onClick={() => setLang(lang === 'fa' ? 'en' : 'fa')}
          data-focusable
          aria-label={lang}
        >
          {lang === 'fa' ? 'EN' : 'FA'}
        </button>
        <button
          type="button"
          className="tv-focus rounded-full border border-[var(--separator)] px-3 py-1.5 text-[12px] focus:outline-none"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          data-focusable
          aria-label={theme}
        >
          {theme === 'dark' ? '☾' : '☀'}
        </button>
      </div>
    </header>
  );
}
