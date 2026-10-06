// Generates all PWA icons from web/public/brand/noax.jpg — idempotent.
// Usage: node scripts/generate-icons.mjs
// Requires: sharp (devDependency in web/ and miniapp/)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = path.join(root, 'web', 'public', 'brand', 'noax.jpg');

if (!fs.existsSync(src)) {
  console.error(`✗ source missing: ${src}`);
  process.exit(1);
}

function loadSharp() {
  const require = createRequire(import.meta.url);
  const candidates = [
    'sharp',
    path.join(root, 'web', 'node_modules', 'sharp'),
    path.join(root, 'miniapp', 'node_modules', 'sharp'),
  ];
  for (const cand of candidates) {
    try {
      const m = require(cand);
      return m.default ?? m;
    } catch {}
  }
  console.error('✗ sharp not found. Run: npm --prefix web install --save-dev sharp');
  process.exit(1);
}

function pngToIco(pngBuffers) {
  // Minimal ICO packer: supports multiple PNG-encoded images (Vista+). Works in all modern browsers/OS.
  const count = pngBuffers.length;
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type 1 = icon
  header.writeUInt16LE(count, 4);
  const entries = [];
  const dataOffset0 = 6 + count * 16;
  let offset = dataOffset0;
  for (const buf of pngBuffers) {
    // PNG header gives real size; ICO entry size byte is 0 for 256.
    // We'll parse PNG IHDR for width/height.
    const w = buf.readUInt32BE(16);
    const h = buf.readUInt32BE(20);
    const entry = Buffer.alloc(16);
    entry[0] = w >= 256 ? 0 : w;
    entry[1] = h >= 256 ? 0 : h;
    entry[2] = 0; // colors
    entry[3] = 0; // reserved
    entry.writeUInt16LE(1, 4); // planes
    entry.writeUInt16LE(32, 6); // bpp
    entry.writeUInt32LE(buf.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    offset += buf.length;
  }
  return Buffer.concat([header, ...entries, ...pngBuffers]);
}

const targets = [
  { w: 192, out: ['web/public/icons/icon-192.png'] },
  { w: 512, out: ['web/public/icons/icon-512.png'] },
  { w: 512, out: ['web/public/icons/icon-512-maskable.png'], maskable: true },
  { w: 180, out: ['web/public/icons/apple-touch-icon.png', 'miniapp/public/apple-touch-icon.png'] },
  { w: 32,  out: ['web/public/favicon.ico.png'] }, // ICO built from 16/32/48 below
];

async function main() {
  const sharp = await loadSharp();
  // Brand copies mirrored to miniapp
  fs.mkdirSync(path.join(root, 'miniapp', 'public', 'brand'), { recursive: true });
  for (const name of ['noax.jpg', 'noax-64.png', 'noax-256.png']) {
    const s = path.join(root, 'web', 'public', 'brand', name);
    const d = path.join(root, 'miniapp', 'public', 'brand', name);
    if (fs.existsSync(s) && !fs.existsSync(d)) fs.copyFileSync(s, d);
  }
  // Also ensure noax.jpg exists under miniapp/public/brand
  if (!fs.existsSync(path.join(root, 'miniapp', 'public', 'brand', 'noax.jpg'))) {
    fs.copyFileSync(path.join(root, 'web', 'public', 'brand', 'noax.jpg'), path.join(root, 'miniapp', 'public', 'brand', 'noax.jpg'));
  }

  for (const t of targets) {
    if (t.maskable) {
      const inner = Math.round(t.w * 0.8);
      const innerBuf = await sharp(src).resize(inner, inner, { fit: 'cover' }).png().toBuffer();
      const outBuf = await sharp({ create: { width: t.w, height: t.w, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } })
        .composite([{ input: innerBuf, left: Math.round((t.w - inner) / 2), top: Math.round((t.w - inner) / 2) }])
        .png().toBuffer();
      for (const rel of t.out) {
        const abs = path.join(root, rel);
        fs.mkdirSync(path.dirname(abs), { recursive: true });
        fs.writeFileSync(abs, outBuf);
        console.log(`wrote ${rel}`);
      }
    } else if (t.w === 32) {
      // favicon path handled below via ICO
      continue;
    } else {
      const buf = await sharp(src).resize(t.w, t.w, { fit: 'cover' }).png().toBuffer();
      for (const rel of t.out) {
        const abs = path.join(root, rel);
        fs.mkdirSync(path.dirname(abs), { recursive: true });
        fs.writeFileSync(abs, buf);
        console.log(`wrote ${rel}`);
      }
    }
  }

  // favicon.ico multi-size 16/32/48 PNG-encoded ICO
  const icoSizes = [16, 32, 48];
  const icoPngs = [];
  for (const s of icoSizes) {
    const buf = await sharp(src).resize(s, s, { fit: 'cover' }).png().toBuffer();
    icoPngs.push(buf);
  }
  const ico = pngToIco(icoPngs);
  for (const rel of ['web/public/favicon.ico', 'miniapp/public/favicon.ico']) {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, ico);
    console.log(`wrote ${rel} (${ico.length} bytes, ${icoSizes.join('/')}px)`);
  }
  // also keep png helpers
  fs.writeFileSync(path.join(root, 'web', 'public', 'favicon.ico.png'), icoPngs[1]);
  fs.writeFileSync(path.join(root, 'miniapp', 'public', 'favicon.ico.png'), icoPngs[1]);

  // og images: 1200x630 cover, webp + png fallback
  const ogW = 1200, ogH = 630;
  // web
  const ogPng = await sharp(src).resize(ogW, ogH, { fit: 'cover', position: 'centre' }).png().toBuffer();
  const ogWebp = await sharp(src).resize(ogW, ogH, { fit: 'cover', position: 'centre' }).webp({ quality: 82 }).toBuffer();
  fs.writeFileSync(path.join(root, 'web', 'public', 'og.png'), ogPng);
  fs.writeFileSync(path.join(root, 'web', 'public', 'og.webp'), ogWebp);
  console.log(`wrote web/public/og.png (${ogPng.length}) + og.webp (${ogWebp.length})`);
  // miniapp
  fs.mkdirSync(path.join(root, 'miniapp', 'public'), { recursive: true });
  fs.writeFileSync(path.join(root, 'miniapp', 'public', 'og.png'), ogPng);
  fs.writeFileSync(path.join(root, 'miniapp', 'public', 'og.webp'), ogWebp);
  console.log(`wrote miniapp/public/og.png + og.webp`);

  // standalone PWA icons for miniapp
  for (const [w, name] of [[192, 'icon-192.png'], [512, 'icon-512.png']]) {
    const buf = await sharp(src).resize(w, w, { fit: 'cover' }).png().toBuffer();
    const abs = path.join(root, 'miniapp', 'public', name);
    fs.writeFileSync(abs, buf);
    console.log(`wrote miniapp/public/${name}`);
  }
  const maskableBuf = await (async () => {
    const inner = 410;
    const innerBuf = await sharp(src).resize(inner, inner, { fit: 'cover' }).png().toBuffer();
    return sharp({ create: { width: 512, height: 512, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } })
      .composite([{ input: innerBuf, left: 51, top: 51 }]).png().toBuffer();
  })();
  fs.writeFileSync(path.join(root, 'miniapp', 'public', 'icon-512-maskable.png'), maskableBuf);
  console.log('wrote miniapp/public/icon-512-maskable.png');

  // simple sanity: og.webp should be < 180KB
  if (ogWebp.length > 180 * 1024) console.warn(`! og.webp is ${(ogWebp.length/1024).toFixed(0)}KB (>180KB budget)`);
  // ICO header check: 00 00 01 00
  if (ico.readUInt16LE(2) !== 1) console.warn('! ICO header unexpected');
  console.log('done');
}

main().catch((e) => { console.error(e); process.exit(1); });
