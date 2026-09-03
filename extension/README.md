# B站按需字幕插件（v0.4）

**本地优先的按需字幕**：打开没有字幕的 B 站视频 → 点左下角「字幕」胶囊 → 生成字幕 → 播放时自动同步显示。字幕持久化保存，可管理、可导出。

## 架构

```
视频页角标/面板(sub-ui.js)
   │ 点击「生成字幕」→ 把 __playinfo__ 音频直链发给离屏页
   ▼
离屏页(offscreen.js) ──优先──▶ 本地服务(subtitle-server.py)
                                服务端自己下载音频(带Referer+UA, 绕开浏览器防盗链限制)
                                → faster-whisper 整片转录(时间轴JSON) → 存 chrome.storage
                          └──服务不可用──▶ 浏览器下载 + WASM whisper 回退(慢)
   ▼
字幕库(library.html): 列表 / 删除 / 导出 .srt；数据存 chrome.storage.local(无限容量, 重启不丢)
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `manifest.json` | MV3 清单（v0.4.1） |
| `sub-ui.js` | 视频页角标（⚪无/🟢有/⏳制作中）+ 操作面板 + 播放同步显示 |
| `shared-subs.js` | 纯函数（SRT 解析/生成/按时间查句），内容脚本/后台/库页/node 测试共用 |
| `offscreen.js` | 离屏页：任务编排（服务端优先，WASM 回退）、进度上报 |
| `background.js` | 任务调度：离屏页保活、进度转发、导出、字幕库入口 |
| `library.html/js` | 字幕库管理页 |
| `popup.html/js` | 弹窗：字幕库入口 |
| `transformers.js` + `ort/` | WASM 识别回退路径用（约 23MB） |

本地服务在项目根目录 `subtitle-server.py`（启动器 `启动字幕服务.vbs` 无窗口 / `.bat` 看日志）。测试在 `tests/`，一键 `tests\run_all.bat`。

## 使用

1. `edge://extensions` → 加载解压缩的扩展 → 选 `extension/` 文件夹
2. （推荐）双击 `启动字幕服务.vbs` 启动本地识别服务，等 10~20 秒
3. 打开无字幕的 B 站视频 → 左下角胶囊 → 「生成字幕」→ 等进度完成
4. 播放：字幕自动同步显示；拖进度条跟得上；全屏也显示

## 已知限制 / TODO

- 识别速度≈本地 faster-whisper 实际速度（这台机器约等于视频时长）；浏览器 WASM 回退慢约 3 倍
- whisper small 有同音字错误
- 上架 Edge 商店（需图标、商店审核）
- 测试设施：服务端 8 用例 + JS 6 用例，改动先跑 `tests\run_all.bat`

## 常见问题

- **面板提示"本地服务版本过旧"**：关闭旧 `.bat` 窗口 → 重新双击启动器
- **看错误日志**：`edge://extensions` → 插件详情 → 检查视图 → 离屏页面控制台；服务端问题看 `.bat` 窗口输出
- 旧"实时字幕"架构（WASM 流式识别）已从代码移除，保留在 git 历史
