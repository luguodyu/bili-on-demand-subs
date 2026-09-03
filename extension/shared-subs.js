// 共享纯函数（内容脚本 / 后台 / 字幕库页 / node 测试 共用）：不依赖任何浏览器 API
(function (root) {
  'use strict';
  const API = {};

  // 时间轴 [{start,end,text}] -> SRT 文本（秒 -> hh:mm:ss,mmm）
  API.segsToSRT = function (segs) {
    const fmt = (sec) => {
      sec = Math.max(0, sec);
      const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60), ms = Math.floor((sec * 1000) % 1000);
      return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0') + ',' + String(ms).padStart(3, '0');
    };
    return segs.map((g, i) =>
      (i + 1) + '\n' + fmt(g.start) + ' --> ' + fmt(g.end) + '\n' + (g.text || '').trim() + '\n'
    ).join('\n');
  };

  // SRT/VTT 文本 -> [{start,end,text}]（秒）
  API.parseSubtitleText = function (text) {
    const segs = [];
    const blocks = String(text || '').replace(/\r/g, '').split(/\n\s*\n/);
    for (const b of blocks) {
      const m = b.match(/(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})/);
      if (!m) continue;
      const toMs = (h, mi, s, ms) => (+h) * 3600000 + (+mi) * 60000 + (+s) * 1000 + (+(ms + '00'.slice(ms.length)));
      const lines = b.split('\n').slice(1).map((l) => l.trim()).filter((l) =>
        l && !l.includes('-->') && !l.startsWith('WEBVTT') && !l.startsWith('NOTE') && !/^\d+$/.test(l)
      );
      if (!lines.length) continue;
      segs.push({
        start: toMs(m[1], m[2], m[3], m[4]) / 1000,
        end: toMs(m[5], m[6], m[7], m[8]) / 1000,
        text: lines.join('\n')
      });
    }
    segs.sort((a, b) => a.start - b.start);
    return segs;
  };

  // 二分查找 t 时刻对应的句 index；无则 -1（区间为 [start, end)）
  API.segAt = function (segs, t) {
    if (!Array.isArray(segs) || !segs.length) return -1;
    let lo = 0, hi = segs.length - 1, res = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (segs[mid].start <= t) { res = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return (res >= 0 && t < segs[res].end) ? res : -1;
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  else root.BiliSubs = API;
})(typeof globalThis !== 'undefined' ? globalThis : this);
