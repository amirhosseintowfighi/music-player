// Digital Asset Links for the Android app (a Trusted Web Activity).
//
// Android only hides the browser bar in the app when this site says, at
// /.well-known/assetlinks.json, that the app signed with these keys is its own.
// Written into dist/ at build time from the environment, and only when a key is
// configured: a wrong file is worse than none (the app would show the URL bar).
//
//   ANDROID_PACKAGE        the app id (default studio.virgule.noax)
//   ANDROID_CERT_SHA256    signing certificate fingerprint(s), comma-separated,
//                          "AB:CD:…" as printed by the Android build workflow
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const fingerprints = (process.env.ANDROID_CERT_SHA256 ?? '')
  .split(',')
  .map((f) => f.trim().toUpperCase())
  .filter(Boolean);
const pkg = (process.env.ANDROID_PACKAGE ?? 'studio.virgule.noax').trim();

if (fingerprints.length === 0) {
  console.log('assetlinks: ANDROID_CERT_SHA256 not set, skipped (the Android app would show a URL bar)');
} else {
  const bad = fingerprints.filter((f) => !/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/.test(f));
  if (bad.length) {
    console.error(`assetlinks: not a SHA-256 fingerprint: ${bad.join(', ')}`);
    process.exit(1);
  }
  const statement = [
    {
      relation: ['delegate_permission/common.handle_all_urls'],
      target: { namespace: 'android_app', package_name: pkg, sha256_cert_fingerprints: fingerprints },
    },
  ];
  const out = join(root, 'dist', '.well-known', 'assetlinks.json');
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, `${JSON.stringify(statement, null, 2)}\n`);
  console.log(`assetlinks: ${pkg} with ${fingerprints.length} key(s) → ${out}`);
}
