import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AnimatePresence, MotionConfig, motion } from 'framer-motion';
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { BrowserRouter, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';

import { ApiError, clearTokens, get, hasRefreshToken, loginWithWidget, setUnauthorizedHandler } from '@/api/client';
import { useRecordPlay, useSavePlayback, useStoredPlayback } from '@/api/playlists';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { HomeIcon, LibraryIcon, MoreIcon, RadioIcon, SearchIcon } from '@/components/icons';
import { MiniPlayer } from '@/components/MiniPlayer';
import { Credit, EmptyState, Glass, Spinner, Toasts, cx } from '@/components/ui';
import { I18nProvider, useI18n, type Key } from '@/i18n';
import { applyPalette, DEFAULT_PALETTE, paletteFromUrl, parsePalette } from '@/lib/color';
import { applyPerf, watchFrameRate } from '@/lib/perf';
import { openTelegramLink } from '@/lib/telegram';
import { useThumbs } from '@/player/thumbs';
import { useConnect } from '@/store/connect';
import { useJam } from '@/store/jam';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';
import { TrackActions } from '@/components/TrackActions';

const FullPlayer = lazy(() => import('@/components/FullPlayer').then((m) => ({ default: m.FullPlayer })));
const Home = lazy(() => import('@/screens/Home').then((m) => ({ default: m.Home })));
const Library = lazy(() => import('@/screens/Library').then((m) => ({ default: m.Library })));
const Discover = lazy(() => import('@/screens/Discover').then((m) => ({ default: m.Discover })));
const Profile = lazy(() => import('@/screens/Profile').then((m) => ({ default: m.Profile })));
const FriendsFeedScreen = lazy(() => import('@/screens/Profile').then((m) => ({ default: m.FriendsFeedScreen })));
const Plans = lazy(() => import('@/screens/Plans').then((m) => ({ default: m.Plans })));
const Settings = lazy(() => import('@/screens/Settings').then((m) => ({ default: m.Settings })));
const Wrapped = lazy(() => import('@/screens/Wrapped').then((m) => ({ default: m.Wrapped })));
const JamScreen = lazy(() => import('@/screens/Jam').then((m) => ({ default: m.JamScreen })));
const BlendScreen = lazy(() => import('@/screens/Blend').then((m) => ({ default: m.BlendScreen })));
const AlbumScreen = lazy(() => import('@/screens/Detail').then((m) => ({ default: m.AlbumScreen })));
const ArtistScreen = lazy(() => import('@/screens/Detail').then((m) => ({ default: m.ArtistScreen })));
const ChannelScreen = lazy(() => import('@/screens/Detail').then((m) => ({ default: m.ChannelScreen })));
const ThisIsScreen = lazy(() => import('@/screens/Detail').then((m) => ({ default: m.ThisIsScreen })));
const TrackScreen = lazy(() => import('@/screens/Detail').then((m) => ({ default: m.TrackScreen })));
const Playlists = lazy(() => import('@/screens/Playlists').then((m) => ({ default: m.Playlists })));
const PlaylistScreen = lazy(() => import('@/screens/Playlists').then((m) => ({ default: m.PlaylistScreen })));
const SharedPlaylistScreen = lazy(() => import('@/screens/Playlists').then((m) => ({ default: m.SharedPlaylistScreen })));
const LikedScreen = lazy(() => import('@/screens/Playlists').then((m) => ({ default: m.LikedScreen })));
const Search = lazy(() => import('@/screens/Search').then((m) => ({ default: m.Search })));
const Tour = lazy(() => import('@/components/Tour'));
const Downloads = lazy(() => import('@/screens/Downloads').then((m) => ({ default: m.Downloads })));

// Desktop sidebar — same destinations as the phone tab bar, plus Downloads
const TABS_DESKTOP: { to: string; key: Key; Icon: typeof HomeIcon }[] = [
  { to: '/', key: 'tab.home', Icon: HomeIcon },
  { to: '/discover', key: 'tab.discover', Icon: RadioIcon },
  { to: '/search', key: 'tab.search', Icon: SearchIcon },
  { to: '/library', key: 'tab.library', Icon: LibraryIcon },
  { to: '/downloads', key: 'web.nav.downloads', Icon: MoreIcon },
  { to: '/settings', key: 'tab.settings', Icon: MoreIcon },
];
const TABS_MOBILE: { to: string; key: Key; Icon: typeof HomeIcon }[] = [
  { to: '/', key: 'tab.home', Icon: HomeIcon },
  { to: '/discover', key: 'tab.discover', Icon: RadioIcon },
  { to: '/search', key: 'tab.search', Icon: SearchIcon },
  { to: '/library', key: 'tab.library', Icon: LibraryIcon },
  { to: '/settings', key: 'tab.settings', Icon: MoreIcon },
];

function DesktopNav() {
  const { t } = useI18n();
  return (
    <aside className="hidden w-[240px] shrink-0 border-e border-[var(--separator)] px-3 py-6 md:block">
      <div className="px-2 text-[15px] font-extrabold tracking-tight">Music</div>
      <nav className="mt-6 flex flex-col gap-0.5">
        {TABS_DESKTOP.map(({ to, key, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cx('flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13.5px]', isActive ? 'bg-[var(--fill)] font-semibold text-[var(--ink)]' : 'text-[var(--ink-dim)] hover:bg-[var(--fill)]')
            }
          >
            <Icon size={18} />
            {t(key)}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}

function BottomNav() {
  const { t } = useI18n();
  return (
    <nav className="glass glass-strong flex justify-around border-t border-[var(--separator)] px-2 pt-2 md:hidden" style={{ paddingBottom: 'calc(8px + var(--safe-bottom))' }}>
      {TABS_MOBILE.map(({ to, key, Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          className={({ isActive }) => cx('grid min-w-14 justify-items-center gap-1 px-3 py-1 text-[10px] font-medium', isActive ? 'text-[var(--accent)]' : 'text-[var(--ink-dim)]')}
        >
          <Icon size={22} />
          {t(key)}
        </NavLink>
      ))}
    </nav>
  );
}

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
  const [widgetFailed, setWidgetFailed] = useState(false);
  const botUsername = (import.meta.env.VITE_BOT_USERNAME as string | undefined) ?? '';

  useEffect(() => {
    if (!botUsername) return;
    let cancelled = false;
    const container = document.getElementById('tg-login');
    if (!container) return;
    container.innerHTML = '';
    const script = document.createElement('script');
    // Try same-origin proxy first (bypasses telegram.org filtering in Iran),
    // fall back to direct telegram.org if proxy 404s — both are allowed by CSP.
    script.src = '/telegram-widget.js';
    script.async = true;
    script.setAttribute('data-telegram-login', botUsername);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-onauth', '__onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    script.onerror = () => {
      // proxy miss — retry directly against telegram.org
      if (cancelled || script.dataset.retried) { if (!cancelled) setWidgetFailed(true); return; }
      script.dataset.retried = '1';
      const fallback = document.createElement('script');
      fallback.src = 'https://telegram.org/js/telegram-widget.js?22';
      fallback.async = true;
      for (const a of ['data-telegram-login', 'data-size', 'data-onauth', 'data-request-access']) fallback.setAttribute(a, script.getAttribute(a) ?? '');
      fallback.onerror = () => { if (!cancelled) setWidgetFailed(true); };
      fallback.onload = () => setTimeout(() => { if (!cancelled && !container.querySelector('iframe')) setWidgetFailed(true); }, 1500);
      container.appendChild(fallback);
    };
    script.onload = () => {
      setTimeout(() => { if (!cancelled && !container.querySelector('iframe')) setWidgetFailed(true); }, 1500);
    };
    container.appendChild(script);
    const timer = setTimeout(() => { if (!cancelled && !container.querySelector('iframe')) setWidgetFailed(true); }, 3500);
    const handler = async (e: Event) => {
      const detail = (e as CustomEvent).detail as Record<string, unknown>;
      setBusy(true);
      setError(null);
      try {
        await loginWithWidget(detail);
        navigate('/', { replace: true });
      } catch (err) {
        if (err instanceof ApiError) {
          const msg = err.message.toLowerCase();
          if (err.status === 401 && (msg.includes('widget') || msg.includes('signature') || msg.includes('stale') || err.code === 'unauthorized')) {
            setError(`${t('web.landing.badSignature', { bot: botUsername, domain: window.location.hostname })} — ${err.code}: ${err.message}`);
          } else if (err.code === 'bad_response') {
            // Almost always VITE_API_URL missing — fetch hit the static host's index.html
            const apiUrl = (import.meta.env.VITE_API_URL as string | undefined) ?? '(not set)';
            setError(`${t('web.landing.corsHint')} — API: ${apiUrl} — ${err.code}: ${err.message.slice(0, 120)}`);
          } else if (err.code === 'network' || err.status === 0) {
            setError(`${t('web.landing.corsHint')} — ${err.code}: ${err.message}`);
          } else {
            setError(`${err.code}: ${err.message}`);
          }
        } else if (err instanceof TypeError) {
          // CORS or network — fetch throws TypeError, not ApiError
          setError(`${t('web.landing.corsHint')} — ${String((err as Error).message).slice(0, 180)}`);
        } else {
          setError('Login failed');
        }
      } finally {
        setBusy(false);
      }
    };
    window.addEventListener('telegram-auth', handler as EventListener);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      window.removeEventListener('telegram-auth', handler as EventListener);
      container.innerHTML = '';
    };
  }, [botUsername, navigate]);

  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col items-center justify-center px-6 py-12 text-center">
      <h1 className="text-3xl font-bold">{t('web.landing.title')}</h1>
      <p className="mt-3 max-w-xl text-[14px] leading-7 text-[var(--ink-dim)]">{t('web.landing.body')}</p>
      <div id="tg-login" className="mt-8 min-h-[44px]">
        {!botUsername && <p className="text-[13px] text-[var(--ink-dim)]">Set VITE_BOT_USERNAME to enable Telegram Login</p>}
      </div>
      {widgetFailed && botUsername && (
        <div className="mt-3 flex flex-col items-center gap-2">
          <a href={`https://t.me/${botUsername}?start=web`} target="_blank" rel="noopener noreferrer" className="rounded-full bg-[var(--accent)] px-6 py-3 text-[14px] font-bold text-[var(--accent-ink)]">
            {t('web.landing.continue') ?? 'Continue with Telegram'}
          </a>
          <p className="max-w-sm text-[12px] leading-5 text-[var(--ink-dim)]">
            {t('web.landing.widgetHint')} — دامنهٔ BotFather باید <span dir="ltr" className="font-mono">noax.virgule.studio</span> باشد.
          </p>
        </div>
      )}
      {busy && <p className="mt-2 text-[13px] text-[var(--ink-dim)]">…</p>}
      {error && <p className="mt-2 text-[13px] text-red-400">{error}</p>}
      <p className="mt-6 text-[12px] text-[var(--ink-faint)]">Works on phone, tablet and desktop · PWA installable</p>
    </div>
  );
}

function useSpecularOnScroll() {
  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const max = document.body.scrollHeight - window.innerHeight;
        const ratio = max > 0 ? Math.min(1, window.scrollY / max) : 0;
        document.documentElement.style.setProperty('--spec-global', String(0.2 + ratio * 0.6));
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
}

function usePlaybackSync(): void {
  const recordPlay = useRecordPlay();
  const savePlayback = useSavePlayback();
  useEffect(() => {
    usePlayer.setState({
      onPlayed: (track, playedSeconds, completed, source, sourceId) => {
        recordPlay.mutate({ track_id: track.id, duration_played: playedSeconds, completed, source, source_id: sourceId });
      },
    });
    return () => usePlayer.setState({ onPlayed: null });
  }, [recordPlay]);
  useEffect(() => {
    const push = () => {
      const state = usePlayer.getState();
      if (!state.current) return;
      savePlayback.mutate({
        track_id: state.current.id,
        position_s: Math.floor(state.position),
        queue: state.queue.map((track) => track.id),
        queue_index: state.index,
        shuffle: state.shuffle,
        repeat_mode: state.repeat,
        speed: state.speed,
      });
    };
    const timer = setInterval(() => { if (usePlayer.getState().isPlaying) push(); }, 10_000);
    const onHide = () => { if (document.visibilityState === 'hidden') push(); };
    document.addEventListener('visibilitychange', onHide);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onHide); };
  }, [savePlayback]);
}

function useResume(): void {
  const stored = useStoredPlayback();
  useEffect(() => {
    const state = stored.data;
    if (!state?.track_id || !state.items?.length) return;
    if (usePlayer.getState().current) return;
    const index = Math.min(state.queue_index, state.items.length - 1);
    usePlayer.setState({
      queue: state.items,
      index,
      current: state.items[index] ?? null,
      position: state.position_s,
      duration: state.items[index]?.duration ?? 0,
      shuffle: state.shuffle,
      repeat: state.repeat_mode,
      speed: state.speed,
    });
  }, [stored.data]);
}

function Shell() {
  const { t } = useI18n();
  const location = useLocation();
  const current = usePlayer((s) => s.current);
  const error = usePlayer((s) => s.error);
  const clearError = usePlayer((s) => s.clearError);
  const toast = useUi((s) => s.toast);
  const showUpsell = useUi((s) => s.showUpsell);
  const thumbs = useThumbs(current ? [current.id] : []);
  const currentThumb = current ? thumbs[current.id] : undefined;
  const actionTrack = useUi((s) => s.actionTrack);
  const setActionTrack = useUi((s) => s.setActionTrack);
  const isOnline = useOnline();
  usePlaybackSync();
  useResume();
  useSpecularOnScroll();
  useEffect(() => {
    if (!error) return;
    if (error.kind === 'plan_limit') showUpsell({ kind: String(error.details.kind ?? 'daily_plays'), limit: Number(error.details.limit ?? 0) });
    else toast(t('player.unavailable'), 'error');
    clearError();
  }, [error, clearError, showUpsell, toast, t]);
  useEffect(() => {
    const track = usePlayer.getState().current;
    const known = parsePalette(track?.palette);
    if (known) { applyPalette(known); return; }
    if (!currentThumb) { applyPalette(DEFAULT_PALETTE); return; }
    let cancelled = false;
    void paletteFromUrl(currentThumb).then((palette) => {
      if (cancelled) return;
      applyPalette(palette);
      if (track) void get(`/v1/tracks/${track.id}/palette`).catch(() => undefined);
    }).catch(() => applyPalette(DEFAULT_PALETTE));
    return () => { cancelled = true; };
  }, [currentThumb]);
  useEffect(() => usePlayer.getState().attach(), []);
  useEffect(() => useConnect.getState().start(), []);
  useEffect(() => { if (!location.pathname.startsWith('/jam/')) void useJam.getState().resume(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl">
      <DesktopNav />
      <div className="flex min-w-0 flex-1 flex-col">
        {!isOnline && <div className="bg-amber-500 px-3 py-2 text-center text-[12px] font-semibold text-black" role="status">{t('app.offline')}</div>}
        <div className="flex-1">
          <AnimatePresence mode="wait" initial={false}>
            <motion.main
              className="flex-1"
              style={{ paddingBottom: 'var(--chrome-h)' }}
              key={location.pathname}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0, transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] } }}
              exit={{ opacity: 0, transition: { duration: 0.12 } }}
            >
              <Suspense fallback={<div className="grid place-items-center py-20"><Spinner /></div>}>
                <Routes location={location}>
                  <Route path="/" element={<Home />} />
                  <Route path="/discover" element={<Discover />} />
                  <Route path="/search" element={<Search />} />
                  <Route path="/library" element={<Library />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="/downloads" element={<Downloads />} />
                  <Route path="/channel/:id" element={<ChannelScreen />} />
                  <Route path="/artist/:id" element={<ArtistScreen />} />
                  <Route path="/this-is/:id" element={<ThisIsScreen />} />
                  <Route path="/album/:artistId/:name" element={<AlbumScreen />} />
                  <Route path="/track/:id" element={<TrackScreen />} />
                  <Route path="/playlists" element={<Playlists />} />
                  <Route path="/playlist/:id" element={<PlaylistScreen />} />
                  <Route path="/shared/:slug" element={<SharedPlaylistScreen />} />
                  <Route path="/likes" element={<LikedScreen />} />
                  <Route path="/plans" element={<Plans />} />
                  <Route path="/profile" element={<Profile />} />
                  <Route path="/user/:id" element={<Profile />} />
                  <Route path="/friends" element={<FriendsFeedScreen />} />
                  <Route path="/wrapped" element={<Wrapped />} />
                  <Route path="/jam" element={<JamScreen />} />
                  <Route path="/blend" element={<BlendScreen />} />
                  <Route path="/blend/:code" element={<BlendScreen />} />
                  <Route path="/jam/:code" element={<JamScreen />} />
                  <Route path="*" element={<EmptyState title={t('app.error')} />} />
                </Routes>
              </Suspense>
            </motion.main>
          </AnimatePresence>
        </div>
        <div className="sticky bottom-0 z-30 mx-auto w-full max-w-6xl">
          <MiniPlayer thumb={currentThumb} />
          <BottomNav />
        </div>
        {current && <Suspense fallback={null}><FullPlayer thumbs={thumbs} /></Suspense>}
        <Paywall />
        <TrackActions track={actionTrack} open={Boolean(actionTrack)} onClose={() => setActionTrack(null)} />
        <Toasts />
        <Suspense fallback={null}><Tour /></Suspense>
      </div>
    </div>
  );
}

function useOnline(): boolean {
  const [online, setOnline] = useState(typeof navigator !== 'undefined' ? navigator.onLine : true);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);
  return online;
}

function Paywall() {
  const { t } = useI18n();
  const upsell = useUi((s) => s.upsell);
  const close = () => useUi.getState().showUpsell(null);
  const key = upsell?.kind === 'daily_plays' ? 'plan.limit.daily_plays' : upsell?.kind === 'playlists' ? 'plan.limit.playlists' : 'plan.limit.channels';
  if (!upsell) return null;
  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/50 p-4">
      <Glass className="w-full max-w-sm p-6">
        <p className="mb-5 text-[13.5px] leading-7 text-[var(--ink-dim)]">{t(key, { limit: upsell.limit ?? 0 })}</p>
        <div className="flex gap-2">
          <button type="button" onClick={close} className="flex-1 rounded-xl bg-[var(--fill)] py-2.5 text-[13.5px]">{t('plan.later')}</button>
          <a href="#/plans" onClick={close} className="flex-1 rounded-xl bg-[var(--accent)] py-2.5 text-center text-[13.5px] font-bold text-[var(--accent-ink)]">{t('plan.upgrade')}</a>
        </div>
      </Glass>
    </div>
  );
}

function JoinScreen({ channels, onPassed, onStillMissing }: { channels: { username: string; url: string }[]; onPassed: () => void; onStillMissing: (c: { username: string; url: string }[]) => void; }) {
  const { t } = useI18n();
  const [checking, setChecking] = useState(false);
  const [stillMissing, setStillMissing] = useState(false);
  return (
    <div className="grid min-h-screen place-items-center px-6">
      <Glass className="w-full max-w-sm space-y-4 p-6 text-center" strong>
        <h1 className="text-[16px] font-bold">{t('gate.title')}</h1>
        <p className="text-[13px] leading-7 text-[var(--ink-dim)]">{t('gate.body')}</p>
        <div className="space-y-2">
          {channels.map((ch) => (
            <button key={ch.username} type="button" className="w-full rounded-xl bg-[var(--accent)] px-4 py-2.5 text-[14px] font-bold text-[var(--accent-ink)]" onClick={() => openTelegramLink(ch.url)}>
              {t('gate.join', { channel: `@${ch.username}` })}
            </button>
          ))}
        </div>
        <button
          type="button"
          disabled={checking}
          className="w-full rounded-xl border border-[var(--separator)] px-4 py-2.5 text-[14px] disabled:opacity-50"
          onClick={() => {
            setChecking(true); setStillMissing(false);
            import('@/api/client').then(({ post }) =>
              post<{ missing: { username: string; url: string }[] }>('/v1/gate/recheck', {}).then((gate) => {
                if (gate.missing.length === 0) onPassed();
                else { onStillMissing(gate.missing); setStillMissing(true); }
              }).catch(() => setStillMissing(true)).finally(() => setChecking(false))
            );
          }}
        >
          {checking ? '…' : t('gate.done')}
        </button>
        {stillMissing && <p className="text-[12px] text-red-400">{t('gate.still')}</p>}
        <Credit />
      </Glass>
    </div>
  );
}

function Gate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'checking' | 'ready' | 'landing' | 'join'>('checking');
  const [joinChannels, setJoinChannels] = useState<{ username: string; url: string }[]>([]);
  const [gateError, setGateError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Debug: force landing for testing: ?forceLanding=1
      if (new URLSearchParams(window.location.search).has('forceLanding')) { if (!cancelled) setState('landing'); return; }
      const hasToken = hasRefreshToken();
      // console for remote debugging (incognito)
      try { console.info('[Gate] hasRefreshToken=', hasToken, 'localStorage tmusic.refresh=', (()=>{ try{return localStorage.getItem('tmusic.refresh')?.slice(0,12)??'null'}catch{return 'err'}})()); } catch {}
      if (!hasToken) { if (!cancelled) setState('landing'); return; }
      try {
        const gate = await get<{ missing: { username: string; url: string }[] }>('/v1/gate');
        if (cancelled) return;
        if (gate.missing.length) { setJoinChannels(gate.missing); setState('join'); }
        else setState('ready');
      } catch (err) {
        if (cancelled) return;
        // Auth/Gate failure must NOT silently show the app — send the user to Landing so the button appears
        if (err instanceof ApiError && (err.status === 401 || err.code === 'unauthorized' || err.code === 'no_refresh')) {
          try { clearTokens(); } catch {}
          setGateError(`${err.code}: ${err.message}`);
          setState('landing');
          return;
        }
        // Transient gate error (e.g. network) — let the app load; player will show retry
        setGateError(err instanceof Error ? err.message : String(err));
        setState('ready');
      }
    })();
    return () => { cancelled = true; };
  }, []);
  if (state === 'checking') return <div className="grid min-h-screen place-items-center text-sm text-[var(--ink-dim)]">…</div>;
  if (state === 'landing') return <><Landing />{gateError && <p className="fixed bottom-2 left-1/2 z-50 -translate-x-1/2 rounded-full bg-black/70 px-3 py-1 text-[11px] text-white">gate: {gateError}</p>}</>;
  if (state === 'join') return <JoinScreen channels={joinChannels} onPassed={() => setState('ready')} onStillMissing={setJoinChannels} />;
  return <>{children}</>;
}

export function App() {
  const lang = useUi((s) => s.lang);
  const theme = useUi((s) => s.theme);
  const lowPerf = useUi((s) => s.lowPerf);
  useEffect(() => {
    applyPerf(lowPerf);
    setUnauthorizedHandler(() => { clearTokens(); queryClient.clear(); location.reload(); });
    const stop = watchFrameRate(() => useUi.getState().markSlowDevice());
    return () => { stop(); setUnauthorizedHandler(null); };
  }, [lowPerf]);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  // Gate needs i18n, so mount Router inside QueryClient+I18n
  const content = useMemo(() => (
    <BrowserRouter>
      <Gate><Shell /></Gate>
    </BrowserRouter>
  ), []);
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <MotionConfig reducedMotion="user">
          <I18nProvider lang={lang}>{content}</I18nProvider>
        </MotionConfig>
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
