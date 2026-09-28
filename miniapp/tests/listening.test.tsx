import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { __setAuthForTests } from '@/api/client';
import { activeLine, parseLrc } from '@/api/listening';
import { LyricsView } from '@/components/Lyrics';
import { TrackActions } from '@/components/TrackActions';
import { allAudio, audio, preloadNext, resetForTests, spareTrackId, takePreloaded } from '@/player/engine';
import { BlendScreen } from '@/screens/Blend';
import { ThisIsScreen } from '@/screens/Detail';
import { Playlists } from '@/screens/Playlists';
import { Settings } from '@/screens/Settings';
import { useAudioSettings } from '@/store/audio';
import { useDj } from '@/store/dj';
import { usePlayer } from '@/store/player';
import { useUi } from '@/store/ui';

import { authRoutes, makeTrack, mockApi, renderApp } from './helpers';

const opened: string[] = [];
vi.mock('@/lib/telegram', async () => {
  const actual = await vi.importActual<typeof import('@/lib/telegram')>('@/lib/telegram');
  return { ...actual, openTelegramLink: (url: string) => opened.push(url), haptic: () => undefined };
});

const tracks = [1, 2, 3, 4, 5, 6].map((id) => makeTrack({ id, title: `Song ${id}` }));
const recs = [makeTrack({ id: 91, title: 'Suggested A' }), makeTrack({ id: 92, title: 'Suggested B' })];

const ticket = (id: number) => ({
  url: `https://cdn.test/s/${id}`,
  thumb_url: null,
  expires_at: Math.floor(Date.now() / 1000) + 300,
  size: 1,
  mime: 'audio/mpeg',
});

function streams(ids: number[]) {
  return Object.fromEntries(ids.map((id) => [`POST /v1/tracks/${id}/stream`, ticket(id)]));
}

beforeEach(() => {
  resetForTests();
  opened.length = 0;
  __setAuthForTests('token');
  useDj.getState().stop();
  usePlayer.setState({
    queue: [],
    manual: [],
    index: 0,
    current: null,
    isPlaying: false,
    isLoading: false,
    shuffle: false,
    smart: false,
    smartIds: [],
    unshuffled: null,
    remote: null,
    error: null,
    position: 0,
  });
  useUi.setState({ toasts: [], actionTrack: null });
});

afterEach(() => {
  useDj.getState().stop();
});

describe('lyrics', () => {
  it('parses LRC and finds the line being sung', () => {
    const lines = parseLrc('[00:01.00] one\n[ar: someone]\n[00:05.50]two\n[01:02.25][01:10.00] chorus');
    expect(lines.map((l) => l.text)).toEqual(['one', 'two', 'chorus', 'chorus']);
    expect(lines[2]?.at).toBeCloseTo(62.25);
    expect(activeLine(lines, 0)).toBe(-1);
    expect(activeLine(lines, 5.5)).toBe(1);
    expect(activeLine(lines, 999)).toBe(3);
  });

  it('shows synced lines and seeks when one is tapped', async () => {
    mockApi({
      'GET /v1/tracks/1/lyrics': {
        track_id: 1,
        found: true,
        synced: '[00:01.00] first line\n[00:05.00] second line',
        plain: null,
        source: 'lrclib',
      },
    });
    const seek = vi.fn();
    usePlayer.setState({
      position: 6,
      remote: { seek, next: vi.fn(), previous: vi.fn(), toggle: vi.fn(), ended: vi.fn(), enqueue: vi.fn(), play: vi.fn() },
    });
    renderApp(<LyricsView trackId={1} />);
    await userEvent.click(await screen.findByText('first line'));
    expect(seek).toHaveBeenCalledWith(1);
  });

  it('says so when there are none', async () => {
    mockApi({ 'GET /v1/tracks/2/lyrics': { track_id: 2, found: false } });
    renderApp(<LyricsView trackId={2} />);
    expect(await screen.findByText('متن این آهنگ پیدا نشد.')).toBeInTheDocument();
  });
});

describe('smart shuffle', () => {
  it('mixes suggestions in, marks them, and takes them out again', async () => {
    mockApi({ ...streams([1, 2, 3, 4, 5, 6]), 'POST /v1/recommendations/for-tracks': { items: recs, next_cursor: null } });
    usePlayer.setState({ queue: tracks, index: 0, current: tracks[0] ?? null, shuffle: true });

    await usePlayer.getState().setSmartShuffle(true);
    const { queue, smartIds, smart } = usePlayer.getState();
    expect(smart).toBe(true);
    expect(smartIds).toEqual([91, 92]);
    // One suggestion after every three of theirs.
    expect(queue.map((t) => t.id)).toEqual([1, 2, 3, 4, 91, 5, 6, 92]);

    await usePlayer.getState().setSmartShuffle(false);
    expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(usePlayer.getState().smartIds).toEqual([]);
  });

  it('cycles off → shuffle → smart → off', async () => {
    mockApi({ 'POST /v1/recommendations/for-tracks': { items: [], next_cursor: null } });
    usePlayer.setState({ queue: tracks, index: 0, current: tracks[0] ?? null });
    usePlayer.getState().cycleShuffle();
    expect(usePlayer.getState().shuffle).toBe(true);
    usePlayer.getState().cycleShuffle();
    await waitFor(() => expect(usePlayer.getState().smart).toBe(true));
    usePlayer.getState().cycleShuffle();
    expect(usePlayer.getState()).toMatchObject({ shuffle: false, smart: false });
  });
});

describe('gapless engine', () => {
  it('buffers the next track in the spare element and switches to it', async () => {
    mockApi(streams([2]));
    const first = audio();
    expect(allAudio()).toHaveLength(2);
    await preloadNext(makeTrack({ id: 2 }));
    expect(spareTrackId()).toBe(2);
    expect(takePreloaded(3)).toBeNull();
    const previous = takePreloaded(2);
    expect(previous).toBe(first);
    expect(audio()).not.toBe(first);
    expect(audio().src).toContain('/s/2');
    expect(spareTrackId()).toBeNull();
  });
});

describe('hide a song', () => {
  it('hides it and drops it from what is still to come', async () => {
    const api = mockApi({ ...authRoutes(), 'GET /v1/me/hidden': [], 'PUT /v1/tracks/3/hide': {}, 'GET /v1/playlists': [] });
    usePlayer.setState({ queue: tracks, index: 0, current: tracks[0] ?? null });
    renderApp(<TrackActions track={tracks[2] ?? null} open onClose={() => undefined} />);
    await userEvent.click(await screen.findByText('این آهنگ را نشانم نده'));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT' && c.url.endsWith('/v1/tracks/3/hide'))).toBe(true));
    await waitFor(() => expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1, 2, 4, 5, 6]));
  });
});

describe('screens', () => {
  it('This Is lists the artist essentials and credits Last.fm', async () => {
    mockApi({
      'GET /v1/artists/5/this-is': {
        artist: { id: 5, name: 'Googoosh', latin_name: null, tracks_count: 3, image_url: null },
        source: 'lastfm',
        items: tracks.slice(0, 2),
      },
      'POST /v1/tracks/thumbs': { items: {} },
    });
    renderApp(<ThisIsScreen />, { route: '/this-is/5', path: '/this-is/:id' });
    expect(await screen.findByText('این Googoosh است')).toBeInTheDocument();
    expect(screen.getByText(/ترتیب از Last.fm/)).toBeInTheDocument();
    expect(screen.getByText('Song 2')).toBeInTheDocument();
  });

  it('Blend invites a friend and shows the match', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/blends': [
        { id: 1, other_user_id: 8, other_name: 'Ali', playlist_id: 30, match_pct: 72, refreshed_at: '2026-09-27T10:00:00Z' },
      ],
      'POST /v1/blends/invite': { code: 'abc', share_url: 'https://t.me/bot?startapp=bl_abc' },
    });
    renderApp(<BlendScreen />, { route: '/blend', path: '/blend' });
    expect(await screen.findByText('تو + Ali')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /دعوت به بلند/ }));
    await waitFor(() => expect(opened[0]).toContain(encodeURIComponent('https://t.me/bot?startapp=bl_abc')));
  });

  it('Blend joins from an invite link', async () => {
    const api = mockApi({
      ...authRoutes(),
      'POST /v1/blends/join/xyz': { id: 2, other_user_id: 9, other_name: 'Sara', playlist_id: 31, match_pct: 40, refreshed_at: '2026-09-27T10:00:00Z' },
      'GET /v1/blends': [],
    });
    renderApp(<BlendScreen />, { route: '/blend/xyz', path: '/blend/:code' });
    await waitFor(() => expect(api.calls.some((c) => c.url.endsWith('/v1/blends/join/xyz'))).toBe(true));
  });

  it('playlists sort, filter and open folders', async () => {
    const base = {
      description: null,
      is_public: false,
      is_collaborative: false,
      share_slug: null,
      share_url: null,
      tracks_count: 1,
      duration_total: 100,
      is_owner: true,
      can_edit: true,
    };
    mockApi({
      ...authRoutes(),
      'GET /v1/playlists': [
        { ...base, id: 1, name: 'Zebra', kind: 'manual', updated_at: '2026-09-27T10:00:00Z', folder_id: null },
        { ...base, id: 2, name: 'Apple', kind: 'manual', updated_at: '2026-09-20T10:00:00Z', folder_id: null },
        { ...base, id: 3, name: 'Release Radar', kind: 'release_radar', updated_at: '2026-09-26T10:00:00Z', folder_id: null },
        { ...base, id: 4, name: 'Filed', kind: 'manual', updated_at: '2026-09-25T10:00:00Z', folder_id: 9 },
      ],
      'GET /v1/folders': [{ id: 9, name: 'Trips', playlists: 1 }],
    });
    renderApp(<Playlists />);
    await screen.findByText('Zebra');
    // Filed playlists live inside their folder.
    expect(screen.queryByText('Filed')).not.toBeInTheDocument();
    const names = () => screen.getAllByText(/^(Zebra|Apple|Release Radar)$/).map((node) => node.textContent);
    expect(names()).toEqual(['Zebra', 'Release Radar', 'Apple']);
    await userEvent.click(screen.getByRole('button', { name: 'الفبایی' }));
    expect(names()).toEqual(['Apple', 'Release Radar', 'Zebra']);
    await userEvent.click(screen.getByRole('button', { name: 'برای تو' }));
    expect(names()).toEqual(['Release Radar']);
    await userEvent.click(screen.getByRole('button', { name: 'همه' }));
    await userEvent.click(screen.getByText('Trips'));
    expect(await screen.findByText('Filed')).toBeInTheDocument();
  });

  it('playback settings are kept on the device', async () => {
    mockApi({
      ...authRoutes(),
      'GET /v1/me/notifications': { prefs: {} },
      'GET /v1/me/private-session': { private_until: null },
    });
    renderApp(<Settings />);
    const slider = await screen.findByRole('slider', { name: 'کراس‌فید' });
    // A range input is set, not typed into.
    slider.dispatchEvent(new Event('input', { bubbles: true }));
    await userEvent.click(screen.getByRole('button', { name: 'بیس بیشتر' }));
    expect(useAudioSettings.getState().eq).toBe('bass');
    expect(JSON.parse(localStorage.getItem('tmusic.audio') ?? '{}')).toMatchObject({ eq: 'bass' });
    expect(screen.getByText('جلسهٔ خصوصی')).toBeInTheDocument();
  });
});

describe('dj', () => {
  it('plays the set and introduces each segment', async () => {
    mockApi({
      ...streams([1, 2, 3]),
      'GET /v1/dj': {
        segments: [
          { kind: 'favorites', items: [tracks[0], tracks[1]] },
          { kind: 'discovery', items: [tracks[2]] },
        ],
      },
    });
    expect(await useDj.getState().start()).toBe(true);
    expect(usePlayer.getState().queue.map((t) => t.id)).toEqual([1, 2, 3]);
    expect(useUi.getState().toasts[0]?.text).toContain('Song 1');
    usePlayer.setState({ index: 2, current: tracks[2] ?? null });
    await waitFor(() => expect(useUi.getState().toasts.some((toast) => toast.text.includes('چیزهای تازه'))).toBe(true));
    useDj.getState().stop();
    expect(useDj.getState().on).toBe(false);
  });

  it('says when there is nothing to play yet', async () => {
    mockApi({ 'GET /v1/dj': { segments: [] } });
    expect(await useDj.getState().start()).toBe(false);
  });
});
