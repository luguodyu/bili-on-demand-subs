// 按需字幕 UI（v0.4）：视频页角标 + 操作面板 + 播放同步显示
// 依赖：content.js 已在页面（各自独立命名空间，不冲突）
(() => {
  if (window.__biliSubUI) return; // 防重复注入
  window.__biliSubUI = true;

  const S = {
    bvid: null,
    cid: null,
    title: '',
    sub: null,          // 当前视频字幕记录 {segs:[{start,end,text}]}
    displayOn: true,    // 是否自动同步显示
    busy: false,
    progress: 0,
    lastKey: null,
    uiReady: false,
  };
  const INDEX_KEY = 'sub:index';
  const subKey = (cid) => 'sub:' + cid;

  let pillEl = null, panelEl = null, overlayEl = null;

  // ================= 工具 =================
  function isVideoPage() { return /^\/video\//.test(location.pathname); }
  function getBvid() {
    const m = location.pathname.match(/^\/video\/(BV[a-zA-Z0-9]+)/);
    return m ? m[1] : null;
  }
  function pageState() {
    try {
      const st = window.__INITIAL_STATE__;
      if (st && st.videoData && st.videoData.cid) return { cid: st.videoData.cid, title: st.videoData.title || '' };
    } catch (e) {}
    try {
      const info = window.__playinfo__;
      if (info && info.data && info.data.cid)
        return { cid: info.data.cid, title: document.title.replace(/_\s*(哔哩哔哩|bilibili).*$/, '').trim() };
    } catch (e) {}
    return null;
  }
  async function fetchCidFallback(bvid) {
    try {
      const r = await fetch('https://api.bilibili.com/x/web-interface/view?bvid=' + bvid, { credentials: 'include' });
      const j = await r.json();
      if (j && j.code === 0 && j.data && j.data.cid) return { cid: j.data.cid, title: j.data.title || '' };
    } catch (e) {}
    return null;
  }
  // 优先 __playinfo__（页面自带），拿不到再走 playurl 接口
  function audioUrlsFromPage() {
    const urls = [];
    try {
      const dash = window.__playinfo__ && window.__playinfo__.data && window.__playinfo__.data.dash;
      if (dash && Array.isArray(dash.audio)) {
        for (const a of dash.audio) {
          if (a.baseUrl) urls.push(a.baseUrl);
          if (Array.isArray(a.backupUrl)) for (const u of a.backupUrl) urls.push(u);
        }
      }
    } catch (e) {}
    return urls;
  }
  async function audioUrlsFallback() {
    try {
      const u = 'https://api.bilibili.com/x/player/playurl?bvid=' + S.bvid + '&cid=' + S.cid + '&fnval=16&fnver=0&fourk=1';
      const r = await fetch(u, { credentials: 'include' });
      const j = await r.json();
      const dash = j && j.data && j.data.dash;
      if (dash && Array.isArray(dash.audio)) {
        const out = [];
        for (const a of dash.audio) {
          if (a.baseUrl) out.push(a.baseUrl);
          if (Array.isArray(a.backupUrl)) for (const x of a.backupUrl) out.push(x);
        }
        return out;
      }
    } catch (e) {}
    return [];
  }

  // ================= 存储 =================
  async function readIndex() {
    const o = await chrome.storage.local.get(INDEX_KEY);
    return Array.isArray(o[INDEX_KEY]) ? o[INDEX_KEY] : [];
  }
  async function getSub(cid) {
    const o = await chrome.storage.local.get(subKey(cid));
    return o[subKey(cid)] || null;
  }
  async function saveSub(rec) {
    await chrome.storage.local.set({ [subKey(rec.cid)]: rec });
    const idx = await readIndex();
    const sum = { cid: rec.cid, bvid: rec.bvid, title: rec.title, createdAt: rec.createdAt, lines: rec.segs ? rec.segs.length : 0 };
    const i = idx.findIndex((x) => x.cid === rec.cid);
    if (i >= 0) idx[i] = sum; else idx.unshift(sum);
    await chrome.storage.local.set({ [INDEX_KEY]: idx });
  }
  async function deleteSub(cid) {
    await chrome.storage.local.remove(subKey(cid));
    const idx = await readIndex();
    await chrome.storage.local.set({ [INDEX_KEY]: idx.filter((x) => x.cid !== cid) });
  }

  // ================= DOM 搭建 =================
  function ensureUI() {
    if (S.uiReady) return;
    S.uiReady = true;
    const css = document.createElement('style');
    css.textContent = `
      #bili-sub-pill{position:fixed;left:16px;bottom:60px;z-index:2147483640;display:flex;align-items:center;gap:6px;
        background:rgba(20,20,20,.85);color:#fff;border-radius:999px;padding:6px 12px;font:13px/1.4 "Microsoft YaHei",sans-serif;
        cursor:pointer;user-select:none;box-shadow:0 2px 8px rgba(0,0,0,.35)}
      #bili-sub-pill .dot{width:8px;height:8px;border-radius:50%;background:#888;flex:none}
      #bili-sub-pill.st-ok .dot{background:#3ddc84}
      #bili-sub-pill.st-busy .dot{background:#f0c75e;animation:biliSubBlink 1s linear infinite}
      #bili-sub-pill.st-none .dot{background:#e05555}
      @keyframes biliSubBlink{50%{opacity:.25}}
      #bili-sub-panel{position:fixed;left:16px;bottom:108px;z-index:2147483641;width:310px;display:none;
        background:rgba(24,24,28,.97);color:#eee;border-radius:10px;padding:12px 14px;font:13px/1.6 "Microsoft YaHei",sans-serif;
        box-shadow:0 6px 24px rgba(0,0,0,.5)}
      #bili-sub-panel .vt{color:#fff;font-weight:bold;font-size:13px;word-break:break-all;margin-bottom:4px;max-height:3.2em;overflow:hidden}
      #bili-sub-panel .st{color:#b8b8c0;font-size:12px;white-space:pre-wrap}
      #bili-sub-panel button{background:#00a1d6;color:#fff;border:none;border-radius:6px;padding:5px 10px;margin:6px 6px 0 0;cursor:pointer;font-size:12px}
      #bili-sub-panel button.gray{background:#3a3a42}
      #bili-sub-panel button.red{background:#d64545}
      #bili-sub-panel button:disabled{opacity:.5;cursor:not-allowed}
      #bili-sub-panel .bar{height:6px;background:#333;border-radius:3px;margin-top:8px;overflow:hidden}
      #bili-sub-panel .bar>i{display:block;height:100%;width:0;background:#00a1d6;transition:width .3s}
      #bili-sub-sync{position:fixed;left:50%;bottom:10%;transform:translateX(-50%);max-width:70%;z-index:2147483647;
        background:rgba(0,0,0,.72);color:#fff;font-size:20px;line-height:1.5;padding:8px 16px;border-radius:8px;
        text-align:center;pointer-events:none;display:none;font-family:"Microsoft YaHei","PingFang SC",sans-serif}
    `;
    (document.head || document.documentElement).appendChild(css);

    pillEl = document.createElement('div');
    pillEl.id = 'bili-sub-pill';
    pillEl.title = 'B站按需字幕';
    pillEl.innerHTML = '<span class="dot"></span><span class="label">字幕</span>';
    pillEl.addEventListener('click', () => { refreshPanel(); panelEl.style.display = panelEl.style.display === 'block' ? 'none' : 'block'; });

    panelEl = document.createElement('div');
    panelEl.id = 'bili-sub-panel';
    panelEl.innerHTML =
      '<div class="vt"></div>' +
      '<div class="st"></div>' +
      '<div class="acts"></div>' +
      '<div class="bar" style="display:none"><i></i></div>' +
      '<div class="pct st" style="display:none"></div>';

    overlayEl = document.createElement('div');
    overlayEl.id = 'bili-sub-sync';

    document.addEventListener('fullscreenchange', anchorAll);
    setInterval(anchorAll, 1000);
    anchorAll();
  }

  function anchorAll() {
    for (const el of [pillEl, panelEl, overlayEl]) {
      if (!el) continue;
      let target = document.body;
      const fs = document.fullscreenElement;
      if (fs) {
        target = (fs.tagName === 'VIDEO' || fs.tagName === 'CANVAS') ? (fs.parentElement || document.body) : fs;
      } else {
        const player = document.querySelector('.bpx-player-container');
        if (player) {
          const cs = getComputedStyle(player);
          if (cs.position === 'fixed') target = player; // B站网页全屏
        }
      }
      if (el.parentNode !== target) target.appendChild(el);
    }
  }

  // ================= 状态与渲染 =================
  function setPillState() {
    if (!pillEl) return;
    pillEl.classList.remove('st-ok', 'st-busy', 'st-none');
    const label = pillEl.querySelector('.label');
    if (S.busy) { pillEl.classList.add('st-busy'); label.textContent = '字幕 ' + Math.round(S.progress) + '%'; }
    else if (S.sub) { pillEl.classList.add('st-ok'); label.textContent = '字幕已就绪'; }
    else { pillEl.classList.add('st-none'); label.textContent = '无字幕 · 点此制作'; }
  }

  function refreshPanel() {
    if (!panelEl) return;
    const vt = panelEl.querySelector('.vt');
    const st = panelEl.querySelector('.st');
    const acts = panelEl.querySelector('.acts');
    const bar = panelEl.querySelector('.bar');
    const pct = panelEl.querySelector('.pct');
    vt.textContent = S.title || S.bvid || '';
    if (S.busy) {
      st.textContent = S.stageText || '处理中…';
      bar.style.display = 'block';
      bar.querySelector('i').style.width = Math.max(2, S.progress) + '%';
      pct.style.display = 'block';
      pct.textContent = Math.round(S.progress) + '%';
      acts.innerHTML = '<button class="red" id="bili-sub-cancel">取消</button>';
      const btn = document.getElementById('bili-sub-cancel');
      btn.addEventListener('click', () => {
        chrome.runtime.sendMessage({ type: 'cancelMake', cid: S.cid }).catch(() => {});
        S.busy = false; setPillState(); refreshPanel();
      });
      return;
    }
    bar.style.display = 'none';
    pct.style.display = 'none';
    if (S.sub && S.sub.segs && S.sub.segs.length) {
      const d = new Date(S.sub.createdAt || Date.now());
      st.textContent = '已有字幕：' + S.sub.segs.length + ' 句 · ' + d.toLocaleDateString() + '\n播放时会自动同步显示';
      acts.innerHTML =
        '<button id="bili-sub-toggle">' + (S.displayOn ? '隐藏字幕' : '显示字幕') + '</button>' +
        '<button id="bili-sub-remake">重新制作</button>' +
        '<button class="gray" id="bili-sub-export">导出 .srt</button>' +
        '<button class="red" id="bili-sub-del">删除字幕</button>' +
        '<button class="gray" id="bili-sub-lib">字幕库</button>';
    } else {
      st.textContent = '此视频还没有字幕文件\n制作约需几分钟，完成后自动保存';
      acts.innerHTML =
        '<button id="bili-sub-make">生成字幕</button>' +
        '<button class="gray" id="bili-sub-import">导入字幕文件</button>' +
        '<button class="gray" id="bili-sub-lib">字幕库</button>';
    }
    const bind = (id, fn) => {
      const b = document.getElementById(id);
      if (b) b.addEventListener('click', fn);
    };
    bind('bili-sub-toggle', () => { S.displayOn = !S.displayOn; refreshPanel(); });
    bind('bili-sub-make', startMake);
    bind('bili-sub-remake', startMake);
    bind('bili-sub-del', async () => {
      if (!confirm('删除此视频的字幕文件？')) return;
      await deleteSub(S.cid);
      S.sub = null; setPillState(); refreshPanel();
    });
    bind('bili-sub-export', () => { chrome.runtime.sendMessage({ type: 'exportSub', cid: S.cid }).catch(() => {}); });
    bind('bili-sub-import', importSubtitleFile);
    bind('bili-sub-lib', () => { chrome.runtime.sendMessage({ type: 'openLibrary' }).catch(() => {}); });
  }

  // ================= 制作 =================
  async function startMake() {
    if (!S.cid) return;
    S.busy = true; S.progress = 0; S.stageText = '正在获取音频…';
    setPillState(); refreshPanel();
    let urls = audioUrlsFromPage();
    if (!urls.length) urls = await audioUrlsFallback();
    if (!urls.length) {
      S.stageText = '获取音频失败：请刷新页面（确保视频能播放）后重试';
      S.busy = false; setPillState(); refreshPanel(); return;
    }
    try {
      await chrome.runtime.sendMessage({
        type: 'makeSub', cid: S.cid, bvid: S.bvid, title: S.title,
        pageUrl: location.href, audioUrls: urls
      });
    } catch (e) {
      S.busy = false; S.stageText = '启动失败: ' + e.message;
      setPillState(); refreshPanel();
    }
  }

  // 页内下载音频（referer 正确）+ 分块传给离屏页；CORS 失败则让离屏页直接下
  async function pageFetchAndSendAudio(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error('下载音频 HTTP ' + r.status);
    const buf = await r.arrayBuffer();
    const CH = 1.5 * 1024 * 1024;
    for (let off = 0; off < buf.byteLength; off += CH) {
      const piece = buf.slice(off, Math.min(off + CH, buf.byteLength));
      const resp = await chrome.runtime.sendMessage({
        type: 'audioChunk', cid: S.cid,
        seq: off / CH, last: off + CH >= buf.byteLength, data: piece
      });
      if (!resp || !resp.ok) throw new Error('分片传输失败');
    }
    return buf.byteLength;
  }

  // ================= 导入字幕文件 =================
  function importSubtitleFile() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.srt,.vtt';
    input.onchange = async () => {
      const f = input.files && input.files[0];
      if (!f) return;
      const text = await f.text();
      const segs = BiliSubs.parseSubtitleText(text);
      if (!segs.length) { alert('未能从文件中解析出字幕'); return; }
      const rec = { cid: S.cid, bvid: S.bvid, title: S.title || f.name, pageUrl: location.href, createdAt: Date.now(), source: 'import', lang: 'zh', segs };
      await saveSub(rec);
      S.sub = rec; setPillState(); refreshPanel();
    };
    input.click();
  }

  // ================= 播放同步显示 =================
  function displayTick() {
    if (!overlayEl) return;
    if (!S.cid || !S.sub || !S.displayOn) { overlayEl.style.display = 'none'; return; }
    const video = document.querySelector('video');
    if (!video || video.paused) return; // 暂停时保持当前字幕（不闪）
    const t = video.currentTime;
    const i = BiliSubs.segAt(S.sub.segs, t);
    if (i >= 0) {
      const seg = S.sub.segs[i];
      if (overlayEl.textContent !== seg.text) overlayEl.textContent = seg.text;
      overlayEl.style.display = 'block';
    } else {
      overlayEl.style.display = 'none';
    }
  }

  // ================= 主循环与消息 =================
  async function tick() {
    if (!isVideoPage()) {
      if (pillEl) pillEl.style.display = 'none';
      return;
    }
    ensureUI();
    pillEl.style.display = 'flex';
    const bvid = getBvid();
    if (!bvid) return;
    let st = pageState();
    if (!st || !st.cid) st = await fetchCidFallback(bvid);
    if (!st) return;
    const key = bvid + ':' + st.cid;
    if (key !== S.lastKey) {
      S.lastKey = key;
      S.bvid = bvid; S.cid = st.cid; S.title = st.title;
      S.sub = await getSub(st.cid);
      S.displayOn = true;
      S.busy = false; S.progress = 0;
      setPillState();
      if (panelEl) panelEl.style.display = 'none';
      queryJob();
    }
  }

  async function queryJob() {
    try {
      const resp = await chrome.runtime.sendMessage({ type: 'queryJob', cid: S.cid });
      if (resp && resp.active && resp.cid === S.cid && !S.sub) {
        S.busy = true; S.progress = 1; S.stageText = '任务进行中…';
        setPillState();
      }
    } catch (e) {}
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || !S.cid) return;
    if (msg.type === 'makeProgress' && msg.cid === S.cid) {
      S.busy = true;
      S.progress = msg.pct || 0;
      S.stageText = msg.msg || '';
      setPillState();
      if (panelEl && panelEl.style.display === 'block') refreshPanel();
    } else if (msg.type === 'makeDone' && msg.cid === S.cid) {
      S.busy = false;
      if (msg.ok) {
        getSub(S.cid).then((rec) => { S.sub = rec; setPillState(); if (panelEl && panelEl.style.display === 'block') refreshPanel(); });
      } else {
        S.stageText = msg.error || '制作失败';
        if (panelEl && panelEl.style.display === 'block') refreshPanel();
      }
    }
  });

  // 兜底：角标消息处理里可能需要页内下载（由 offscreen 请求时触发）
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === 'fetchAudioHere' && msg.cid === S.cid && msg.url) {
      (async () => {
        try {
          await pageFetchAndSendAudio(msg.url);
          chrome.runtime.sendMessage({ type: 'audioFetched', cid: S.cid, ok: true }).catch(() => {});
        } catch (e) {
          chrome.runtime.sendMessage({ type: 'audioFetched', cid: S.cid, ok: false, error: e.message }).catch(() => {});
        }
      })();
    }
  });

  setInterval(tick, 1500);
  setInterval(displayTick, 200);
  tick();
})();
