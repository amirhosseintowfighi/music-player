// Checks that nginx CSP connect-src covers the same origins as the API's CORS / Vite proxy.
// Minimal Fase 1 version: reads .env CORS_ORIGINS and solo.conf.template connect-src and web vite proxy.
// Usage: node scripts/check-cors.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function readEnvCORS() {
  const envPath = path.join(root, '.env');
  const examplePath = path.join(root, '.env.example');
  let text = '';
  if (fs.existsSync(envPath)) text = fs.readFileSync(envPath, 'utf8');
  else if (fs.existsSync(examplePath)) text = fs.readFileSync(examplePath, 'utf8');
  const m = text.match(/^\s*CORS_ORIGINS\s*=\s*(.*)\s*$/m);
  if (!m) return null;
  try { return JSON.parse(m[1].trim()); } catch { return null; }
}

function readNginxConnectSrc() {
  const p = path.join(root, 'infra/nginx/solo.conf.template');
  if (!fs.existsSync(p)) return [];
  const text = fs.readFileSync(p, 'utf8');
  const matches = [...text.matchAll(/connect-src\s+([^";]+)/g)];
  return matches.map((m) => m[1].trim());
}

let failed = false;

const corsOrigins = readEnvCORS();
const connectSrcs = readNginxConnectSrc();

// 1) Nginx must allow api.${DOMAIN} and apex https://${DOMAIN} (vite fallback + web CSP)
const requiredTokens = ['https://api.${DOMAIN}', 'https://${DOMAIN}'];
for (const tok of requiredTokens) {
  if (!connectSrcs.some((cs) => cs.includes(tok))) {
    console.error(`✗ solo.conf.template missing connect-src token: ${tok}`);
    failed = true;
  } else {
    console.log(`✓ connect-src has ${tok}`);
  }
}

// 2) web/vite.config proxy must target /v1 to api:8000 fallback — already in vite.config proxy; check file exists
const viteWeb = path.join(root, 'web/vite.config.ts');
const viteMini = path.join(root, 'miniapp/vite.config.ts');
for (const p of [viteWeb, viteMini]) {
  if (!fs.existsSync(p)) { console.error(`✗ missing ${p}`); failed = true; continue; }
  const t = fs.readFileSync(p, 'utf8');
  if (!t.includes("'/v1'") && !t.includes('"/v1"')) {
    console.error(`✗ ${path.relative(root, p)} missing /v1 proxy`);
    failed = true;
  } else console.log(`✓ ${path.relative(root, p)} has /v1 proxy`);
}

// 3) Info: CORS_ORIGINS value (if set)
if (corsOrigins) console.log(`· CORS_ORIGINS = ${JSON.stringify(corsOrigins)}`);
else console.log('· CORS_ORIGINS not set in .env (using defaults / empty)');

if (failed) {
  console.error('check-cors: FAILED');
  process.exit(1);
}
console.log('check-cors: OK');
