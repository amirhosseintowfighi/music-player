import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  get,
  patch,
  post,
  put,
  qs,
  type AuditEntry,
  type Broadcast,
  type CandidatePage,
  type CrawlChannel,
  type CrawlerHealth,
  type ImportResult,
  type MetadataRow,
  type ParserHealth,
  type ResolverStatus,
  type Health,
  type MetricPoint,
  type Overview,
  type PendingPayment,
  type Report,
  type UserDetail,
  type UsersPage,
} from './client';

export const keys = {
  overview: ['overview'] as const,
  metric: (metric: string, days: number) => ['metric', metric, days] as const,
  retention: ['retention'] as const,
  users: (query: string, plan: string, offset: number) => ['users', query, plan, offset] as const,
  user: (id: number) => ['user', id] as const,
  payments: ['payments', 'pending'] as const,
  reports: ['reports'] as const,
  broadcasts: ['broadcasts'] as const,
  progress: (id: number) => ['broadcast', id] as const,
  audit: (filters: Record<string, string>) => ['audit', filters] as const,
  health: ['health'] as const,
  candidates: (status: string) => ['candidates', status] as const,
  crawler: ['crawler'] as const,
  crawlChannels: (status: string) => ['crawl-channels', status] as const,
  parser: ['parser'] as const,
  resolver: ['resolver'] as const,
  metadata: (source: string) => ['metadata', source] as const,
};

export function useOverview() {
  return useQuery({ queryKey: keys.overview, queryFn: () => get<Overview>('/admin/overview') });
}

export function useMetric(metric: 'users' | 'plays' | 'revenue', days = 30) {
  return useQuery({
    queryKey: keys.metric(metric, days),
    queryFn: () => get<MetricPoint[]>(`/admin/metrics/${metric}${qs({ days })}`),
  });
}

export interface Cohort {
  cohort: string;
  size: number;
  weeks: Record<string, number>;
}

export function useRetention() {
  return useQuery({
    queryKey: keys.retention,
    queryFn: () => get<Cohort[]>('/admin/retention?weeks=6'),
  });
}

export function useUsers(query: string, plan: string, offset: number) {
  return useQuery({
    queryKey: keys.users(query, plan, offset),
    queryFn: () => get<UsersPage>(`/admin/users${qs({ q: query, plan, offset, limit: 25 })}`),
    placeholderData: (previous) => previous,
  });
}

export function useUser(id: number) {
  return useQuery({
    queryKey: keys.user(id),
    queryFn: () => get<UserDetail>(`/admin/users/${id}`),
    enabled: id > 0,
  });
}

export function useBanUser(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { banned: boolean; reason: string }) =>
      post(`/admin/users/${id}/ban`, input),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.user(id) });
      void client.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

export function useGift(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (days: number) => post<UserDetail>(`/admin/users/${id}/gift${qs({ days })}`),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.user(id) }),
  });
}

export function useImpersonate(id: number) {
  return useMutation({
    mutationFn: () => post<{ access_token: string; user_id: number }>(
      `/admin/users/${id}/impersonate`,
    ),
  });
}

export function usePendingPayments() {
  return useQuery({
    queryKey: keys.payments,
    queryFn: () => get<PendingPayment[]>('/admin/payments/pending'),
    refetchInterval: 30_000,
  });
}

export function useReviewPayment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: number; approve: boolean; reason?: string }) =>
      post(
        input.approve
          ? `/admin/payments/${input.id}/approve`
          : `/admin/payments/${input.id}/reject${qs({ reason: input.reason ?? '' })}`,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.payments }),
  });
}

export function useReports(status: string) {
  return useQuery({
    queryKey: [...keys.reports, status],
    queryFn: () => get<Report[]>(`/admin/reports${qs({ status })}`),
  });
}

export function useResolveReport() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      id: number;
      status: 'in_review' | 'actioned' | 'dismissed';
      resolution: string;
      hide_entity: boolean;
    }) => post(`/admin/reports/${input.id}/resolve`, input),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.reports }),
  });
}

export function useBroadcasts() {
  return useQuery({
    queryKey: keys.broadcasts,
    queryFn: () => get<Broadcast[]>('/admin/broadcasts'),
    refetchInterval: 10_000, // live progress while one is running
  });
}

export function useEstimate() {
  return useMutation({
    mutationFn: (target: Record<string, unknown>) =>
      post<{ total: number }>('/admin/broadcasts/estimate', target),
  });
}

export function useCreateBroadcast() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: Record<string, unknown>) => post<Broadcast>('/admin/broadcasts', input),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.broadcasts }),
  });
}

export function useBroadcastStatus() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: number; status: string }) =>
      post<Broadcast>(`/admin/broadcasts/${input.id}/status${qs({ status: input.status })}`),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.broadcasts }),
  });
}

export function useAudit(filters: { entity?: string; action?: string }) {
  return useQuery({
    queryKey: keys.audit(filters as Record<string, string>),
    queryFn: () => get<AuditEntry[]>(`/admin/audit${qs({ ...filters, limit: 100 })}`),
  });
}

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => get<Health>('/admin/health'),
    refetchInterval: 30_000,
  });
}

export interface PlanLimits {
  code: string;
  limits: Record<string, number | boolean>;
}

export function usePlanLimits() {
  return useQuery({
    queryKey: ['plan-limits'],
    queryFn: () => get<PlanLimits[]>('/admin/plans'),
  });
}

export function useSetting(key: string) {
  return useQuery({
    queryKey: ['setting', key],
    queryFn: () => get<{ key: string; value: unknown }>(`/admin/settings/${key}`),
  });
}

export function useSetFlag() {
  return useMutation({
    mutationFn: (input: { key: string; value: unknown }) =>
      put(`/admin/flags/${input.key}`, { value: input.value }),
  });
}

export function useSetSetting() {
  return useMutation({
    mutationFn: (input: { key: string; value: unknown }) =>
      put(`/admin/settings/${input.key}`, { value: input.value }),
  });
}

export function usePatchPlan() {
  return useMutation({
    mutationFn: (input: { code: string; changes: Record<string, unknown> }) =>
      patch(`/admin/plans/${input.code}`, input.changes),
  });
}

export function usePatchProvider() {
  return useMutation({
    mutationFn: (input: { code: string; changes: Record<string, unknown> }) =>
      patch(`/admin/providers/${input.code}`, input.changes),
  });
}

// ── channel discovery and crawler health (ADR-002 §5) ────────────────────────

export function useCandidates(status: string) {
  return useQuery({
    queryKey: keys.candidates(status),
    queryFn: () => get<CandidatePage>(`/admin/candidates${qs({ status, limit: 100 })}`),
  });
}

export function useReviewCandidate() {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ['candidates'] });
  return useMutation({
    mutationFn: (input: { approve: boolean; ids: number[]; reason?: string }) =>
      input.approve
        ? post(`/admin/candidates/${input.ids[0]}/approve`)
        : post('/admin/candidates/reject', { ids: input.ids, reason: input.reason ?? '' }),
    onSuccess: () => {
      void refresh();
      void client.invalidateQueries({ queryKey: keys.crawler });
    },
  });
}

export function useImportChannels() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => post<ImportResult>('/admin/channels/import', { text }),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.crawler }),
  });
}

export function useCrawlerHealth() {
  return useQuery({
    queryKey: keys.crawler,
    queryFn: () => get<CrawlerHealth>('/admin/crawler'),
    refetchInterval: 30_000,
  });
}

export function useCrawlChannels(status: string) {
  return useQuery({
    queryKey: keys.crawlChannels(status),
    queryFn: () => get<CrawlChannel[]>(`/admin/crawler/channels${qs({ status, limit: 100 })}`),
    refetchInterval: 30_000,
  });
}

export function useRecrawl() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: number; full: boolean }) =>
      post(`/admin/crawler/channels/${input.id}/recrawl${qs({ full: input.full })}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['crawl-channels'] }),
  });
}

export function useParserHealth() {
  return useQuery({
    queryKey: keys.parser,
    queryFn: () => get<ParserHealth>('/admin/crawler/parser'),
    refetchInterval: 60_000,
  });
}

export function useResolverStatus() {
  return useQuery({
    queryKey: keys.resolver,
    queryFn: () => get<ResolverStatus>('/admin/crawler/resolver'),
    refetchInterval: 30_000,
  });
}

export function useMetadataQueue(source: string) {
  return useQuery({
    queryKey: keys.metadata(source),
    queryFn: () => get<MetadataRow[]>(`/admin/metadata/queue${qs({ source, limit: 50 })}`),
  });
}

export function useFixMetadata() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: number; title?: string; artist?: string; apply_to_artist?: boolean }) =>
      post<{ tracks: number }>(`/admin/metadata/${input.id}/fix`, {
        title: input.title,
        artist: input.artist,
        apply_to_artist: input.apply_to_artist ?? false,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['metadata'] }),
  });
}
