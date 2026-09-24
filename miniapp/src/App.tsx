import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { HashRouter, Route, Routes, useLocation } from 'react-router-dom';

import { ApiError, get, login, post, put, setUnauthorizedHandler } from '@/api/client';
import { useRecordPlay, useSavePlayback, useStoredPlayback } from '@/api/playlists';
import { useMe } from '@/api/hooks';
import { MiniPlayer } from '@/components/MiniPlayer';
import { TabBar } from '@/components/TabBar';
import { Aurora, Credit, EmptyState, Glass, Sheet, Spinner, Toasts } from '@/components/ui';
import { I18nProvider, useI18n } from '@/i18n';
import { applyPalette, DEFAULT_PALETTE, paletteFromUrl, parsePalette } from '@/lib/color';
import { applyPerf, watchFrameRate } from '@/lib/perf';
import { initTelegram, openTelegramLink } from '@/lib/telegram';
import { useThumbs } from '@/player/thumbs';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';
import { Home } from '@/screens/Home';
import { Library } from '@/screens/Library';
import { Search } from '@/screens/Search';
import { Discover } from '@/screens/Discover';
import { FriendsFeedScreen, Profile } from '@/screens/Profile';
import { Plans } from '@/screens/Plans';
import { Settings } from '@/screens/Settings';
import { Wrapped } from '@/screens/Wrapped';
import { ArtistScreen, ChannelScreen, TrackScreen } from '@/screens/Detail';
import { LikedScreen, PlaylistScreen, Playlists, SharedPlaylistScreen } from '@/screens/Playlists';
import { TrackActions } from '@/components/TrackActions';

// The full-screen player is only needed once something plays.
const FullPlayer = lazy(() => import('@/components/FullPlayer').then((m) => ({ default: m.FullPlayer })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => (error instanceof ApiError && error.status < 500 ? false : count < 2),
      refetchOnWindowFocus: false,
    },
  },
});

function Paywall() {
  const { t } = useI18n();
  const upsell = useUi((s) => s.upsell);
  const close = () => useUi.getState().showUpsell(null);
  const key =
    upsell?.kind === 'daily_plays'
      ? 'plan.limit.daily_plays'
      : upsell?.kind === 'playlists'
        ? 'plan.limit.playlists'
        : 'plan.limit.channels';
  return (
    <Sheet open={Boolean(upsell)} onClose={close} title={t('plan.upgrade')}>
      <p className="mb-5 px-1 text-[13.5px] leading-7 text-[var(--ink-dim)]">
        {t(key, { limit: upsell?.limit ?? 0 })}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={close}
          className="flex-1 rounded-xl bg-white/8 py-2.5 text-[13.5px]"
        >
          {t('plan.later')}
        </button>
        <a
          href="#/plans"
          onClick={close}
          className="flex-1 rounded-xl bg-[var(--accent)] py-2.5 text-center text-[13.5px] font-bold text-[var(--accent-ink)]"
        >
          {t('plan.upgrade')}
        </a>
      </div>
    </Sheet>
  );
}

/** Scroll drives the specular highlight so the glass looks lit from a fixed source. */
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


/** Reports finished plays and keeps the server-side resume point up to date. */
function usePlaybackSync(): void {
  const recordPlay = useRecordPlay();
  const savePlayback = useSavePlayback();

  useEffect(() => {
    usePlayer.setState({
      onPlayed: (track, playedSeconds, completed, source, sourceId) => {
        recordPlay.mutate({
          track_id: track.id,
          duration_played: playedSeconds,
          completed,
          source,
          source_id: sourceId,
        });
      },
    });
    return () => usePlayer.setState({ onPlayed: null });
  }, [recordPlay]);

  useEffect(() => {
    // Save the resume point every 10s while playing, and when the app is hidden.
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
    const timer = setInterval(() => {
      if (usePlayer.getState().isPlaying) push();
    }, 10_000);
    const onHide = () => {
      if (document.visibilityState === 'hidden') push();
    };
    document.addEventListener('visibilitychange', onHide);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onHide);
    };
  }, [savePlayback]);
}

/** Restores the queue from another device without starting playback. */
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
  usePlaybackSync();
  useResume();

  useSpecularOnScroll();

  // Playback errors surface as a toast, or as the paywall when a limit was hit.
  useEffect(() => {
    if (!error) return;
    if (error.kind === 'plan_limit') {
      showUpsell({ kind: String(error.details.kind ?? 'daily_plays'), limit: Number(error.details.limit ?? 0) });
    } else {
      toast(t('player.unavailable'), 'error');
    }
    clearError();
  }, [error, clearError, showUpsell, toast, t]);

  // Artwork tints the background. The colours travel with the track once anyone has
  // computed them, so most listeners never touch a canvas (ADR-003 phase 12).
  useEffect(() => {
    const track = usePlayer.getState().current;
    const known = parsePalette(track?.palette);
    if (known) {
      applyPalette(known);
      return;
    }
    if (!currentThumb) {
      applyPalette(DEFAULT_PALETTE);
      return;
    }
    let cancelled = false;
    void paletteFromUrl(currentThumb)
      .then((palette) => {
        if (cancelled) return;
        applyPalette(palette);
        // Teach the server, so the next listener gets it for free.
        if (track) {
          void put(`/v1/tracks/${track.id}/palette`, {
            colors: [palette.c1, palette.c2, palette.c3],
          }).catch(() => undefined);
        }
      })
      .catch(() => applyPalette(DEFAULT_PALETTE));
    return () => {
      cancelled = true;
    };
  }, [currentThumb]);

  useEffect(() => usePlayer.getState().attach(), []);

  return (
    <div className="mx-auto flex min-h-full max-w-lg flex-col" style={{ paddingTop: 'var(--safe-top)' }}>
      <Aurora />
      <main className="flex-1" style={{ paddingBottom: 'var(--chrome-h)' }} key={location.pathname}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/discover" element={<Discover />} />
          <Route path="/search" element={<Search />} />
          <Route path="/library" element={<Library />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/channel/:id" element={<ChannelScreen />} />
          <Route path="/artist/:id" element={<ArtistScreen />} />
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
          <Route path="*" element={<EmptyState title={t('app.error')} />} />
        </Routes>
      </main>

      <div className="fixed inset-x-2.5 bottom-2.5 z-30 mx-auto max-w-lg">
        <MiniPlayer thumb={currentThumb} />
        <TabBar />
      </div>

      {current && (
        <Suspense fallback={null}>
          <FullPlayer thumbs={thumbs} />
        </Suspense>
      )}
      <Paywall />
      <TrackActions track={actionTrack} open={Boolean(actionTrack)} onClose={() => setActionTrack(null)} />
      <Toasts />
    </div>
  );
}

/** Authenticates with initData before the first API call. */
function Gate({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const [state, setState] = useState<'loading' | 'ready' | 'outside' | 'failed' | 'join'>(
    'loading',
  );
  const [detail, setDetail] = useState('');
  const [missing, setMissing] = useState<JoinChannel[]>([]);
  const me = useMe();
  const setLang = useUi((s) => s.setLang);

  useEffect(() => {
    let cancelled = false;
    login()
      .then((pair) => {
        if (cancelled) return;
        const param = pair.start_param ?? '';
        if (param.startsWith('pl_')) window.location.hash = `#/shared/${param.slice(3)}`;
        // A shared track: open the app on it and start playing (ADR-003 phase 12).
        else if (param.startsWith('tr_')) window.location.hash = `#/track/${param.slice(3)}`;
        // The forced-join gate. It is deliberately not on the playback path: it runs
        // once here, and an unreachable check lets the listener in (the server
        // decides that, not this screen).
        return get<{ missing: JoinChannel[] }>('/v1/gate')
          .then((gate) => {
            if (cancelled) return;
            setMissing(gate.missing);
            setState(gate.missing.length ? 'join' : 'ready');
          })
          .catch(() => {
            if (!cancelled) setState('ready');
          });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // Two very different failures used to show the same sentence, and the
        // second one sent operators hunting in the wrong place: "opened in a
        // browser" is the user's doing, "the API did not answer" is the server's.
        const noInitData = error instanceof ApiError && error.code === 'no_init_data';
        setDetail(error instanceof ApiError ? `${error.status} ${error.code}` : 'network');
        setState(noInitData ? 'outside' : 'failed');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (me.data?.lang) setLang(me.data.lang === 'en' ? 'en' : 'fa');
  }, [me.data?.lang, setLang]);

  if (state === 'loading') {
    return (
      <div className="grid h-full place-items-center gap-3">
        <Spinner size={26} />
        <p className="text-[13px] text-[var(--ink-dim)]">{t('app.loading')}</p>
      </div>
    );
  }
  if (state === 'outside') {
    return (
      <div className="grid h-full place-items-center px-8">
        <Glass className="p-6 text-center text-[13.5px] leading-7">{t('app.outsideTelegram')}</Glass>
      </div>
    );
  }
  if (state === 'join') {
    return (
      <JoinScreen
        channels={missing}
        onPassed={() => setState('ready')}
        onStillMissing={setMissing}
      />
    );
  }
  if (state === 'failed') {
    return (
      <div className="grid h-full place-items-center px-8">
        <Glass className="p-6 text-center text-[13.5px] leading-7">
          <p>{t('app.signInFailed')}</p>
          <p className="mt-2 text-[11.5px] text-[var(--ink-faint)]">{detail}</p>
        </Glass>
      </div>
    );
  }
  return <>{children}</>;
}

interface JoinChannel {
  username: string;
  url: string;
}

/** Shown when the owner requires channel membership. One tap per channel, then recheck. */
function JoinScreen({
  channels,
  onPassed,
  onStillMissing,
}: {
  channels: JoinChannel[];
  onPassed: () => void;
  onStillMissing: (channels: JoinChannel[]) => void;
}) {
  const { t } = useI18n();
  const [checking, setChecking] = useState(false);
  const [stillMissing, setStillMissing] = useState(false);

  return (
    <div className="grid h-full place-items-center px-6">
      <Glass className="w-full max-w-sm space-y-4 p-6 text-center">
        <h1 className="text-[16px] font-bold">{t('gate.title')}</h1>
        <p className="text-[13px] leading-7 text-[var(--ink-dim)]">{t('gate.body')}</p>
        <div className="space-y-2">
          {channels.map((channel) => (
            <button
              key={channel.username}
              type="button"
              className="w-full rounded-xl bg-[var(--accent)] px-4 py-2.5 text-[14px] font-bold text-[var(--accent-ink)]"
              onClick={() => openTelegramLink(channel.url)}
            >
              {t('gate.join', { channel: `@${channel.username}` })}
            </button>
          ))}
        </div>
        <button
          type="button"
          disabled={checking}
          className="w-full rounded-xl border border-white/15 px-4 py-2.5 text-[14px] disabled:opacity-50"
          onClick={() => {
            setChecking(true);
            setStillMissing(false);
            post<{ missing: JoinChannel[] }>('/v1/gate/recheck', {})
              .then((gate) => {
                if (gate.missing.length === 0) onPassed();
                else {
                  onStillMissing(gate.missing);
                  setStillMissing(true);
                }
              })
              .catch(() => setStillMissing(true))
              .finally(() => setChecking(false));
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

export function App() {
  const lang = useUi((s) => s.lang);
  const theme = useUi((s) => s.theme);
  const lowPerf = useUi((s) => s.lowPerf);

  useEffect(() => {
    const telegram = initTelegram();
    useUi.getState().setTheme(telegram.colorScheme);
    useUi.getState().setLang(document.documentElement.lang === 'en' ? 'en' : 'fa');
    applyPerf(lowPerf);
    setUnauthorizedHandler(() => queryClient.clear());
    const stop = watchFrameRate(() => useUi.getState().markSlowDevice());
    return () => {
      stop();
      setUnauthorizedHandler(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const content = useMemo(
    () => (
      <HashRouter>
        <Gate>
          <Shell />
        </Gate>
      </HashRouter>
    ),
    [],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider lang={lang}>{content}</I18nProvider>
    </QueryClientProvider>
  );
}
