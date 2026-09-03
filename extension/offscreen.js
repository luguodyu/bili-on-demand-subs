// 离屏页面 v0.4.1（按需字幕专用，旧实时识别代码已移除）：
//   接收「生成字幕」任务 -> 本地 faster-whisper 服务(服务端自下载音频) 优先，
//   服务不可用时浏览器下载 + WASM 识别回退 -> 存储字幕 -> 通知完成。
import { pipeline, env, read_audio } from './transformers.js';

// —— 环境加固（必须在加载模型前执行）——
console.log('[B站字幕] offscreen v0.4.1 (按需转录·服务端下载)');

// 无 WebGPU 适配器时 ort 的 requestAdapter() 可能挂起：直接屏蔽，秒退 WASM
try {
  Object.defineProperty(Navigator.prototype, 'gpu', { configurable: true, get: () => undefined });
} catch (e) {
  console.warn('禁用 navigator.gpu 失败(不影响功能):', e);
}

// 模型走国内镜像；WASM 运行时用插件本地副本
env.remoteHost = 'https://hf-mirror.com';
env.backends.onnx.wasm.wasmPaths = chrome.runtime.getURL('ort/');

const SERVER_BASE = 'http://127.0.0.1:8765';
const SERVER_VERSION = 2;

let batch = null;              // { cid, cancelled, ctrl }
let asr = null;                // WASM whisper 管道（回退路径懒加载）
const audioAssembler = {};     // cid -> { chunks:[{seq,data}], done, failed }

// ================= 基础工具 =================
function prog(pct, msg) {
  if (!batch) return;
  try { chrome.runtime.sendMessage({ type: 'makeProgress', cid: batch.cid, pct, msg }).catch(() => {}); } catch (e) {}
}
const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchWithTimeout(url, ms) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try { return await fetch(url, { signal: ctrl.signal }); }
  finally { clearTimeout(t); }
}

// ================= 存储（统一走后台：离屏页无 chrome.storage） =================
function sendMsg(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => {
        if (chrome.runtime.lastError) resolve(null);
        else resolve(r);
      });
    } catch (e) { resolve(null); }
  });
}

async function storeSubViaBackground(rec) {
  const r = await sendMsg({ type: 'storeSub', rec });
  if (!r || !r.ok) throw new Error('字幕保存失败（后台未响应）');
}

// ================= 快路径：本地服务（服务端自下载 + 识别） =================
async function transcribeServer(urls) {
  const health = await fetchWithTimeout(SERVER_BASE + '/health', 1500).catch(() => null);
  if (!health || !health.ok) return null;
  const hj = await health.json().catch(() => ({}));
  if (hj.version !== SERVER_VERSION) {
    const e = new Error('本地服务版本过旧，请先关闭旧的 .bat 窗口，再重新双击「启动字幕服务.bat」');
    e.stale = true;
    throw e;
  }
  prog(6, '已连接本地识别服务');
  const ctrl = new AbortController();
  if (batch) batch.ctrl = ctrl;
  const capTimer = setTimeout(() => { try { ctrl.abort(); } catch (e) {} }, 60 * 60 * 1000);
  const t0 = Date.now();
  // 轮询服务端真实进度
  const poll = setInterval(async () => {
    try {
      if (batch && batch.cancelled) { try { ctrl.abort(); } catch (e) {} return; }
      const r = await fetchWithTimeout(SERVER_BASE + '/progress', 2000);
      const j = await r.json();
      if (j && j.duration > 0 && j.ts > 0) {
        const p = Math.min(0.9, (j.end || 0) / j.duration);
        prog(15 + p * 75, '本地识别中…'); // 百分比以进度条数值为准（与 pct 同源）
      } else {
        prog(Math.min(12, 6 + (Date.now() - t0) / 1000 * 0.4), '服务端下载音频中…');
      }
    } catch (e) {}
  }, 2000);
  try {
    // text/plain：简单请求，不触发 CORS 预检
    const resp = await fetch(SERVER_BASE + '/transcribe-url', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: JSON.stringify({ urls }),
      signal: ctrl.signal
    });
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}));
      throw new Error(j.error || ('本地服务返回 ' + resp.status));
    }
    const j = await resp.json();
    if (!Array.isArray(j.segments)) throw new Error('本地服务响应格式异常');
    return j.segments
      .map((g) => ({ start: Math.round((g.start || 0) * 10) / 10, end: Math.round((g.end || 0) * 10) / 10, text: (g.text || '').trim() }))
      .filter((g) => g.text);
  } finally {
    clearInterval(poll);
    clearTimeout(capTimer);
  }
}

// ================= 回退路径：浏览器下载 + WASM =================
function concatChunks(a) {
  a.chunks.sort((x, y) => x.seq - y.seq);
  const total = a.chunks.reduce((s, c) => s + c.data.byteLength, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of a.chunks) { out.set(new Uint8Array(c.data), off); off += c.data.byteLength; }
  return out.buffer;
}

async function downloadAudio(urls) {
  const list = (urls || []).filter(Boolean);
  if (!list.length) throw new Error('未取得音频地址');
  let hbTicks = 0;
  const hb = setInterval(() => {
    hbTicks++;
    if (batch) prog(Math.min(6, 2 + hbTicks * 0.5), '正在下载音频…');
  }, 5000);
  try {
    // 1) 页内下载（referer 正确）；不等响应，直接等分片流
    try { chrome.runtime.sendMessage({ type: 'fetchAudioHere', cid: batch.cid, url: list[0] }).catch(() => {}); } catch (e) {}
    for (let i = 0; i < 240; i++) {
      const a = audioAssembler[batch.cid];
      if (a && a.failed) break;
      if (a && a.done) {
        const buf = concatChunks(a);
        delete audioAssembler[batch.cid];
        if (!buf || buf.byteLength < 10000) throw new Error('音频数据不完整');
        return buf;
      }
      await sleepMs(500);
    }
    // 2) 直连兜底
    for (const u of list) {
      try {
        const r = await fetchWithTimeout(u, 8000);
        if (r.ok) {
          const b = await r.arrayBuffer();
          if (b.byteLength > 10000) return b;
        }
      } catch (e) {}
    }
    throw new Error('音频下载失败：页面与直连均不可用');
  } finally {
    clearInterval(hb);
  }
}

async function decodeToPcm(buf) {
  const blob = new Blob([buf], { type: 'audio/mp4' });
  const url = URL.createObjectURL(blob);
  try {
    const pcm = await read_audio(url, 16000);
    if (!pcm || !pcm.length) throw new Error('音频解码失败');
    return pcm;
  } finally { URL.revokeObjectURL(url); }
}

async function ensureModel() {
  if (asr) return;
  prog(15, '加载浏览器识别模型（首次需下载）…');
  asr = await pipeline('automatic-speech-recognition', 'Xenova/whisper-small', {
    dtype: 'q8',
    session_options: { executionProviders: ['wasm'] }
  });
  console.log('whisper 模型就绪');
}

async function transcribeWholeWasm(pcm) {
  await ensureModel();
  const rate = 16000;
  const WIN = 30 * rate, ADV = 25 * rate;
  const segs = [];
  let w0 = 0;
  while (w0 < pcm.length) {
    if (batch && batch.cancelled) throw new Error('已取消');
    const w1 = Math.min(w0 + WIN, pcm.length);
    const win = pcm.slice(w0, w1);
    const out = await asr(win, { language: 'chinese', task: 'transcribe', return_timestamps: true });
    const chunks = (out && out.chunks) || [];
    for (const c of chunks) {
      const text = (c.text || '').trim();
      if (!text) continue;
      const ts = c.timestamp || [0, 0];
      const start = w0 / rate + ts[0];
      const end = w0 / rate + ts[1];
      if (w0 > 0 && end <= w0 / rate + (WIN - ADV) / rate + 0.3) continue; // 跳过重叠重复
      segs.push({ start: Math.round(start * 10) / 10, end: Math.round(end * 10) / 10, text });
    }
    w0 += ADV;
    prog(Math.min(97, 15 + (w0 / pcm.length) * 80), '浏览器识别中…（较慢，可稍后回来查看）');
  }
  segs.sort((a, b) => a.start - b.start);
  return segs;
}

// ================= 任务主流程 =================
async function runMakeJob(msg) {
  if (batch && !batch.cancelled && batch.cid === msg.cid) return; // 广播+中继去重
  batch = { cid: msg.cid, cancelled: false, ctrl: null };
  const urls = (msg.audioUrls || []).filter(Boolean);
  try {
    let segs = null;
    if (urls.length) {
      prog(3, '连接本地识别服务…');
      try { segs = await transcribeServer(urls); } catch (e) {
        if (e && e.stale) throw e; // 服务版本过旧：明确报错
        console.error('服务端识别失败，回退浏览器:', e);
      }
    }
    if (!segs) {
      prog(2, '本地服务不可用，改用浏览器方式（较慢）…');
      const buf = await downloadAudio(urls);
      prog(8, '音频下载完成，解码中…');
      const pcm = await decodeToPcm(buf);
      prog(15, '浏览器内置识别中…');
      segs = await transcribeWholeWasm(pcm);
    }
    if (batch && batch.cancelled) throw new Error('已取消');
    if (!segs || !segs.length) throw new Error('未识别到任何内容');
    const rec = {
      cid: msg.cid, bvid: msg.bvid, title: msg.title || '', pageUrl: msg.pageUrl || '',
      createdAt: Date.now(), source: 'make', lang: 'zh', segs
    };
    await storeSubViaBackground(rec);
    prog(100, '完成');
    chrome.runtime.sendMessage({ type: 'makeDone', cid: msg.cid, ok: true }).catch(() => {});
  } catch (err) {
    console.error('制作字幕失败:', err);
    chrome.runtime.sendMessage({ type: 'makeDone', cid: msg.cid, ok: false, error: err.message || String(err) }).catch(() => {});
  } finally {
    batch = null;
  }
}

// ================= 消息入口 =================
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg !== 'object') return;
  switch (msg.type) {
    case 'makeSub':
      runMakeJob(msg);
      return;
    case 'cancelMake':
      if (batch && batch.cid === msg.cid) {
        batch.cancelled = true;
        if (batch.ctrl) { try { batch.ctrl.abort(); } catch (e) {} }
      }
      return;
    case 'audioChunk': {
      if (!msg.data || msg.cid == null) { sendResponse({ ok: false }); return true; }
      const a = audioAssembler[msg.cid] || (audioAssembler[msg.cid] = { chunks: [], done: false, failed: false });
      a.chunks.push({ seq: msg.seq || 0, data: msg.data });
      if (msg.last) a.done = true;
      sendResponse({ ok: true });
      return true;
    }
    case 'audioFetched': {
      const a = audioAssembler[msg.cid] || (audioAssembler[msg.cid] = { chunks: [], done: false, failed: false });
      if (!msg.ok) a.failed = true;
      return;
    }
    default:
      return; // keepalive 等：忽略
  }
});

// 通知后台：本页已就绪（background 创建离屏页后会等这条）
chrome.runtime.sendMessage({ type: 'offscreenReady' }).catch(() => {});
