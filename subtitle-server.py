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
import tempfile
import subprocess
import urllib.request
import urllib.error
import urllib.parse

# 模型下载走国内镜像（必须在 import faster_whisper 之前设置）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, request, jsonify
from faster_whisper import WhisperModel
from opencc import OpenCC

app = Flask(__name__)
app.json.ensure_ascii = False  # 中文直接显示，不转义

# 实时进度（供浏览器轮询；单线程串行处理，无并发问题）
PROGRESS = {"end": 0.0, "duration": 0.0, "ts": 0.0}

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
    return jsonify({"status": "ok", "model": "small", "version": 2})


@app.route("/progress")
def progress():
    return jsonify(PROGRESS)


def download_to_temp(url, suffix=".m4s"):
    """带 B 站 referer 下载音频直链到临时文件（浏览器 fetch 无法设置 Referer，这里可以）"""
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
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
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

    tmp = None
    try:
        for u in urls:
            try:
                tmp = download_to_temp(u)
                if tmp:
                    break
            except Exception as e:
                print("download failed:", u[:80], e, flush=True)
                tmp = None
        if not tmp:
            return jsonify({"error": "download failed (all urls)"}), 502

        PROGRESS.update({"end": 0.0, "duration": 0.0, "ts": time.time()})
        segments, info = model.transcribe(
            tmp,
            language="zh",
            vad_filter=True,
            word_timestamps=True,
            beam_size=1,
            initial_prompt=DEFAULT_PROMPT,
        )
        duration = float(getattr(info, "duration", 0) or 0)
        PROGRESS["duration"] = duration
        out = []
        for seg in segments:
            text = cc.convert((seg.text or "").strip())
            if text:
                out.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": text})
            if duration > 0:
                PROGRESS.update({"end": float(getattr(seg, "end", 0) or 0), "ts": time.time()})
        PROGRESS["end"] = duration
        return jsonify({"segments": out, "language": getattr(info, "language", None)})
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


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

    segments, info = model.transcribe(
        samples,
        language="zh",
        vad_filter=True,
        word_timestamps=True,
        beam_size=1,
        initial_prompt=DEFAULT_PROMPT,
    )
    # 实时进度：duration 取实际（缺失则按采样数估算），end 随分段流式更新
    duration = float(getattr(info, "duration", 0) or (len(samples) / 16000.0))
    PROGRESS.update({"end": 0.0, "duration": duration, "ts": time.time()})
    out = []
    for seg in segments:
        text = cc.convert((seg.text or "").strip())
        if text:
            out.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": text})
        PROGRESS.update({"end": float(getattr(seg, "end", 0) or 0), "ts": time.time()})
    PROGRESS["end"] = duration
    return jsonify({"segments": out, "language": info.language})


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
