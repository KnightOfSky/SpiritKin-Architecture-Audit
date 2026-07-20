import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { pathToFileURL } from 'node:url';
const pwUrl = pathToFileURL(path.join(process.env.PW_DIR, 'playwright', 'index.mjs')).href;
const { chromium } = await import(pwUrl);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..', 'frontend');
const outDir = path.resolve(__dirname, '..', 'runtime', 'ui_shots');
fs.mkdirSync(outDir, { recursive: true });

const MIME = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json' };

const server = http.createServer((req, res) => {
  let urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
  if (urlPath === '/') urlPath = '/desktop_console.html';
  const filePath = path.join(root, urlPath);
  if (!filePath.startsWith(root) || !fs.existsSync(filePath)) {
    res.writeHead(404); res.end('not found'); return;
  }
  res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'application/octet-stream' });
  fs.createReadStream(filePath).pipe(res);
});

await new Promise(r => server.listen(0, '127.0.0.1', r));
const port = server.address().port;
const base = `http://127.0.0.1:${port}/desktop_console.html`;
console.log('serving', base);

const browser = await chromium.launch({ executablePath: process.env.PW_CHROME });
const consoleErrors = [];
for (const vp of [{ w: 1440, h: 900, tag: 'desktop' }, { w: 1040, h: 800, tag: 'min' }, { w: 900, h: 800, tag: 'narrow' }]) {
  const page = await browser.newPage({ viewport: { width: vp.w, height: vp.h } });
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(`[${vp.tag}] ${m.text()}`); });
  page.on('pageerror', e => consoleErrors.push(`[${vp.tag}] PAGEERROR ${e.message}`));
  await page.goto(base, { waitUntil: 'networkidle' }).catch(() => {});
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(outDir, `console_${vp.tag}.png`), fullPage: false });
  console.log('shot', vp.tag);
  await page.close();
}
await browser.close();
server.close();
fs.writeFileSync(path.join(outDir, 'console_errors.txt'), consoleErrors.join('\n') || '(no console errors)');
console.log('--- console errors ---');
console.log(consoleErrors.join('\n') || '(none)');
