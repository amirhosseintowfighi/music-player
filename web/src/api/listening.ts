/**
 * The listening features: artist essentials and follows, lyrics, hiding songs,
 * private session, long-track progress, folders, Blend, Daylist and the DJ.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { del, get, patch, post, put, type Artist, type Page, type Track } from './client';
import { playlistKeys } from './playlists';
import type { components } from './schema';

export type ThisIs = components['schemas']['ThisIsOut'];
export type Lyrics = components['schemas']['LyricsOut'];
export type Folder = components['schemas']['FolderOut'];
export type Blend = components['schemas']['BlendOut'];
export type BlendInvite = components['schemas']['BlendInviteOut'];
export type Daylist = components['schemas']['DaylistOut'];
export type Dj = components['schemas']['DjOut'];
export type DjSegment = components['schemas']['DjSegmentOut'];
export type FollowState = components['schemas']['FollowArtistOut'];
export type PrivateSession = components['schemas']['PrivateSessionOut'];
export type Hidden = components['schemas']['HiddenOut'];
export type Pin = components['schemas']['PinOut'];
export type Credits = components['schemas']['CreditsOut'];
export type Device = components['schemas']['DeviceOut'];
export type Heartbeat = components['schemas']['HeartbeatOut'];
export type ConnectCommand = components['schemas']['ConnectCommandOut'];

export const listeningKeys = {
  thisIs: (artistId: number) => ['this-is', artistId] as const,
  artistPage: (artistId: number) => ['artist', artistId, 'page'] as const,
  followed: ['me', 'artists'] as const,
  lyrics: (trackId: number) => ['lyrics', trackId] as const,
  hidden: ['me', 'hidden'] as const,
  related: (artistId: number) => ['artist', artistId, 'related'] as const,
  credits: (trackId: number) => ['credits', trackId] as const,
  pins: ['me', 'pins'] as const,
  privateSession: ['me', 'private-session'] as const,
  inProgress: ['me', 'in-progress'] as const,
  folders: ['folders'] as const,
  playlistRecs: (playlistId: number) => ['playlist', playlistId, 'recommendations'] as const,
  blends: ['blends'] as const,
  daylist: ['daylist'] as const,
  dj: ['dj'] as const,
};

// ── artists ──

export function useThisIs(artistId: number) {
  return useQuery({
    queryKey: listeningKeys.thisIs(artistId),
    queryFn: () => get<ThisIs>(`/v1/artists/${artistId}/this-is`),
    enabled: artistId > 0,
    staleTime: 10 * 60_000,
  });
}

export function useFollowArtist(artistId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (following: boolean) =>
      following
        ? del<FollowState>(`/v1/artists/${artistId}/follow`)
        : put<FollowState>(`/v1/artists/${artistId}/follow`, {}),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['artist', artistId] });
      void client.invalidateQueries({ queryKey: listeningKeys.followed });
    },
  });
}

export function useFollowedArtists() {
  return useQuery({ queryKey: listeningKeys.followed, queryFn: () => get<Artist[]>('/v1/me/artists') });
}

/** "Fans also like". */
export function useRelatedArtists(artistId: number) {
  return useQuery({
    queryKey: listeningKeys.related(artistId),
    queryFn: () => get<Artist[]>(`/v1/artists/${artistId}/related`),
    enabled: artistId > 0,
    staleTime: 30 * 60_000,
  });
}

export function useCredits(trackId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: listeningKeys.credits(trackId ?? 0),
    queryFn: () => get<Credits>(`/v1/tracks/${trackId}/credits`),
    enabled: enabled && Boolean(trackId),
    staleTime: 30 * 60_000,
  });
}

// ── pins ──

export function usePins() {
  return useQuery({ queryKey: listeningKeys.pins, queryFn: () => get<Pin[]>('/v1/me/pins') });
}

export const MAX_PINS = 4;

export function useTogglePin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, refId, pinned }: { kind: Pin['kind']; refId: number; pinned: boolean }) =>
      pinned ? del<Pin[]>(`/v1/me/pins/${kind}/${refId}`) : put<Pin[]>(`/v1/me/pins/${kind}/${refId}`, {}),
    onSuccess: (pins) => client.setQueryData(listeningKeys.pins, pins),
  });
}

// ── lyrics ──

export function useLyrics(trackId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: listeningKeys.lyrics(trackId ?? 0),
    queryFn: () => get<Lyrics>(`/v1/tracks/${trackId}/lyrics`),
    enabled: enabled && Boolean(trackId),
    staleTime: Infinity,
  });
}

export interface LyricLine {
  at: number;
  text: string;
}

/** "[mm:ss.xx] words" → timed lines, in order. Lines without a time are dropped. */
export function parseLrc(lrc: string): LyricLine[] {
  const lines: LyricLine[] = [];
  for (const raw of lrc.split(/\r?\n/)) {
    const stamps = [...raw.matchAll(/\[(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)\]/g)];
    if (stamps.length === 0) continue;
    const text = raw.replace(/\[[^\]]*\]/g, '').trim();
    for (const stamp of stamps) {
      lines.push({ at: Number(stamp[1]) * 60 + Number(stamp[2]), text });
    }
  }
  return lines.sort((a, b) => a.at - b.at);
}

/** Index of the line being sung at ``position`` (or -1 before the first). */
export function activeLine(lines: LyricLine[], position: number): number {
  let low = 0;
  let high = lines.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if ((lines[mid]?.at ?? Infinity) <= position) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

// ── hide, private session, progress ──

export const SNOOZE_DAYS = 30;

export function useHideTrack() {
  const client = useQueryClient();
  return useMutation({
    /** ``snooze``: gone for thirty days, then it may come back. */
    mutationFn: async ({ trackId, hidden, snooze = false }: { trackId: number; hidden: boolean; snooze?: boolean }) => {
      if (hidden) {
        await del<void>(`/v1/tracks/${trackId}/hide`);
        return null;
      }
      return put<Hidden>(`/v1/tracks/${trackId}/hide`, snooze ? { snooze_days: SNOOZE_DAYS } : {});
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: listeningKeys.hidden });
      void client.invalidateQueries({ queryKey: ['discover'] });
    },
  });
}

export function useHiddenTracks() {
  return useQuery({ queryKey: listeningKeys.hidden, queryFn: () => get<number[]>('/v1/me/hidden') });
}

export function usePrivateSession() {
  return useQuery({
    queryKey: listeningKeys.privateSession,
    queryFn: () => get<PrivateSession>('/v1/me/private-session'),
  });
}

export function useSetPrivateSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (on: boolean) => put<PrivateSession>('/v1/me/private-session', { on }),
    onSuccess: (data) => client.setQueryData(listeningKeys.privateSession, data),
  });
}

export function useInProgress() {
  return useQuery({
    queryKey: listeningKeys.inProgress,
    queryFn: () => get<Page<Track>>('/v1/me/in-progress'),
  });
}

export function saveProgress(trackId: number, positionS: number): Promise<unknown> {
  return put(`/v1/tracks/${trackId}/progress`, { position_s: Math.floor(positionS) });
}

export async function loadProgress(trackId: number): Promise<{ position_s: number; finished: boolean } | null> {
  const rows = await get<{ track_id: number; position_s: number; finished: boolean }[]>(
    `/v1/me/progress?ids=${trackId}`,
  );
  return rows[0] ?? null;
}

// ── recommendations for a set of tracks ──

export function fetchForTracks(trackIds: number[], limit = 20): Promise<Track[]> {
  return post<Page<Track>>(`/v1/recommendations/for-tracks?limit=${limit}`, { track_ids: trackIds.slice(0, 200) }).then(
    (page) => page.items,
  );
}

export function usePlaylistRecommendations(playlistId: number, enabled = true) {
  return useQuery({
    queryKey: listeningKeys.playlistRecs(playlistId),
    queryFn: () => get<Page<Track>>(`/v1/playlists/${playlistId}/recommendations?limit=10`),
    enabled: enabled && playlistId > 0,
  });
}

// ── folders ──

export function useFolders() {
  return useQuery({ queryKey: listeningKeys.folders, queryFn: () => get<Folder[]>('/v1/folders') });
}

export function useFolderActions() {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: listeningKeys.folders });
    void client.invalidateQueries({ queryKey: playlistKeys.all });
  };
  return {
    create: useMutation({ mutationFn: (name: string) => post<Folder>('/v1/folders', { name }), onSuccess: refresh }),
    rename: useMutation({
      mutationFn: ({ id, name }: { id: number; name: string }) => patch<Folder>(`/v1/folders/${id}`, { name }),
      onSuccess: refresh,
    }),
    remove: useMutation({ mutationFn: (id: number) => del<void>(`/v1/folders/${id}`), onSuccess: refresh }),
    file: useMutation({
      mutationFn: ({ playlistId, folderId }: { playlistId: number; folderId: number | null }) =>
        put<void>(`/v1/playlists/${playlistId}/folder`, { folder_id: folderId }),
      onSuccess: refresh,
    }),
  };
}

// ── Blend ──

export function useBlends() {
  return useQuery({ queryKey: listeningKeys.blends, queryFn: () => get<Blend[]>('/v1/blends') });
}

export function useBlendActions() {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: listeningKeys.blends });
    void client.invalidateQueries({ queryKey: playlistKeys.all });
  };
  return {
    invite: useMutation({ mutationFn: () => post<BlendInvite>('/v1/blends/invite', {}) }),
    join: useMutation({ mutationFn: (code: string) => post<Blend>(`/v1/blends/join/${code}`, {}), onSuccess: refresh }),
    refresh: useMutation({ mutationFn: (id: number) => post<Blend>(`/v1/blends/${id}/refresh`, {}), onSuccess: refresh }),
    leave: useMutation({ mutationFn: (id: number) => del<void>(`/v1/blends/${id}`), onSuccess: refresh }),
  };
}

// ── Daylist and DJ ──

export function useDaylist() {
  return useQuery({
    queryKey: listeningKeys.daylist,
    queryFn: () => get<Daylist>('/v1/daylist'),
    staleTime: 15 * 60_000,
  });
}

export function fetchDj(): Promise<Dj> {
  return get<Dj>('/v1/dj');
}

// ── Connect ──

export interface HeartbeatBody {
  device_id: string;
  name: string;
  kind: Device['kind'];
  state: { track_id: number | null; position_s: number; playing: boolean };
}

export function sendHeartbeat(body: HeartbeatBody): Promise<Heartbeat> {
  return post<Heartbeat>('/v1/connect/heartbeat', body);
}

export interface CommandBody {
  target: string;
  sender: string;
  action: ConnectCommand['action'];
  track_ids?: number[];
  index?: number;
  position_s?: number;
  playing?: boolean;
}

export function sendCommand(body: CommandBody): Promise<void> {
  return post<void>('/v1/connect/command', body);
}

export function forgetDevice(deviceId: string): Promise<void> {
  return del<void>(`/v1/connect/devices/${deviceId}`);
}
