// Dev-only fake API so the Mini App can be run and screenshotted without the backend.
//   node scripts/mock-api.mjs     (listens on :8787)
//   VITE_API_URL=http://localhost:8787 npm run dev
import { createServer } from 'node:http';

const PORT = Number(process.env.MOCK_PORT ?? 8787);

const artists = [
  { id: 1, name: 'معین', role: 'primary' },
  { id: 2, name: 'گوگوش', role: 'primary' },
  { id: 3, name: 'هایده', role: 'primary' },
  { id: 4, name: 'محسن یگانه', role: 'primary' },
  { id: 5, name: 'داریوش', role: 'primary' },
  { id: 6, name: 'شادمهر عقیلی', role: 'primary' },
  { id: 7, name: 'Queen', role: 'primary' },
];
const titles = [
  'شب بارونی',
  'پل',
  'گل سنگم',
  'بهت قول میدم',
  'نون و پنیر',
  'تقدیر',
  'Bohemian Rhapsody',
];
const tracks = titles.map((title, index) => ({
  id: index + 1,
  title,
  artists: [artists[index % artists.length]],
  album: index === 1 ? 'Golden Hits' : null,
  duration: 180 + index * 23,
  language: index === 6 ? 'en' : 'fa',
  year: null,
  has_thumb: false,
  channels_count: (index % 4) + 1,
  playable: true,
}));

const channels = [
  {
    id: 1, username: 'persianhits', title: 'Persian Hits', status: 'indexing',
    status_reason: null, progress_pct: 62, tracks_count: 480, subscribers_count: 12,
    is_featured: false, category_id: 1, source: 'mtproto', avatar_url: null,
  },
  {
    id: 2, username: 'goldenoldies', title: 'Golden Oldies', status: 'active',
    status_reason: null, progress_pct: 100, tracks_count: 1240, subscribers_count: 40,
    is_featured: false, category_id: 7, source: 'mtproto', avatar_url: null,
  },
];
const featured = [
  { ...channels[1], id: 9, username: 'rapfarsi', title: 'Rap Farsi', is_featured: true, tracks_count: 1200 },
  { ...channels[1], id: 10, username: 'instrumental', title: 'بی‌کلام', is_featured: true, tracks_count: 340 },
];

const me = {
  id: 1, tg_id: 12345, username: 'sara', first_name: 'سارا', lang: 'fa', plan: 'free',
  premium_until: null, features: ['discover_weekly'],
  limits: { channels: 3, playlists: 5, daily_plays: 60 }, referral_code: 'SARA2026',
};

const routes = {
  'POST /v1/auth/telegram': () => ({
    access_token: 'mock-access',
    access_expires_at: Math.floor(Date.now() / 1000) + 900,
    refresh_token: 'mock-refresh',
    refresh_expires_at: Math.floor(Date.now() / 1000) + 86400,
    token_type: 'bearer',
    start_param: null,
    me,
  }),
  'POST /v1/auth/refresh': () => routes['POST /v1/auth/telegram'](),
  'GET /v1/me': () => me,
  'GET /v1/library/channels': () => channels.map((c) => ({ ...c, added_at: new Date().toISOString() })),
  'GET /v1/library/tracks': () => ({ items: tracks, next_cursor: null }),
  'GET /v1/library/artists': () => ({
    items: artists.map((a) => ({ id: a.id, name: a.name, latin_name: null, tracks_count: 12 })),
    next_cursor: null,
  }),
  'GET /v1/library/albums': () => [
    { album: 'Golden Hits', artist_id: 2, artist_name: 'گوگوش', tracks_count: 9 },
  ],
  'GET /v1/channels/featured': () => ({ items: featured, next_cursor: null }),
  'GET /v1/channels/categories': () => [
    { id: 1, slug: 'pop-fa', name_fa: 'پاپ فارسی', name_en: 'Persian pop' },
  ],
  'GET /v1/search': () => ({ items: tracks.slice(0, 4), total: 4, offset: 0, limit: 30, degraded: false }),
  'GET /v1/search/suggest': () => ({ history: ['moein', 'گوگوش'], tracks: tracks.slice(0, 3) }),
  'POST /v1/tracks/thumbs': () => ({ items: {} }),
  'GET /v1/me/profile': () => ({
    user_id: 7,
    first_name: 'سارا',
    username: 'sara',
    is_pro: false,
    followers: 12,
    following: 31,
    playlists: 4,
    tracks_played: 842,
    joined_at: '2026-02-11T00:00:00Z',
    is_me: true,
    is_following: false,
    top_artists: [
      { id: 1, name: 'معین', plays: 120 },
      { id: 2, name: 'گوگوش', plays: 96 },
      { id: 4, name: 'محسن یگانه', plays: 54 },
    ],
    top_tracks: tracks.slice(0, 4),
    public_playlists: [{ id: 3, name: 'شب‌های بارونی', tracks_count: 18, share_slug: 'abc' }],
  }),
  'GET /v1/social/feed': () =>
    tracks.slice(0, 3).map((track, index) => ({
      user_id: 40 + index,
      first_name: ['علی', 'مریم', 'رضا'][index],
      username: null,
      played_at: new Date(Date.now() - index * 3600000).toISOString(),
      track,
    })),
  'GET /v1/me/wrapped': () => ({
    year: 2026,
    plays: 842,
    minutes: 2317,
    unique_tracks: 214,
    active_days: 151,
    top_artists: [
      { id: 1, name: 'معین', plays: 120 },
      { id: 2, name: 'گوگوش', plays: 96 },
    ],
    top_tracks: [{ id: 1, title: 'شب بارونی', plays: 40 }],
    busiest_day: { day: '2026-03-21', plays: 37 },
    tracks: tracks.slice(0, 3),
  }),
  'GET /v1/me/notifications': () => ({
    prefs: {
      new_tracks: true,
      digest: true,
      discover_ready: true,
      sub_expiry: true,
      payment: true,
      system: true,
    },
  }),
  'GET /v1/plans': () => ({
    plans: [
      { code: 'free', name: 'رایگان', period_days: null, prices: {}, limits: { channels: 3 }, features: [], is_current: true },
      {
        code: 'pro_monthly',
        name: 'پرو ماهانه',
        period_days: 30,
        prices: { IRR: 1490000, XTR: 150 },
        limits: { channels: -1 },
        features: ['offline', 'share_playlist'],
        is_current: false,
      },
      {
        code: 'pro_yearly',
        name: 'پرو سالانه',
        period_days: 365,
        prices: { IRR: 14900000, XTR: 1500 },
        limits: { channels: -1 },
        features: ['offline', 'share_playlist'],
        is_current: false,
      },
    ],
    providers: [
      { code: 'stars', currency: 'XTR', kind: 'telegram_invoice' },
      { code: 'zarinpal', currency: 'IRR', kind: 'redirect' },
      { code: 'card2card', currency: 'IRR', kind: 'instructions' },
    ],
    trial_available: true,
    trial_days: 7,
  }),
  'GET /v1/me/subscription': () => ({ plan_code: 'free', status: 'free', days_left: 0, auto_renew: false }),
  'GET /v1/me/payments': () => [],
  'GET /v1/payments/discount': () => ({
    valid: true,
    amount: 1192000,
    original_amount: 1490000,
    discount_amount: 298000,
    currency: 'IRR',
    reason: null,
  }),
  'POST /v1/payments/checkout': () => ({
    payment_id: 'mock-payment',
    provider: 'card2card',
    amount: 1490000,
    currency: 'IRR',
    kind: 'instructions',
    url: null,
    payload: { card_number: '6037-9900-0000-0000', holder_name: 'علی رضایی', reference: 'ab12cd34' },
  }),
  'POST /v1/payments/trial': () => ({
    plan_code: 'pro_monthly',
    status: 'trialing',
    source: 'trial',
    started_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
    grace_until: null,
    days_left: 7,
    auto_renew: false,
  }),
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
  const handler =
    routes[key] ??
    (url.pathname.endsWith('/stream')
      ? () => ({
          url: 'about:blank',
          thumb_url: null,
          expires_at: Math.floor(Date.now() / 1000) + 300,
          size: 1,
          mime: 'audio/mpeg',
        })
      : null);
  if (!handler) {
    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: { code: 'not_found', message: key, details: {} } }));
    return;
  }
  let body = '';
  request.on('data', (chunk) => {
    body += chunk;
  });
  request.on('end', () => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(handler(body ? JSON.parse(body) : undefined)));
  });
}).listen(PORT, () => console.log(`mock api on http://localhost:${PORT}`));
