// Performance budget from ARCHITECTURE section 10-2: the initial bundle must stay
// under 200 KB gzip. Fails the build when it does not.
import { gzipSync } from 'node:zlib';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { basename, join } from 'node:path';

const LIMIT_KB = 200;

function walk(path) {
  return readdirSync(path).flatMap((name) => {
    const full = join(path, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const entryHtml = readFileSync('dist/index.html', 'utf8');
// Scripts, stylesheets and modulepreload links in index.html make up the first paint;
// lazily imported chunks (the full player) do not.
const initial = walk('dist/assets')
  .filter((file) => !file.endsWith('.map'))
  .filter((file) => entryHtml.includes(basename(file)));

if (initial.length === 0) {
  console.error('✗ no entry assets found — did the build output change?');
  process.exit(1);
}

let total = 0;
for (const file of initial) {
  const size = gzipSync(readFileSync(file)).length;
  total += size;
  console.log(`  ${basename(file)}: ${(size / 1024).toFixed(1)} KB gz`);
}

const kb = total / 1024;
console.log(`initial bundle: ${kb.toFixed(1)} KB gzip (budget ${LIMIT_KB} KB)`);
if (kb > LIMIT_KB) {
  console.error(`✗ over budget by ${(kb - LIMIT_KB).toFixed(1)} KB`);
  process.exit(1);
}

// A bundle built without VITE_API_URL silently points every request at its own
// static host, which comes back as index.html and looks like a network error on a
// phone. Cheap to notice here; expensive to debug on a server.
if (!process.env.VITE_API_URL) {
  console.warn(
    '! VITE_API_URL was not set: this bundle calls its own origin.\n' +
      '  Fine for local dev; for a deployment build it with\n' +
      '  VITE_API_URL=https://api.<domain> npm run build',
  );
}
