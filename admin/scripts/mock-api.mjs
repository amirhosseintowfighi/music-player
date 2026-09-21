// Dev-only fake admin API so the panel can be run and reviewed without the backend.
//   node scripts/mock-api.mjs        (listens on :8788)
//   VITE_API_URL=http://localhost:8788 npm run dev
import { createServer } from 'node:http';

const PORT = Number(process.env.MOCK_PORT ?? 8788);

const me = {
  id: 1,
  tg_id: 123456789,
  role: 'owner',
  permissions: ['*'],
  first_name: 'Owner',
  is_active: true,
};

const users = Array.from({ length: 12 }, (_, index) => ({
  id: index + 1,
  tg_id: 900000 + index,
  username: index % 3 === 0 ? `user${index}` : null,
  first_name: ['سارا', 'علی', 'مریم', 'رضا'][index % 4],
  lang: index % 4 === 0 ? 'en' : 'fa',
  plan_code: index % 5 === 0 ? 'pro_monthly' : 'free',
  premium_until: index % 5 === 0 ? '2026-10-18T00:00:00Z' : null,
  is_banned: index === 7,
  ban_reason: index === 7 ? 'spam' : null,
  bot_blocked: index === 4,
  created_at: '2026-08-20T10:00:00Z',
  last_seen_at: '2026-09-18T09:30:00Z',
}));

const days = (n, make) =>
  Array.from({ length: n }, (_, index) => ({
    day: new Date(Date.now() - (n - 1 - index) * 86400000).toISOString().slice(0, 10),
    value: make(index),
  }));

const routes = {
  'GET /admin/me': () => me,
  'POST /admin/login': () => ({ access_token: 'mock-admin-token', expires_at: 0, me }),
  'GET /admin/overview': () => ({
    dau: 1240,
    wau: 4820,
    mau: 11300,
    new_users_today: 86,
    paying_users: 512,
    trials: 47,
    tracks: 184300,
    channels: 212,
    plays_today: 9840,
    revenue_30d: [
      { provider: 'zarinpal', currency: 'IRR', amount: 512000000 },
      { provider: 'stars', currency: 'XTR', amount: 4200 },
    ],
    conversion_pct: 4.5,
    churn_30d_pct: 2.1,
  }),
  'GET /admin/metrics/users': () => days(30, (i) => 40 + Math.round(Math.sin(i / 3) * 25) + i),
  'GET /admin/metrics/plays': () => days(30, (i) => 6000 + i * 120),
  'GET /admin/metrics/revenue': () => days(30, (i) => (i % 4 === 0 ? 14900000 : 4470000)),
  'GET /admin/retention': () =>
    Array.from({ length: 6 }, (_, index) => ({
      cohort: new Date(Date.now() - (5 - index) * 7 * 86400000).toISOString().slice(0, 10),
      size: 120 + index * 15,
      weeks: { 0: 120 + index * 15, 1: 70 + index * 5, 2: 45, 3: 30, 4: 22, 5: 18 },
    })),
  'GET /admin/users': () => ({ items: users, total: users.length }),
  'GET /admin/users/1': () => ({
    user: users[0],
    stats: { channels: 6, playlists: 4, likes: 132, plays: 2140, spent: 14900000 },
    subscription_status: 'active',
    subscription_expires_at: '2026-10-18T00:00:00Z',
  }),
  'GET /admin/payments/pending': () => [
    {
      id: 41,
      public_id: 'c0ffee',
      amount: 1490000,
      currency: 'IRR',
      plan_code: 'pro_monthly',
      receipt_file_id: 'AgACAgQAAxkBAAI...',
      review_due_at: '2026-09-19T09:00:00Z',
      created_at: '2026-09-18T09:00:00Z',
      user_id: 3,
      tg_id: 900003,
      first_name: 'مریم',
      username: 'maryam',
    },
  ],
  'GET /admin/reports': () => [
    {
      id: 9,
      entity_type: 'track',
      entity_id: 5512,
      reason: 'copyright',
      details: 'این آهنگ متعلق به ماست',
      status: 'open',
      resolution: null,
      due_at: '2026-09-20T09:00:00Z',
      created_at: '2026-09-18T09:00:00Z',
    },
  ],
  'GET /admin/broadcasts': () => [
    {
      id: 7,
      status: 'running',
      total: 11300,
      sent: 4200,
      failed: 12,
      blocked: 340,
      scheduled_at: null,
      started_at: '2026-09-18T09:00:00Z',
      finished_at: null,
      created_at: '2026-09-18T08:55:00Z',
    },
  ],
  'POST /admin/broadcasts/estimate': () => ({ total: 8421 }),
  'GET /admin/health': () => ({
    indexer_accounts: { total: 4, healthy: 3 },
    edges: { total: 2, healthy: 2 },
    channels: { indexing: 3, failed: 1 },
    queues: { payments_to_review: 1, open_reports: 1 },
    floodwait_24h_s: 420,
  }),
  'GET /admin/audit': () => [
    {
      id: 301,
      actor_type: 'admin',
      actor_id: 1,
      action: 'payment.paid',
      entity: 'payment',
      entity_id: '40',
      payload: { provider: 'card2card', amount: 1490000 },
      created_at: '2026-09-18T09:10:00Z',
    },
  ],
};

createServer((request, response) => {
  const url = new URL(request.url ?? '/', 'http://localhost');
  const key = `${request.method} ${url.pathname}`;
  response.setHeader('Access-Control-Allow-Origin', '*');
  response.setHeader('Access-Control-Allow-Headers', '*');
  response.setHeader('Access-Control-Allow-Methods', '*');
  if (request.method === 'OPTIONS') {
    response.writeHead(204).end();
    return;
  }
  const handler = routes[key];
  if (!handler) {
    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: { code: 'not_found', message: key, details: {} } }));
    return;
  }
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(handler()));
}).listen(PORT, () => console.log(`mock admin API on http://localhost:${PORT}`));
