import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { saveToken, setUnauthorizedHandler } from '@/api/client';
import { Candidates } from '@/screens/Candidates';
import { Crawler } from '@/screens/Crawler';

import { mockApi, renderPanel } from './helpers';

const candidate = {
  id: 11,
  username: 'persian_music',
  title: 'Persian Music',
  source: 'crawl_mention',
  status: 'pending',
  score: 72.5,
  tracks_estimate: 3600,
  audio_ratio: 0.9,
  posts_per_day: 6.5,
  subscribers: 12000,
  mention_count: 3,
  requested_by: 2,
  discovered_from_channel_id: 4,
  probed_at: '2026-09-19T09:00:00Z',
  reject_reason: null,
  created_at: '2026-09-18T09:00:00Z',
};

const weak = { ...candidate, id: 12, username: 'random_chan', score: 8, requested_by: 0 };

const crawlChannel = {
  id: 3,
  username: 'musicirani',
  title: 'Music Irani',
  status: 'indexing',
  crawl_status: 'error',
  progress_pct: 45,
  oldest_crawled_msg_id: 500,
  newest_crawled_msg_id: 1200,
  tracks_count: 320,
  last_crawl_at: '2026-09-19T08:00:00Z',
  next_crawl_at: '2026-09-19T09:00:00Z',
  crawl_interval_sec: 3600,
  fail_count: 2,
  crawl_error: '502 from telegram',
  extraction_rate: 0.88,
  preview_available: true,
};

const parserHealthy = {
  days: [
    { day: '2026-09-19', pages: 40, messages: 800, audio_items: 700, empty_pages: 1, rate: 0.875 },
    { day: '2026-09-18', pages: 38, messages: 760, audio_items: 690, empty_pages: 0, rate: 0.9 },
  ],
  today_rate: 0.875,
  expected_rate: 0.9,
  alert: false,
};

const resolverOk = {
  unresolved: 12000,
  pending: 3,
  failed: 7,
  resolved: 800,
  breaker_open: false,
  consecutive_failures: 0,
  accounts: 1,
};

const crawlerHealth = {
  channels: 40,
  running: 2,
  errored: 1,
  preview_disabled: 3,
  completed: 34,
  due: 5,
  unresolved_tracks: 12000,
};

beforeEach(() => {
  saveToken(null);
  setUnauthorizedHandler(null);
  vi.unstubAllGlobals();
});

describe('candidate queue', () => {
  it('shows the queue with its score and stats', async () => {
    mockApi({ 'GET /admin/candidates': { items: [candidate, weak], total: 2 } });
    renderPanel(<Candidates />);

    expect(await screen.findByText('@persian_music')).toBeInTheDocument();
    expect(screen.getByText('۷۳')).toBeInTheDocument(); // rounded score
    expect(screen.getAllByText('~۳٬۶۰۰')).toHaveLength(2); // both rows carry the estimate
    expect(screen.getAllByText('۹۰٪')[0]).toBeInTheDocument();
    expect(screen.getAllByText(/منشن در کرال/)).toHaveLength(2);
    // The preview link is how an admin actually judges a channel.
    expect(screen.getByText('@persian_music').closest('a')).toHaveAttribute(
      'href',
      'https://t.me/s/persian_music',
    );
  });

  it('approves one candidate', async () => {
    const api = mockApi({
      'GET /admin/candidates': { items: [candidate], total: 1 },
      'POST /admin/candidates/11/approve': { channel_id: 99 },
    });
    renderPanel(<Candidates />);

    await userEvent.click(await screen.findByRole('button', { name: 'تأیید' }));
    await waitFor(() =>
      expect(
        api.calls.some((call) => call.url.endsWith('/admin/candidates/11/approve')),
      ).toBe(true),
    );
  });

  it('rejects a selection in one call', async () => {
    const api = mockApi({
      'GET /admin/candidates': { items: [candidate, weak], total: 2 },
      'POST /admin/candidates/reject': { rejected: 2 },
    });
    renderPanel(<Candidates />);

    await userEvent.click(await screen.findByLabelText('انتخاب persian_music'));
    await userEvent.click(screen.getByLabelText('انتخاب random_chan'));
    await userEvent.click(screen.getByRole('button', { name: /رد گروهی/ }));

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.endsWith('/admin/candidates/reject'));
      expect(call?.body).toMatchObject({ ids: [11, 12] });
    });
  });

  it('imports a pasted list and reports what happened', async () => {
    const api = mockApi({
      'GET /admin/candidates': { items: [], total: 0 },
      'POST /admin/channels/import': {
        created: 2,
        existing: 1,
        blocked: 0,
        invalid: ['oops'],
      },
    });
    renderPanel(<Candidates />);

    await userEvent.type(screen.getByLabelText('لیست کانال‌ها'), '@one\n@two');
    await userEvent.click(screen.getByRole('button', { name: 'وارد کن' }));

    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.endsWith('/admin/channels/import'));
      expect(call?.body).toMatchObject({ text: '@one\n@two' });
    });
    expect(await screen.findByText(/۲ اضافه شد/)).toBeInTheDocument();
    expect(screen.getByText(/نامعتبر: oops/)).toBeInTheDocument();
  });
});

describe('crawler page', () => {
  const base = {
    'GET /admin/crawler': crawlerHealth,
    'GET /admin/crawler/channels': [crawlChannel],
    'GET /admin/crawler/parser': parserHealthy,
    'GET /admin/crawler/resolver': resolverOk,
  };

  it('shows per-channel crawl state including the error', async () => {
    mockApi(base);
    renderPanel(<Crawler />);

    expect(await screen.findByText('@musicirani')).toBeInTheDocument();
    expect(screen.getAllByText('خطا').length).toBeGreaterThan(1); // filter + badge
    expect(screen.getByText('۴۵٪')).toBeInTheDocument();
    expect(screen.getByText(/502 from telegram/)).toBeInTheDocument();
    expect(screen.getByText('۵۰۰ … ۱٬۲۰۰')).toBeInTheDocument();
  });

  it('asks for a full re-crawl when the button says so', async () => {
    const api = mockApi({ ...base, 'POST /admin/crawler/channels/3/recrawl': {} });
    renderPanel(<Crawler />);

    await userEvent.click(await screen.findByRole('button', { name: 'از ابتدا' }));
    await waitFor(() => {
      const call = api.calls.find((entry) => entry.url.includes('/recrawl'));
      expect(call?.url).toContain('full=true');
    });

    await userEvent.click(screen.getByRole('button', { name: 'کرال مجدد' }));
    await waitFor(() => {
      const recrawls = api.calls.filter((entry) => entry.url.includes('/recrawl'));
      expect(recrawls).toHaveLength(2);
      expect(recrawls[1]?.url).toContain('full=false');
    });
  });

  it('warns loudly when the parser stops extracting', async () => {
    mockApi({
      ...base,
      'GET /admin/crawler/parser': {
        ...parserHealthy,
        today_rate: 0.02,
        expected_rate: 0.9,
        alert: true,
      },
    });
    renderPanel(<Crawler />);

    expect(await screen.findByText(/افت ناگهانی استخراج/)).toBeInTheDocument();
    expect(screen.getByText('۲٪')).toBeInTheDocument();
  });

  it('shows the resolver breaker when it is open', async () => {
    mockApi({
      ...base,
      'GET /admin/crawler/resolver': {
        ...resolverOk,
        breaker_open: true,
        consecutive_failures: 5,
      },
    });
    renderPanel(<Crawler />);

    expect(await screen.findByText(/مدار باز است/)).toBeInTheDocument();
  });
});
