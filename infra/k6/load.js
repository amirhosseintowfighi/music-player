/**
 * Load test for the §9 target: 10,000 concurrent Mini App users.
 *
 *   k6 run -e BASE=https://api.example.com -e INIT_DATA_FILE=./initdata.txt infra/k6/load.js
 *
 * Shape of the traffic, from the real screens:
 *   - open the app (auth + me + home lists) once per session
 *   - browse the library and search a few times
 *   - start playback (stream ticket) and report the play
 * Streaming bytes are NOT pulled here: the edge serves those and would otherwise
 * saturate the load generator's own bandwidth long before the API breaks. The
 * separate `stream.js` scenario covers the edge with a small sample.
 *
 * Thresholds encode the budget from ARCHITECTURE §10: p95 API < 200 ms,
 * search < 100 ms, and essentially no 5xx.
 */
import { check, group, sleep } from 'k6';
import http from 'k6/http';
import { Counter, Trend } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://localhost:8000';
const VUS = Number(__ENV.VUS || 500);
const DURATION = __ENV.DURATION || '5m';

// initData strings, one per line; generate them with `python -m app.cli fake-initdata N`.
const initDataList = open(__ENV.INIT_DATA_FILE || './initdata.txt')
  .split('\n')
  .map((line) => line.trim())
  .filter(Boolean);

const authFailures = new Counter('auth_failures');
const searchLatency = new Trend('search_latency', true);
const homeLatency = new Trend('home_latency', true);

export const options = {
  scenarios: {
    browse: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: Math.round(VUS * 0.3) },
        { duration: '2m', target: VUS },
        { duration: DURATION, target: VUS },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{kind:api}': ['p(95)<200'],
    search_latency: ['p(95)<100'],
    home_latency: ['p(95)<300'],
    auth_failures: ['count<10'],
  },
};

function login() {
  const initData = initDataList[__VU % initDataList.length];
  const response = http.post(
    `${BASE}/v1/auth/telegram`,
    JSON.stringify({ init_data: initData }),
    { headers: { 'Content-Type': 'application/json' }, tags: { kind: 'api', name: 'auth' } },
  );
  if (response.status !== 200) {
    authFailures.add(1);
    return null;
  }
  return response.json('access_token');
}

export default function run() {
  const token = login();
  if (!token) {
    sleep(5);
    return;
  }
  const params = (name) => ({
    headers: { Authorization: `Bearer ${token}` },
    tags: { kind: 'api', name },
  });

  group('home', () => {
    const responses = http.batch([
      ['GET', `${BASE}/v1/me`, null, params('me')],
      ['GET', `${BASE}/v1/library/channels`, null, params('channels')],
      ['GET', `${BASE}/v1/library/tracks?limit=30`, null, params('tracks')],
      ['GET', `${BASE}/v1/discover`, null, params('discover')],
    ]);
    homeLatency.add(Math.max(...responses.map((response) => response.timings.duration)));
    check(responses[0], { 'me ok': (r) => r.status === 200 });
    check(responses[3], { 'discover ok': (r) => r.status === 200 });
  });

  sleep(2 + Math.random() * 3);

  group('search', () => {
    const terms = ['moein', 'معین', 'gogoosh', 'شب', 'hayedeh'];
    const term = terms[Math.floor(Math.random() * terms.length)];
    const response = http.get(
      `${BASE}/v1/search?q=${encodeURIComponent(term)}&scope=library&limit=20`,
      params('search'),
    );
    searchLatency.add(response.timings.duration);
    check(response, { 'search ok': (r) => r.status === 200 });
  });

  sleep(1 + Math.random() * 2);

  group('play', () => {
    const list = http.get(`${BASE}/v1/library/tracks?limit=10`, params('tracks'));
    if (list.status !== 200) return;
    const items = list.json('items') || [];
    if (items.length === 0) return;
    const track = items[Math.floor(Math.random() * items.length)];

    const ticket = http.get(`${BASE}/v1/tracks/${track.id}/stream`, params('stream'));
    check(ticket, { 'ticket ok': (r) => r.status === 200 });

    // Report the play the way the player does, without downloading the audio.
    http.post(
      `${BASE}/v1/history`,
      JSON.stringify({ track_id: track.id, duration_played: 180, completed: true, source: 'library' }),
      {
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        tags: { kind: 'api', name: 'history' },
      },
    );
  });

  sleep(3 + Math.random() * 5);
}
