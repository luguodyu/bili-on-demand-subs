// 后台服务（Service Worker）v0.4：按需字幕任务调度 + 消息中继
// 角色：记录任务来源标签页 -> 保证离屏页存活 -> 把离屏进度/结果转发回页面
importScripts('shared-subs.js');

let job = null;            // { tabId, cid }
let keepaliveTimer = null;

const OFFSCREEN_REASON = 'USER_MEDIA'; // 音频处理

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument().catch(() => false);
  if (has) return true;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: [OFFSCREEN_REASON],
    justification: '下载并转录B站视频音频，生成字幕文件'
  });
  // 等离屏页注册好监听器
  await new Promise((resolve) => {
    const timer = setTimeout(finish, 6000);
    function finish() { clearTimeout(timer); chrome.runtime.onMessage.removeListener(onMsg); resolve(); }
    function onMsg(msg) { if (msg && msg.type === 'offscreenReady') finish(); }
    chrome.runtime.onMessage.addListener(onMsg);
  });
  return true;
}

function startKeepalive() {
  stopKeepalive();
  keepaliveTimer = setInterval(() => {
    if (!job) { stopKeepalive(); return; }
    chrome.runtime.sendMessage({ type: 'keepalive' }).catch(() => {});
  }, 10000);
}
function stopKeepalive() {
  if (keepaliveTimer) { clearInterval(keepaliveTimer); keepaliveTimer = null; }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg !== 'object') return;

  switch (msg.type) {
    case 'makeSub': {
      const tabId = sender.tab ? sender.tab.id : null;
      if (tabId != null && msg.cid) {
        job = { tabId, cid: msg.cid };
        startKeepalive();
      }
      // 内容脚本广播的消息离屏页可能已直接收到；这里兜底：确保离屏页存在并再送一次（离屏页按 cid 去重）
      ensureOffscreen()
        .then(() => chrome.runtime.sendMessage(msg).catch(() => {}))
        .catch((e) => console.error('离屏页启动失败:', e));
      sendResponse({ ok: true });
      return true;
    }
    case 'makeProgress':
    case 'makeDone': {
      // 来自离屏页 -> 转发给任务标签页（makeDone 必须先转发再清任务，否则通知丢失）
      if (sender.tab) return;
      if (msg.type === 'makeDone') {
        const tabId = job ? job.tabId : null;
        stopKeepalive();
        job = null;
        if (tabId != null) chrome.tabs.sendMessage(tabId, msg).catch(() => {});
      } else if (job && job.tabId != null) {
        chrome.tabs.sendMessage(job.tabId, msg).catch(() => {});
      }
      return;
    }
    case 'cancelMake': {
      job = null;
      stopKeepalive();
      return; // 离屏页会直接收到内容脚本的广播
    }
    case 'queryJob': {
      sendResponse({ type: 'jobState', active: !!(job && job.cid === msg.cid), cid: job ? job.cid : null });
      return true;
    }
    case 'fetchAudioHere': {
      // 离屏页请求页面帮忙下载音频 -> 转发给任务标签页
      if (job && job.tabId != null) chrome.tabs.sendMessage(job.tabId, msg).catch(() => {});
      return;
    }
    case 'exportSub': {
      doExport(msg.cid).catch((e) => console.error('导出失败:', e));
      sendResponse({ ok: true });
      return true;
    }
    case 'storeSub': {
      // 离屏页无 chrome.storage：统一由后台落库
      storeSubRec(msg.rec).then(() => sendResponse({ ok: true })).catch((e) => {
        console.error('存储失败:', e);
        sendResponse({ ok: false, error: String(e) });
      });
      return true;
    }
    case 'openLibrary': {
      chrome.tabs.create({ url: chrome.runtime.getURL('library.html') }).catch(() => {});
      sendResponse({ ok: true });
      return true;
    }
    case 'audioChunk':
      // 内容 -> 离屏的分片流：后台不回应（让离屏页 sendResponse），不转发
      return;
    default:
      return;
  }
});

// 落库：sub:<cid> 记录 + sub:index 摘要
async function storeSubRec(rec) {
  if (!rec || !rec.cid || !Array.isArray(rec.segs)) throw new Error('rec 无效');
  await chrome.storage.local.set({ ['sub:' + rec.cid]: rec });
  const o = await chrome.storage.local.get('sub:index');
  const idx = Array.isArray(o['sub:index']) ? o['sub:index'] : [];
  const sum = { cid: rec.cid, bvid: rec.bvid, title: rec.title, createdAt: rec.createdAt, lines: rec.segs.length };
  const i = idx.findIndex((x) => x.cid === rec.cid);
  if (i >= 0) idx[i] = sum; else idx.unshift(sum);
  await chrome.storage.local.set({ 'sub:index': idx });
}

// 导出字幕为 .srt（content 侧请求时经后台执行；字幕库页面内可直接调用）
async function doExport(cid) {
  const o = await chrome.storage.local.get('sub:' + cid);
  const rec = o['sub:' + cid];
  if (!rec || !rec.segs || !rec.segs.length) return;
  const srt = BiliSubs.segsToSRT(rec.segs);
  const blob = new Blob(['\ufeff' + srt], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const safe = String(rec.title || rec.bvid || cid).replace(/[\\/:*?"<>|]/g, '_').slice(0, 80);
  try {
    await chrome.downloads.download({ url, filename: safe + '.srt', saveAs: true });
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
}
