// 多视图探针：舞台 + 各子界面逐个开一遍，抓图并收集溢出/控制台错误
import fs from 'node:fs';

const PORT = Number(process.env.CDP_PORT || 9222);
const OUT = process.env.OUT_DIR || 'png/ui-audit';
const URL_TARGET = process.env.APP_URL || 'http://127.0.0.1:8010/';
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const VIEWS = (process.env.VIEWS || 'map,court,mem,local,mainline,person,army,liubu,waizhusi,ershisi,settings,dialogs,tech')
  .split(',').map((s) => s.trim()).filter(Boolean);

async function findPage() {
  for (let i = 0; i < 40; i++) {
    try {
      const l = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const p = l.find((t) => t.type === 'page');
      if (p) return p;
    } catch {}
    await sleep(250);
  }
  throw new Error('no page target');
}
class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = new Map();
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
    return new Promise((res, rej) => { this.pending.set(id, { resolve: res, reject: rej });
      this.ws.send(JSON.stringify({ id, method, params })); });
  }
  on(m, f) { if (!this.handlers.has(m)) this.handlers.set(m, []); this.handlers.get(m).push(f); }
  async eval(e) {
    const r = await this.send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('eval: ' + (r.exceptionDetails.text || '') + ' ' +
      JSON.stringify(r.exceptionDetails.exception?.description || ''));
    return r.result.value;
  }
}

const page = await findPage();
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
const cdp = new CDP(ws);

const errs = [];
cdp.on('Runtime.exceptionThrown', (p) => errs.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text));
cdp.on('Runtime.consoleAPICalled', (p) => { if (p.type === 'error') errs.push((p.args || []).map((a) => a.value ?? a.description).join(' ')); });

await cdp.send('Page.enable');
await cdp.send('Runtime.enable');
await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 950, deviceScaleFactor: 1, mobile: false });
await cdp.send('Page.navigate', { url: URL_TARGET });
await sleep(2500);

async function shot(name) {
  const s = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.writeFileSync(`${OUT}/v-${name}.png`, Buffer.from(s.data, 'base64'));
}

const metrics = { stage: null, views: {} };

const PROBE = `(() => {
  const r = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height),
             right: Math.round(b.right), bottom: Math.round(b.bottom) }; };
  const off = [];
  for (const el of document.querySelectorAll('body *')) {
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (b.right > innerWidth + 1 || b.bottom > innerHeight + 1 || b.left < -1) {
      off.push({ tag: el.tagName.toLowerCase(), id: el.id || null,
                 cls: el.className && String(el.className).slice(0, 28),
                 right: Math.round(b.right), bottom: Math.round(b.bottom) });
    }
  }
  return {
    vw: innerWidth, vh: innerHeight, docW: document.documentElement.scrollWidth,
    stage: r(document.querySelector('#stage')),
    dialog: r(document.querySelector('#stage-dialog')),
    openViews: [...document.querySelectorAll('.subview.open')].map((e) => e.id),
    frames: [...document.querySelectorAll('.subview.open')].map((e) => r(e)),
    offscreen: off.slice(0, 10), offCount: off.length,
  };
})()`;

// 1) 舞台
metrics.stage = await cdp.eval(PROBE);
await shot('stage');

// 1a0) 可选：世界地图舞台层
if (process.env.MAP) {
  await cdp.eval('openMapScreen()');
  await sleep(1200);
  metrics.map = await cdp.eval(PROBE);
  await shot('map-stage');
  await cdp.eval('closeMapView()');
  await sleep(300);
}

// 1a0b) 可选：紫禁城（离开落点）
if (process.env.CITY) {
  await cdp.eval('openCityScreen()');
  await sleep(1400);
  metrics.city = await cdp.eval(PROBE);
  await shot('city');
  await cdp.eval('closeMapView()');
  await sleep(300);
}

// 1a1) 可选：择人面板
if (process.env.PICK) {
  await cdp.eval('showCouncilStart(); councilRefresh && councilRefresh();');
  await sleep(1500);
  await shot('council-pick');
  await cdp.eval('closeCouncilStart()');
}

// 1a) 可选：进入会中的廷议舞台
if (process.env.COUNCIL) {
  await cdp.eval('showCouncil()');
  await sleep(1500);
  metrics.council = await cdp.eval(PROBE);
  await shot('council-stage');
  if (process.env.SEND) {
    await cdp.eval(`(() => { $("council-text").value = ${JSON.stringify(process.env.SEND)}; councilSend(); })()`);
    await sleep(9000);
    await shot('council-sent');
  }
}

// 1b) 可选：演一条对话，验证对话框与立绘
if (process.env.SAY) {
  await cdp.eval(`dlgSay(${process.env.SAY})`);
  await sleep(900);
  metrics.dialog = await cdp.eval(PROBE);
  await shot('dialog');
  await cdp.eval('dlgNext(); stageClear();');
}

// 1c) 可选：看某个人物卡（含表情格）
if (process.env.CARD) {
  await cdp.eval(`openView('person')`);
  await sleep(800);
  await cdp.eval(`fetch('/api/card?name=' + encodeURIComponent(${JSON.stringify(process.env.CARD)}))
    .then(r => r.json()).then(d => renderCardDetail($('person-detail'), d.card))`);
  await sleep(1200);
  metrics.card = await cdp.eval(`(() => {
    const d = document.querySelector('#person-detail');
    const slots = [...document.querySelectorAll('#person-detail .expr-slot')];
    return { slots: slots.length,
             labels: slots.map(s => s.querySelector('.e').textContent + ':' + s.querySelector('.s').textContent),
             sections: [...document.querySelectorAll('#person-detail .d-sec .h')].map(x => x.textContent) };
  })()`);
  await shot('card');
  await cdp.eval('leaveToMap()');
}

// 1d) 可选：对话查阅的某个页签
if (process.env.DLGTAB) {
  await cdp.eval(`openView('dialogs'); dlgTab(${JSON.stringify(process.env.DLGTAB)})`);
  await sleep(1200);
  await shot('dlgtab-' + process.env.DLGTAB);
  await cdp.eval('leaveSubView()');
}

// 2) 各子界面
for (const v of VIEWS) {
  try {
    await cdp.eval(`openView(${JSON.stringify(v)})`);
    await sleep(900);
    metrics.views[v] = await cdp.eval(PROBE);
    await shot(v);
    await cdp.eval('leaveToMap()');
    await sleep(400);
    await cdp.eval('closeMapView()');
    await sleep(400);
  } catch (e) {
    metrics.views[v] = { error: String(e).slice(0, 200) };
  }
}

metrics.errors = errs.slice(0, 20);
fs.writeFileSync(`${OUT}/views.json`, JSON.stringify(metrics, null, 2));
console.log(JSON.stringify({
  stage: metrics.stage && { off: metrics.stage.offCount, dialog: metrics.stage.dialog },
  views: Object.fromEntries(Object.entries(metrics.views).map(([k, v]) => [k, v.error || { off: v.offCount, frames: v.frames }])),
  errors: metrics.errors.length,
}, null, 1));
ws.close();
process.exit(0);
