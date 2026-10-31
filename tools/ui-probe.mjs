// 主界面 UI 探针：渲染截图 + 顶栏账目几何/溢出/字号/空白 + 控制台错误
// 用法: bash tools/run-ui-probe.sh   (需先起服务器 LLM_MOCK=1 python server.py)
import fs from 'node:fs';

const PORT = Number(process.env.CDP_PORT || 9222);
const URL_TARGET = process.env.APP_URL || 'http://127.0.0.1:8010/';
const OUT = process.env.OUT_DIR || 'png/ui-audit';
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function findPage() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const p = list.find((t) => t.type === 'page');
      if (p) return p;
    } catch {}
    await sleep(250);
  }
  throw new Error('CDP page target not found');
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    this.handlers = new Map();
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      } else if (m.method) (this.handlers.get(m.method) || []).forEach((f) => f(m.params));
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(m, f) { if (!this.handlers.has(m)) this.handlers.set(m, []); this.handlers.get(m).push(f); }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails.text));
    return r.result.value;
  }
}

const page = await findPage();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
const cdp = new CDP(ws);

const consoleMsgs = [], exceptions = [];
cdp.on('Runtime.consoleAPICalled', (p) => {
  if (['error', 'warning'].includes(p.type)) consoleMsgs.push({ type: p.type, text: (p.args || []).map((a) => a.value ?? a.description ?? a.type).join(' ') });
});
cdp.on('Runtime.exceptionThrown', (p) => exceptions.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text));
cdp.on('Log.entryAdded', (p) => { if (['error', 'warning'].includes(p.entry.level)) consoleMsgs.push({ type: p.entry.level, text: p.entry.text }); });

await cdp.send('Page.enable');
await cdp.send('Runtime.enable');
await cdp.send('Log.enable');
await cdp.send('Page.navigate', { url: URL_TARGET });
await sleep(1200);

let dataReady = false;
for (let i = 0; i < 60; i++) {
  const t = await cdp.eval(`(document.querySelector('#ov-treasury')?.textContent||'').trim()`);
  if (t && t !== '—') { dataReady = true; break; }
  await sleep(250);
}
await sleep(600);

const METRICS = `(() => {
  const q = (s) => document.querySelector(s);
  const all = (s) => [...document.querySelectorAll(s)];
  const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
             right: Math.round(r.right), bottom: Math.round(r.bottom) }; };

  const tl = q('#tl'), tr = q('#tr'), led = q('#tl-ledger');
  const rows = all('.tl-line').map((el) => ({ h: Math.round(el.getBoundingClientRect().height),
    key: (el.querySelector('.tl-name')?.textContent || '').trim(),
    items: all('.tl-line').length ? 0 : 0,
    right: Math.round(el.getBoundingClientRect().right) }));

  // 顶栏内部空白：面板可见高度 − 内容实际占用高度
  const cols = all('.tl-col');
  const colH = cols.map((c) => Math.round(c.getBoundingClientRect().height));
  const colW = cols.map((c) => Math.round(c.getBoundingClientRect().width));
  const panelInner = tl ? Math.round(tl.getBoundingClientRect().height - 16) : 0;
  const contentH = colH.length ? Math.max(...colH) : 0;
  const blank = panelInner - contentH;

  // 溢出：账目右边界 vs 右上面板左边界
  const itemsRight = Math.max(0, ...all('.tl-col').map((e) => Math.round(e.getBoundingClientRect().right)));
  const overflow = tr ? itemsRight - Math.round(tr.getBoundingClientRect().x) : 0;

  // 视口外元素
  const vw = innerWidth, vh = innerHeight, off = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (r.right > vw + 1 || r.bottom > vh + 1 || r.left < -1) {
      off.push({ tag: el.tagName.toLowerCase(), id: el.id || null, cls: el.className && String(el.className).slice(0, 30) || null,
                 right: Math.round(r.right), w: Math.round(r.width) });
    }
  }

  // 字号普查（#tl 内）
  const sizes = {};
  for (const el of all('#tl *')) {
    if (!el.childElementCount && (el.textContent || '').trim()) {
      const fs = getComputedStyle(el).fontSize;
      sizes[fs] = (sizes[fs] || 0) + 1;
    }
  }

  // 每个数值是否被压成分行（按钮式换行检测：宽高比异常）
  const squeezes = [];
  for (const el of all('#adv-row > *')) {
    const r = el.getBoundingClientRect();
    if (r.height > 34) squeezes.push({ txt: (el.textContent || '').trim().slice(0, 12), w: Math.round(r.width), h: Math.round(r.height) });
  }

  const gearEl = q('#settings-btn');
  const gearR = gearEl ? gearEl.getBoundingClientRect() : null;
  const lastColR = cols.length ? cols[cols.length - 1].getBoundingClientRect() : null;
  const lineRight = Math.max(...all('.tl-line').map((e) => e.getBoundingClientRect().right));
  const gearOverlap = !!(gearR && lastColR && lineRight > gearR.left - 1);

  return {
    gearOverlap, lineRight: Math.round(lineRight), gearLeft: gearR ? Math.round(gearR.left) : null,
    viewport: { w: vw, h: vh }, scroll: { docW: document.documentElement.scrollWidth, appW: q('#app').scrollWidth },
    rects: { tl: rect(tl), tr: rect(tr), ledger: rect(led), left: rect(q('#left')), map: rect(q('#map')), mem: rect(q('#mem')) },
    rows, colH, colW, contentH, blank, overflow, itemsRight,
    trX: tr ? Math.round(tr.getBoundingClientRect().x) : null,
    offscreenCount: off.length, offscreen: off.slice(0, 12),
    fontSizeCensus: sizes, squeezes,
    tipsInTl: all('#tl .info-tip').length,
    values: {
      treasury: q('#ov-treasury')?.textContent, m1: q('#ov-m1')?.textContent, m2: q('#ov-m2')?.textContent,
      reserve: q('#ov-reserve')?.textContent, health: q('#ov-health')?.textContent, f: q('#ov-f-rate')?.textContent,
      gold: q('#ov-gold-total')?.textContent, prestige: q('#ov-prestige')?.textContent,
      corruption: q('#ov-central-corruption')?.textContent, facYan: q('#ov-fac-yan')?.textContent,
      removed_g: q('#ov-g-scale'), removed_pop: q('#ov-pop'), techPts: q('#tech-entry-points')?.textContent,
    },
    domNodes: document.querySelectorAll('*').length,
  };
})()`;

const metrics = { dataReady, viewports: {} };

async function shoot(name, w, h) {
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  await sleep(700);
  const m = await cdp.eval(METRICS);
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(`${OUT}/${name}.png`, Buffer.from(shot.data, 'base64'));
  const clip = await cdp.eval(`(() => { const r = document.querySelector('#tl').getBoundingClientRect();
     return { x: r.x, y: r.y, width: r.width, height: r.height }; })()`);
  try {
    const s2 = await cdp.send('Page.captureScreenshot', { format: 'png',
      clip: { x: Math.max(0, clip.x - 4), y: Math.max(0, clip.y - 4), width: clip.width + 8, height: clip.height + 8, scale: 2 } });
    fs.writeFileSync(`${OUT}/${name}-topband.png`, Buffer.from(s2.data, 'base64'));
  } catch {}
  metrics.viewports[name] = m;
}

await shoot('w1920', 1920, 1080);
await shoot('w1600', 1600, 950);
await shoot('w1366', 1366, 768);
await shoot('w1280', 1280, 800);

// 强制展开一个 ⓘ，检验提示框设计仍在
try {
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 950, deviceScaleFactor: 1, mobile: false });
  await sleep(400);
  await cdp.eval(`(() => { const t = document.querySelector('#ov-gold-tip');
    if (!t) return 'no tip'; t.closest('.info-tip').classList.add('forced');
    const st = document.createElement('style');
    st.textContent = '.info-tip.forced .tip-box{display:block!important;}';
    document.head.appendChild(st); return 'ok'; })()`);
  await sleep(400);
  const s = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(`${OUT}/tooltip.png`, Buffer.from(s.data, 'base64'));
} catch (e) { metrics.tooltipError = String(e); }

metrics.consoleMessages = consoleMsgs.slice(0, 40);
metrics.exceptions = exceptions.slice(0, 20);
fs.writeFileSync(`${OUT}/metrics.json`, JSON.stringify(metrics, null, 2));
console.log(JSON.stringify({ dataReady, screens: Object.keys(metrics.viewports),
  console: consoleMsgs.length, exceptions: exceptions.length }));
ws.close();
process.exit(0);
