/**
 * Edge streaming check: Range/206 correctness and first-byte latency under load.
 * Deliberately small (a few dozen VUs): this saturates bandwidth, not CPU, and the
 * point is to prove the cache and Range handling behave, not to move terabytes.
 *
 *   k6 run -e BASE=https://api.example.com -e TOKEN=<access-token> infra/k6/stream.js
 */
import { check, sleep } from 'k6';
import http from 'k6/http';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE || 'http://localhost:8000';
const TOKEN = __ENV.TOKEN || '';
const firstByte = new Trend('stream_ttfb', true);

export const options = {
  vus: Number(__ENV.VUS || 30),
  duration: __ENV.DURATION || '2m',
  thresholds: {
    stream_ttfb: ['p(95)<800'],
    checks: ['rate>0.99'],
  },
};

export default function run() {
  const auth = { headers: { Authorization: `Bearer ${TOKEN}` } };
  const list = http.get(`${BASE}/v1/library/tracks?limit=20`, auth);
  if (list.status !== 200) return;
  const items = list.json('items') || [];
  if (items.length === 0) return;
  const track = items[Math.floor(Math.random() * items.length)];

  const ticket = http.get(`${BASE}/v1/tracks/${track.id}/stream`, auth);
  if (ticket.status !== 200) return;
  const url = ticket.json('url');

  // The player always asks for a range; 206 with Content-Range is the contract.
  const chunk = http.get(url, { headers: { Range: 'bytes=0-262143' } });
  firstByte.add(chunk.timings.waiting);
  check(chunk, {
    'partial content': (r) => r.status === 206,
    'has content-range': (r) => Boolean(r.headers['Content-Range']),
    'accepts ranges': (r) => r.headers['Accept-Ranges'] === 'bytes',
  });

  // A seek into the middle must not re-download from zero.
  const seek = http.get(url, { headers: { Range: 'bytes=1048576-1310719' } });
  check(seek, { 'seek partial': (r) => r.status === 206 });

  sleep(5);
}
