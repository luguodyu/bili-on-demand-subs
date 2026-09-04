// 共享纯函数测试（node 直接运行，不依赖浏览器）
const assert = require('assert');
const B = require('../extension/shared-subs.js');

let n = 0;
function t(name, fn) {
  fn();
  console.log('  PASS', name);
  n++;
}

// ---------- parseSubtitleText ----------
const SRT_SAMPLE = `1
00:00:01,000 --> 00:00:03,500
你好，世界。

2
00:00:04,000 --> 00:00:06,000
第二句字幕内容
第二行。

3
00:01:00.250 --> 00:01:02.750
带点号时间轴的VTT风格
`;
t('parseSubtitleText: 基本解析', () => {
  const segs = B.parseSubtitleText(SRT_SAMPLE);
  assert.strictEqual(segs.length, 3);
  assert.deepStrictEqual(segs[0], { start: 1, end: 3.5, text: '你好，世界。' });
  assert.strictEqual(segs[1].text, '第二句字幕内容\n第二行。');
  assert.strictEqual(segs[2].start, 60.25); // 点号毫秒 + 分位
});
t('parseSubtitleText: 乱序块按 start 排序', () => {
  const segs = B.parseSubtitleText('1\n00:00:10,000 --> 00:00:11,000\n后\n\n2\n00:00:01,000 --> 00:00:02,000\n前');
  assert.strictEqual(segs[0].text, '前');
  assert.strictEqual(segs[1].text, '后');
});
t('parseSubtitleText: 空/垃圾输入', () => {
  assert.strictEqual(B.parseSubtitleText('').length, 0);
  assert.strictEqual(B.parseSubtitleText('随便一段话\n没有时间轴').length, 0);
  assert.strictEqual(B.parseSubtitleText('WEBVTT\n\nNOTE x\n\n1\n00:00:01,000 --> 00:00:02,000\n有效').length, 1);
});

// ---------- segsToSRT 往返 ----------
t('segsToSRT: 格式与往返一致', () => {
  const segs = [
    { start: 0, end: 1.5, text: '第一句' },
    { start: 3661.25, end: 3662, text: '跨小时' }
  ];
  const srt = B.segsToSRT(segs);
  assert.ok(srt.includes('00:00:00,000 --> 00:00:01,500'));
  assert.ok(srt.includes('01:01:01,250 --> 01:01:02,000'));
  const back = B.parseSubtitleText(srt);
  assert.strictEqual(back.length, 2);
  assert.strictEqual(back[0].text, '第一句');
  assert.strictEqual(back[1].start, 3661.25);
});

// ---------- segAt ----------
const SEGS = [
  { start: 0, end: 2, text: 'A' },
  { start: 3, end: 5, text: 'B' },
  { start: 5.5, end: 8, text: 'C' }
];
t('segAt: 区间边界语义 [start, end)', () => {
  assert.strictEqual(B.segAt(SEGS, -1), -1);
  assert.strictEqual(B.segAt(SEGS, 0), 0);
  assert.strictEqual(B.segAt(SEGS, 1.999), 0);
  assert.strictEqual(B.segAt(SEGS, 2), -1);     // end 不含
  assert.strictEqual(B.segAt(SEGS, 3), 1);
  assert.strictEqual(B.segAt(SEGS, 5), -1);
  assert.strictEqual(B.segAt(SEGS, 5.5), 2);
  assert.strictEqual(B.segAt(SEGS, 8), -1);
  assert.strictEqual(B.segAt(SEGS, 100), -1);
});
t('segAt: 空输入', () => {
  assert.strictEqual(B.segAt([], 1), -1);
  assert.strictEqual(B.segAt(null, 1), -1);
});

// ---------- controlsAutoHide（全屏按钮隐藏判定：方案A = 播放中永不显示） ----------
t('controlsAutoHide: 全屏且播放中即隐藏（与空闲时长/鼠标活动无关）', () => {
  assert.strictEqual(B.controlsAutoHide({ playing: true, inFullscreen: true }), true);
  assert.strictEqual(B.controlsAutoHide({ playing: true, inFullscreen: true, idleMs: 0 }), true);
  assert.strictEqual(B.controlsAutoHide({ playing: true, inFullscreen: true, idleMs: 99999 }), true);
});
t('controlsAutoHide: 非全屏或未播放一律不隐藏', () => {
  assert.strictEqual(B.controlsAutoHide({ playing: true, inFullscreen: false }), false); // 窗口模式
  assert.strictEqual(B.controlsAutoHide({ playing: false, inFullscreen: true }), false); // 暂停时显示按钮
  assert.strictEqual(B.controlsAutoHide({ playing: false, inFullscreen: false }), false);
  assert.strictEqual(B.controlsAutoHide({}), false);
  assert.strictEqual(B.controlsAutoHide(null), false);
});

console.log('----');
console.log('JS TESTS PASSED:', n);
