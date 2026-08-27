const puppeteer = require('puppeteer-core');
const { pathToFileURL } = require('url');
const fs = require('fs'); const path = require('path');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const FPS = 30;
const scenes = JSON.parse(fs.readFileSync(path.join(__dirname, 'scenes.json'), 'utf8'));
(async () => {
  const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--force-device-scale-factor=1'], defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 } });
  const page = await browser.newPage();
  const base = pathToFileURL(path.join(__dirname, 'scene_cine.html')).href;
  for (const s of scenes) {
    const outDir = path.join(__dirname, 'frames', s.id); fs.mkdirSync(outDir, { recursive: true });
    const url = `${base}?bg=${s.bg}&kb=${s.kb}&eyebrow=${encodeURIComponent(s.eyebrow)}&title=${encodeURIComponent(s.title)}&sub=${encodeURIComponent(s.sub)}&points=${encodeURIComponent(s.points || '')}`;
    await page.goto(url, { waitUntil: 'load' }); await new Promise(r => setTimeout(r, 320));
    const total = Math.round((s.dur + 0.7) * FPS);
    for (let i = 0; i < total; i++) { await page.evaluate(t => document.getAnimations().forEach(a => { try { a.pause(); a.currentTime = t; } catch (e) {} }), (i / FPS) * 1000); await page.screenshot({ path: path.join(outDir, String(i).padStart(4, '0') + '.png') }); }
    console.log('rendered', s.id, total);
  }
  await browser.close(); console.log('DONE');
})().catch(e => { console.error('ERR', e.stack); process.exit(1); });
