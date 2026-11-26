/** Wake-up alarms (0020). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { components } from './schema';
import { del, get, patch, post } from './client';

export type Alarm = components['schemas']['AlarmOut'];
export type AlarmInput = components['schemas']['AlarmIn'];
export type AlarmPatch = components['schemas']['AlarmPatchIn'];
export type Wake = components['schemas']['WakeOut'];

const key = ['alarms'] as const;

export function useAlarms() {
  return useQuery({ queryKey: key, queryFn: () => get<Alarm[]>('/v1/alarms') });
}

export function useWake(id: number) {
  return useQuery({
    queryKey: ['alarms', 'wake', id],
    queryFn: () => get<Wake>(`/v1/alarms/${id}/wake`),
    enabled: Number.isFinite(id) && id > 0,
    staleTime: Infinity,
  });
}

export function useAlarmActions() {
  const client = useQueryClient();
  const done = () => client.invalidateQueries({ queryKey: key });
  return {
    create: useMutation({ mutationFn: (body: AlarmInput) => post<Alarm>('/v1/alarms', body), onSuccess: done }),
    update: useMutation({
      mutationFn: ({ id, ...body }: AlarmPatch & { id: number }) => patch<Alarm>(`/v1/alarms/${id}`, body),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => del<void>(`/v1/alarms/${id}`), onSuccess: done }),
  };
}

/** The browser's offset from UTC in minutes, east positive (Tehran = 210). */
export function localOffset(date = new Date()): number {
  return -date.getTimezoneOffset() || 0;
}

export const ALL_DAYS = 127;
export const WEEKDAYS = 31;
