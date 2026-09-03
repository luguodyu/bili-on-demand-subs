// 字幕库管理页：列表 / 删除 / 导出 .srt
const INDEX_KEY = 'sub:index';
const listEl = document.getElementById('list');
const emptyEl = document.getElementById('empty');
const countEl = document.getElementById('count');

function fmtTime(ms) {
  const d = new Date(ms);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') + ' ' +
    String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
function fmtDur(sec) {
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  return (m > 0 ? m + '分' : '') + s + '秒';
}

async function readIndex() {
  const o = await chrome.storage.local.get(INDEX_KEY);
  return Array.isArray(o[INDEX_KEY]) ? o[INDEX_KEY] : [];
}

async function render() {
  const idx = await readIndex();
  countEl.textContent = idx.length ? '共 ' + idx.length + ' 条' : '';
  emptyEl.style.display = idx.length ? 'none' : 'block';
  listEl.innerHTML = '';
  for (const it of idx) {
    const row = document.createElement('div');
    row.className = 'item';
    const info = document.createElement('div');
    info.className = 'info';
    const t = document.createElement('div');
    t.className = 't';
    t.textContent = it.title || it.bvid || ('视频 ' + it.cid);
    const m = document.createElement('div');
    m.className = 'm';
    m.textContent = (it.bvid || '') + (it.bvid ? ' · ' : '') + fmtTime(it.createdAt) + ' · ' + (it.lines || 0) + ' 句';
    info.appendChild(t);
    info.appendChild(m);
    const btnExp = document.createElement('button');
    btnExp.className = 'btn-exp';
    btnExp.textContent = '导出 .srt';
    btnExp.addEventListener('click', () => exportSRT(it.cid));
    const btnDel = document.createElement('button');
    btnDel.className = 'btn-del';
    btnDel.textContent = '删除';
    btnDel.addEventListener('click', async () => {
      if (!confirm('删除「' + (it.title || it.cid) + '」的字幕？')) return;
      await chrome.storage.local.remove('sub:' + it.cid);
      await chrome.storage.local.set({ [INDEX_KEY]: idx.filter((x) => x.cid !== it.cid) });
      render();
    });
    row.appendChild(info);
    row.appendChild(btnExp);
    row.appendChild(btnDel);
    listEl.appendChild(row);
  }
}

async function exportSRT(cid) {
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

render();
