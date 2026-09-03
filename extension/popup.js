// 弹窗 v0.4：入口 = 字幕库管理
const statusEl = document.getElementById('status');

async function refresh() {
  try {
    const o = await chrome.storage.local.get('sub:index');
    const idx = Array.isArray(o['sub:index']) ? o['sub:index'] : [];
    statusEl.textContent = idx.length ? '字幕库：共 ' + idx.length + ' 条字幕' : '字幕库：暂无字幕';
  } catch (e) {
    statusEl.textContent = '';
  }
}

document.getElementById('lib').addEventListener('click', () => {
  chrome.tabs.create({ url: chrome.runtime.getURL('library.html') });
});

refresh();
