# -*- coding: utf-8 -*-
# 本地字幕服务 v0.4（按需字幕后端）：
#   接收浏览器传来的 16kHz 单声道 float32 PCM -> faster-whisper 整片转录 -> 时间轴 JSON
#
# 启动:  python subtitle-server.py   （或双击 启动字幕服务.vbs，无窗口）
# 接口:
#   GET  /health                   健康检查
#   POST /transcribe-full          原始 PCM(16kHz f32le) -> {"segments":[{start,end,text}],...}
#   POST /transcribe               旧接口(webm/文件) -> {"text":...} （兼容保留）
#
# 模型目录优先用项目内 hf-cache（离线可用）；找不到则回退在线下载（走 hf-mirror）
import sys
import os
import glob
import time
import json
import uuid
import tempfile
import subprocess
import threading
import urllib.request
import urllib.error
import urllib.parse

# 模型下载走国内镜像（必须在 import faster_whisper 之前设置）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, request, jsonify
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from opencc import OpenCC

app = Flask(__name__)
app.json.ensure_ascii = False  # 中文直接显示，不转义

# 接口版本：扩展用它判断服务端是否配套。
# v3 起 /progress 增加 taskId、新增 /cancel、/transcribe-url 接受 duration。
# 改动接口语义时务必递增，否则新旧组合会静默跑错版本（历史踩过这个坑）。
SERVER_API_VERSION = 3

# 客户端心跳超时：客户端一旦开始轮询 /progress，超过这个时长不再来读，
# 即视为已放弃（浏览器崩溃/扩展被卸载/请求被 abort），当前转写会被放弃。
# 只对「曾经轮询过」的任务生效，直接 curl 调用不受影响。
CLIENT_STALE_SEC = 15.0

# 转写串行锁：同一时刻只允许一个任务真正占用模型
TRANSCRIBE_LOCK = threading.Lock()


class TaskTracker:
    """单个转写任务的进度与存活状态。

    历史上进度是模块级全局 dict，取消/断连后旧任务仍会继续写它，
    导致新任务读到旧任务的 duration，前端算出 525% 之类的越界值。
    改为每个任务一个 tracker 后，这种串扰在结构上不可能发生。
    """

    def __init__(self, task_id):
        self.task_id = task_id
        self.lock = threading.Lock()
        self.end = 0.0
        self.duration = 0.0
        self.ts = time.time()
        self._cancel = threading.Event()
        self._started = time.time()
        self._last_poll = 0.0
        self._ever_polled = False

    # —— 进度 ——
    def set_duration(self, duration):
        with self.lock:
            self.duration = float(duration)
            self.ts = time.time()

    def advance(self, end):
        """进度只增不减、封顶在 duration 内"""
        end = float(end)
        with self.lock:
            if self.duration > 0:
                end = min(end, self.duration)
            if end > self.end:
                self.end = end
                self.ts = time.time()

    def reset_progress(self):
        """把进度归零（下载阶段的占位进度 → 转写阶段重新从 0 单调爬）"""
        with self.lock:
            self.end = 0.0
            self.ts = time.time()

    def finish(self):
        with self.lock:
            if self.duration > 0:
                self.end = self.duration
            self.ts = time.time()

    def snapshot(self):
        with self.lock:
            return {"taskId": self.task_id, "end": self.end,
                    "duration": self.duration, "ts": self.ts}

    # —— 存活 ——
    def mark_polled(self):
        """被 /progress 读到一次，作为客户端仍在等待的证据"""
        with self.lock:
            self._ever_polled = True
            self._last_poll = time.time()

    def cancel(self):
        self._cancel.set()

    def cancelled(self):
        return self._cancel.is_set()

    def should_abandon(self):
        """客户端是否已不再等待（显式取消，或心跳停止）"""
        if self._cancel.is_set():
            return True
        with self.lock:
            if self._ever_polled and (time.time() - self._last_poll) > CLIENT_STALE_SEC:
                return True
        return False


class ProgressHub:
    """当前任务的 tracker 持有者：新任务开始即作废仍在跑的旧任务"""

    def __init__(self):
        self._lock = threading.Lock()
        self._current = None

    def begin(self, task_id):
        """开始新任务，并作废上一个（若仍有旧任务在跑，它会在下一个分段边界自行退出）"""
        with self._lock:
            old = self._current
            tracker = TaskTracker(task_id)
            self._current = tracker
        if old is not None:
            old.cancel()
        return tracker

    def current(self):
        with self._lock:
            return self._current

    def clear(self, tracker):
        with self._lock:
            if self._current is tracker:
                self._current = None

    def snapshot(self):
        t = self.current()
        if t is None:
            return {"taskId": None, "end": 0.0, "duration": 0.0, "ts": 0.0}
        return t.snapshot()


HUB = ProgressHub()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def find_local_model():
    """优先用便携包布局 models/（与脚本同级）；开发布局回退到项目内 hf-cache 快照"""
    for base, pat in (
        (BASE_DIR, os.path.join("models", "faster-whisper-*")),           # 便携版
        (BASE_DIR, os.path.join("hf-cache", "hub", "models--Systran--faster-whisper-*", "snapshots", "*")),  # 开发版
    ):
        cands = sorted(glob.glob(os.path.join(base, pat)))
        if cands:
            return cands[0]
    return None


MODEL_DIR = find_local_model() or "small"
print(f"Loading faster-whisper model: {MODEL_DIR} ...", flush=True)
model = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8")
print("Model ready.", flush=True)

# 繁体 -> 简体（whisper 中文输出常带繁体）
cc = OpenCC("t2s")

# 引导 whisper 输出带标点的简体中文
DEFAULT_PROMPT = (
    "以下是一段中文演讲的转录文本，请用简体中文并带标点符号转录："
    "大家好，今天我们讨论一个重要的话题。谢谢大家！"
)

# ffmpeg 兜底路径（旧接口 webm 解码用；transcribe-full 不需要）
FFMPEG_CANDS = [
    r"D:\edge\BBDownG_win\BBDownG_win\ffmpeg.exe",
    "ffmpeg",
]


@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/health")
def health():
    return jsonify({"status": "ok", "model": "small", "version": SERVER_API_VERSION})


@app.route("/progress")
def progress():
    """浏览器轮询进度。同时充当心跳：读到一次即登记客户端仍在等待。"""
    t = HUB.current()
    if t is not None:
        t.mark_polled()
    return jsonify(HUB.snapshot())


@app.route("/cancel", methods=["POST", "OPTIONS"])
def cancel():
    """显式取消当前任务（扩展在 abort 请求前调用）。

    body 可带 {"taskId": "..."}；不带则取消当前任务。
    取消后服务端会在下一个分段边界停止转写，不再空烧 CPU。
    """
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    want = data.get("taskId")
    t = HUB.current()
    if t is None:
        return jsonify({"ok": True, "cancelled": None, "note": "no active task"})
    if want and want != t.task_id:
        return jsonify({"ok": True, "cancelled": None, "note": "task id mismatch"})
    t.cancel()
    return jsonify({"ok": True, "cancelled": t.task_id})


def download_to_temp(url, suffix=".m4s", on_progress=None):
    """带 B 站 referer 下载音频直链到临时文件（浏览器 fetch 无法设置 Referer，这里可以）

    on_progress(fraction)：已读字节 / Content-Length，取值 0.0~1.0。
    长视频的整片音频下载要一两分钟，这段此前完全不报进度，
    前端只能把进度条钉在 15% 不动——"进度停滞"的主要来源。
    """
    req = urllib.request.Request(
        url,
        headers={
            "Referer": "https://www.bilibili.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        },
    )
    try:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or suffix
    except Exception:
        ext = suffix
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    # timeout 同时约束连接与读取：不可达镜像（mcdn 等）快速失败，不拖住整个任务
    with urllib.request.urlopen(req, timeout=12) as resp, open(tmp_path, "wb") as f:
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        got = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if on_progress is not None and total > 0:
                on_progress(got / total)
    if os.path.getsize(tmp_path) < 10000:
        os.unlink(tmp_path)
        return None
    return tmp_path


def _url_rank(u):
    """可达性排序：bilivideo.com 官方源优先，mcdn 类镜像最后（实测不可达会挂起）"""
    try:
        host = urllib.parse.urlparse(u).hostname or ""
    except Exception:
        return 1
    if "mcdn." in host or ":8082" in u:
        return 2
    if "bilivideo.com" in host or "bilivideo.cn" in host:
        return 0
    return 1


def run_segments(segments, tracker):
    """消费 faster-whisper 的分段生成器，产出结果并推进进度。

    每处理完一个分段检查一次是否该放弃——这是「取消后还在空烧 CPU」的修复点：
    客户端 abort 或心跳消失后，这里会在下一个分段边界停下。
    返回 (out, abandoned)。
    """
    out = []
    for seg in segments:
        if tracker.should_abandon():
            return out, True
        text = cc.convert((seg.text or "").strip())
        if text:
            out.append({"start": round(float(seg.start), 2),
                        "end": round(float(seg.end), 2),
                        "text": text})
        tracker.advance(getattr(seg, "end", 0) or 0)
    tracker.finish()
    return out, False


@app.route("/transcribe-url", methods=["POST", "OPTIONS"])
def transcribe_url():
    """整片转录 v2：body = {"urls":[...]}，服务端自己下载音频（带正确Referer）再转录"""
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}  # force：兼容 text/plain（免 CORS 预检）
    urls = [u for u in (data.get("urls") or []) if u]
    if not urls:
        return jsonify({"error": "no url"}), 400
    urls.sort(key=_url_rank)  # 官方源优先，避免卡在不可达镜像上

    tracker = HUB.begin(uuid.uuid4().hex[:8])

    # 先按前端给的时长占位，让下载阶段也有进度可报；
    # 解码完成后会用精确采样数覆盖它（两者应一致，此处只是为了不让进度条卡住）。
    try:
        hint = float(data.get("duration") or 0)
    except (TypeError, ValueError):
        hint = 0.0
    if hint > 0:
        tracker.set_duration(hint)

    def on_download(fraction):
        """把「下载到百分之几」映射成音频秒数，复用同一套 end/duration 语义。

        注意不能直接写 tracker.end = fraction：advance() 会把 end 封顶在 duration 内，
        若此前已有转写进度，下载比例会把 end 顶到 duration，进度条会瞬间冲到 100%。
        """
        d = tracker.duration
        if d > 0:
            tracker.advance(min(fraction * d, d * 0.5))

    tmp = None
    try:
        for u in urls:
            try:
                tmp = download_to_temp(u, on_progress=on_download)
                if tmp:
                    break
            except Exception as e:
                print("download failed:", u[:80], e, flush=True)
                tmp = None
        if not tmp:
            return jsonify({"error": "download failed (all urls)"}), 502

        # 统一解码为 16k float32 数组再转写（duration 精确可知、避免"文件+VAD"路径的线程异常）
        # 注：decode_audio 已返回归一化 float32，勿再除以 32768
        samples = decode_audio(tmp)
        real = float(len(samples)) / 16000.0
        if real > 0:
            tracker.set_duration(real)
            tracker.reset_progress()   # 下载阶段的占位进度清掉，识别进度从 0 开始单调爬
        out, language, abandoned = _transcribe_tracked(samples, tracker)
        if abandoned:
            return jsonify({"error": "cancelled by client", "code": "cancelled"}), 499
        return jsonify({"segments": out, "language": language})
    finally:
        HUB.clear(tracker)
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _transcribe_tracked(samples, tracker):
    """加锁执行转写；若本任务已被新任务取代则直接放弃，不等锁。

    返回 (out, language, abandoned)。
    """
    with TRANSCRIBE_LOCK:
        if HUB.current() is not tracker:
            return [], None, True
        segments, info = model.transcribe(
            samples,
            language="zh",
            vad_filter=True,
            beam_size=1,
            initial_prompt=DEFAULT_PROMPT,
        )
        language = getattr(info, "language", None)
        out, abandoned = run_segments(segments, tracker)
        return out, language, abandoned


@app.route("/transcribe-full", methods=["POST", "OPTIONS"])
def transcribe_full():
    """整片转录：body = 16kHz 单声道 float32 原始 PCM"""
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.data
    if not data or len(data) < 16000 * 2 * 2:  # 少于 ~2 秒音频直接拒绝
        return jsonify({"error": "no audio received"}), 400
    import numpy as np

    samples = np.frombuffer(data, dtype=np.float32).copy()
    tracker = HUB.begin(uuid.uuid4().hex[:8])
    tracker.set_duration(float(len(samples)) / 16000.0)
    try:
        out, language, abandoned = _transcribe_tracked(samples, tracker)
        if abandoned:
            return jsonify({"error": "cancelled by client", "code": "cancelled"}), 499
        return jsonify({"segments": out, "language": language})
    finally:
        HUB.clear(tracker)


def convert_to_wav(src_path, dst_path):
    for ff in FFMPEG_CANDS:
        try:
            result = subprocess.run(
                [ff, "-y", "-i", src_path, "-ar", "16000", "-ac", "1", dst_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=120,
            )
            if result.returncode == 0 and os.path.exists(dst_path):
                return True
        except Exception:
            continue
    return False


@app.route("/transcribe", methods=["POST", "OPTIONS"])
def transcribe():
    """旧接口（兼容）：传 webm/mp4 字节或 multipart file，返回整段文字"""
    if request.method == "OPTIONS":
        return ("", 204)
    if request.files and "file" in request.files:
        audio = request.files["file"].read()
    else:
        audio = request.data
    if not audio:
        return jsonify({"error": "no audio received"}), 400

    prompt = request.args.get("prompt") or DEFAULT_PROMPT
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
        f.write(audio)
        tmp_path = f.name
    try:
        try:
            segments, info = model.transcribe(
                tmp_path, language="zh", vad_filter=True, initial_prompt=prompt
            )
        except Exception:
            wav_path = tmp_path + ".wav"
            if convert_to_wav(tmp_path, wav_path):
                segments, info = model.transcribe(
                    wav_path, language="zh", vad_filter=True, initial_prompt=prompt
                )
                os.unlink(wav_path)
            else:
                raise
        seg_texts = [cc.convert(seg.text.strip()) for seg in segments if seg.text.strip()]
        text = "".join(seg_texts)
    finally:
        os.unlink(tmp_path)

    return jsonify({"text": text, "segments": seg_texts, "language": info.language})


if __name__ == "__main__":
    # threaded=True：识别进行中也要能响应 /progress 轮询
    app.run(host="127.0.0.1", port=8765, threaded=True)
