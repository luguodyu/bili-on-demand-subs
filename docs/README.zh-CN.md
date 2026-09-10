# B站按需字幕（bili-on-demand-subs）

> 本地优先、按需为 B 站无字幕视频生成字幕：识别在你的电脑上完成，字幕全部存在浏览器本地存储里。

浏览器插件（Manifest V3）+ 可选的本地语音识别服务。打开没有字幕的 B 站视频 → 点左下角「字幕」胶囊 → 生成字幕 → 播放时自动同步显示。字幕持久化保存，可管理、可导出 `.srt`。

**当前版本：** 插件 `v0.5.2` · 本地服务 `API v3` · 服务端实测 64 位 Windows 10/11，插件测于 Chrome/Edge。

> 演示 GIF / 截图：录制与存放指引见 [`docs/demo.md`](docs/demo.md)。

---

## 为什么这么设计

浏览器里拿不到干净的 B 站音频是难点：媒体直链带签名，普通 `fetch` 会被防盗链（Referer）拒绝。本项目分两层绕开：

```
视频页 (sub-ui.js)
   │ 点「生成字幕」→ 把 __playinfo__ 音频直链发给离屏页
   ▼
离屏页 (offscreen.js) ──优先本地服务──▶ subtitle-server.py
                                 服务端自己下载音频（带 Referer + UA，绕开浏览器防盗链）
                                 → faster-whisper 整片转录
                                 → 字幕 JSON 存 chrome.storage
                           └──服务不可用──▶ 浏览器内回退：
                              浏览器下载音频 → WASM whisper（较慢）
   ▼
字幕库 (library.html)：列表 / 删除 / 导出 .srt
   存储：chrome.storage.local（无限容量、重启不丢）
```

插件总是先探测本地服务（`/health`）。服务没起时：装过 **Native Messaging 原生宿主**（`subtitle-native.exe`）则随浏览器自动拉起、浏览器关闭自动退出；没装宿主则回退到浏览器内 WASM 识别。

## 功能

- ⚡ **本地优先转写**：音频不出本机，无 API key、无按量费用
- 🎯 **播放同步**：进度单调上报、拖动进度条跟得上、全屏也显示
- 💾 **字幕库持久化**：按视频存储（`sub:<cid>`），独立页面列表/删除/导出 `.srt`
- 🔁 **双识别链路**：本地 faster-whisper small(int8) + 浏览器 WASM 回退
- 🔌 **零安装自动拉起**：原生宿主随浏览器启停本地服务（可选，一次性注册）

## 快速开始

### 0. 一次性还原 WASM 运行时（克隆后必须）

约 23MB 的 WASM 运行时**不入 git**，克隆后先执行：

```bash
python tools/fetch_vendor.py           # 还原 extension/transformers.js + extension/ort/*
python tools/fetch_vendor.py --check   # 校验
```

### 1. 本地识别服务（推荐）

**方式 A — 便携版（最终用户，免装 Python）**：从 [Releases](...) 下载 `tools/build_package.py` 打出的便携 zip，解压后双击 `启动字幕服务.bat`，或运行 `安装-浏览器自动拉起.bat` 一次性注册（之后浏览器自动拉起）。

**方式 B — 源码运行（开发者）**：需要 Python 3.10+ 及 `subtitle-server.py` 的依赖（faster-whisper / ctranslate2 / flask / av / opencc 等）。首次运行会自动下载 small 模型（约 460MB）：

```bash
python subtitle-server.py        # 等到出现 "Running on http://127.0.0.1:8765"
```

### 2. 加载插件

1. 打开 `edge://extensions`（或 `chrome://extensions`）→ 打开「开发者模式」→「加载已解压的扩展程序」→ 选 `extension/` 文件夹
2. （可选）在便携版目录运行安装脚本并粘贴插件 ID，实现浏览器自动拉起服务
3. 打开任意无字幕 B 站视频 → 左下角「字幕」胶囊 →「生成字幕」→ 等进度完成
4. 播放：字幕自动同步显示、跟得上拖动、全屏可见

> 没有本地服务时走 WASM 回退：约慢 3 倍，且"无服务下载→WASM 转写"链路目前是最少被验证的组合（见[已知限制](#已知限制)）。

## 仓库结构

```
extension/          MV3 插件源码（从此目录加载）
  shared-subs.js     纯函数：SRT 解析/生成、按时间查句
  sub-ui.js          视频页角标/面板 + 播放同步显示
  offscreen.js       任务编排（服务优先、WASM 回退）、进度上报
  background.js      任务调度、保活、导出、字幕库入口
  library.html/js    字幕库管理页
subtitle-server.py  本地 Flask 识别服务（127.0.0.1:8765）
tests/              回归测试（服务端 + 共享 JS）
tools/
  build_package.py   打便携 zip（引擎 + 模型 + 原生宿主）
  fetch_vendor.py    还原不入库的 WASM 运行时
  native_host.cs     Native Messaging 宿主源码（自动拉起）
docs/               架构笔记、英文 README、演示素材
```

## 测试

```bash
python tests/test_server.py     # 服务端：进程内路由 + 真实转写（需模型，约 1~3 分钟）
node tests/test_shared.js       # 共享 JS 纯函数（8 例）
```

Windows 下可用 `tests/run_all.bat` 一键跑两套。

## 已知限制

- 识别速度 ≈ 本机 faster-whisper 速度（中端 CPU 约等于视频时长）；浏览器 WASM 回退慢约 3 倍
- `whisper small` 中文偶尔有同音字错误
- 范围是 B 站网页视频；不针对其他站点
- 纯浏览器（无服务）链路在上次重构后未做过端到端复验——当作尽力而为的回退
- 尚未上架商店（Edge/Chrome）

## Roadmap

- [ ] 在干净环境复验「无服务：下载 → WASM 转写」链路
- [ ] 便携包"无 Python 机器"最终冒烟
- [ ] Edge 商店上架（需图标 + 审核）
- [ ] CI：每次推送跑 JS 测试 + lint（服务端套件需 ~460MB 模型，保持手动）

## 合规与隐私

个人 / 学习用途。不上传任何数据：音频交给本地服务或浏览器内处理，字幕存在 `chrome.storage.local`。本项目与 B 站无关联、未经其背书，仅使用公开播放器接口。

## 第三方声明

transformers.js（Apache-2.0）与 onnxruntime-web（MIT）在运行时引入但不入库，见 [`docs/THIRD_PARTY_NOTICES.md`](docs/THIRD_PARTY_NOTICES.md)。

## License

[MIT](LICENSE)

---

[English README → README.md](../README.md)
