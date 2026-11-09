// Home-screen icons (apple-touch-icon, manifest icons), drawn in code and emitted as PNGs at build
// time: the repo's privacy hook refuses committed image files, so these never live in git.
import { deflateSync } from "node:zlib";
import type { Plugin } from "vite";

type RGB = [number, number, number];
const BLUE: RGB = [47, 111, 219];
const PAPER: RGB = [246, 245, 242];
const WHITE: RGB = [255, 255, 255];
const PHOTO: RGB = [226, 223, 216];

const CRC = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf: Buffer): number {
  let c = 0xffffffff;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}

/** A shoebox with a photo peeking out, on blue. `pad` shrinks the drawing (for maskable icons). */
export function iconPng(size: number, pad = 0): Buffer {
  const SS = 4; // supersampling per axis, for smooth corners
  const rects: { box: [number, number, number, number]; r: number; c: RGB }[] = [];
  const m = 0.2 + pad;
  const w = 1 - 2 * m;
  rects.push({ box: [m + w * 0.2, m - w * 0.02, m + w * 0.62, m + w * 0.2], r: 0, c: PHOTO });
  rects.push({ box: [m, m + w * 0.28, 1 - m, 1 - m], r: w * 0.06, c: PAPER });
  rects.push({ box: [m - w * 0.05, m + w * 0.12, 1 - m + w * 0.05, m + w * 0.32], r: w * 0.05, c: WHITE });
  const inside = (x: number, y: number, [x0, y0, x1, y1]: [number, number, number, number], r: number) => {
    if (x < x0 || x > x1 || y < y0 || y > y1) return false;
    const cx = Math.min(Math.max(x, x0 + r), x1 - r);
    const cy = Math.min(Math.max(y, y0 + r), y1 - r);
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
  };
  const raw = Buffer.alloc(size * (size * 3 + 1));
  for (let py = 0; py < size; py++) {
    raw[py * (size * 3 + 1)] = 0; // filter: none
    for (let px = 0; px < size; px++) {
      const acc = [0, 0, 0];
      for (let sy = 0; sy < SS; sy++)
        for (let sx = 0; sx < SS; sx++) {
          const x = (px + (sx + 0.5) / SS) / size;
          const y = (py + (sy + 0.5) / SS) / size;
          let c = BLUE;
          for (const rc of rects) if (inside(x, y, rc.box, rc.r)) c = rc.c;
          for (let k = 0; k < 3; k++) acc[k] += c[k];
        }
      const o = py * (size * 3 + 1) + 1 + px * 3;
      for (let k = 0; k < 3; k++) raw[o + k] = Math.round(acc[k] / (SS * SS));
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const ICONS: Record<string, () => Buffer> = {
  "apple-touch-icon.png": () => iconPng(180),
  "icon-192.png": () => iconPng(192),
  "icon-512.png": () => iconPng(512),
  "icon-maskable-512.png": () => iconPng(512, 0.08),
};

/** Emits the icons into the build, and serves them from the dev server. */
export function homeScreenIcons(): Plugin {
  return {
    name: "shoebox-home-screen-icons",
    generateBundle() {
      for (const [fileName, make] of Object.entries(ICONS)) this.emitFile({ type: "asset", fileName, source: make() });
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const make = ICONS[(req.url ?? "").replace(/^\//, "").split("?")[0]];
        if (!make) return next();
        res.setHeader("Content-Type", "image/png");
        res.end(make());
      });
    },
  };
}
