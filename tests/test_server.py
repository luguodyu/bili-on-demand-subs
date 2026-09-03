# -*- coding: utf-8 -*-
"""服务端自动化测试（先于改动，回归用）：
   直接以 Flask test_client 在进程内测路由，不起真实端口；
   /transcribe-url 的下载环节用本地 mock HTTP 服务器验证（不依赖B站）。
   运行:  <conda python> tests/test_server.py
"""
import io
import os
import sys
import json
import wave
import struct
import threading
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# —— 加载服务端模块（模型加载约需 10~20 秒）——
spec = importlib.util.spec_from_file_location("subtitle_server_mod", os.path.join(BASE, "subtitle-server.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
app = mod.app

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS", name)
    else:
        print("  FAIL", name, detail)
        FAILED.append(name)


# ---------- 工具 ----------
def wav_to_pcm16k(path):
    """clip.wav -> 16kHz 单声道 int16 PCM（用 PyAV 做正规重采样）"""
    try:
        import av
        with av.open(path) as cont:
            src = cont.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            frames = cont.decode(src)
            out = b""
            for fr in frames:
                for rf in resampler.resample(fr):
                    out += rf.to_bytes()
            for rf in resampler.resample(None):
                out += rf.to_bytes()
            return out
    except Exception as e:
        print("  (av 重采样不可用，回退 wave 直接读):", e)
        with wave.open(path, "rb") as w:
            assert w.getsampwidth() == 2, "clip.wav 需 16bit"
            raw = w.readframes(w.getnframes())
            rate = w.getframerate()
            ch = w.getnchannels()
            samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
            mono = samples[0::ch]
            # 线性抽稀到 16k（测试够用）
            step = rate / 16000.0
            idx = [int(i * step) for i in range(int(len(mono) / step))]
            out16 = b"".join(struct.pack("<h", mono[i]) for i in idx)
            return out16


def pcm16_to_f32(data16):
    return struct.pack("<%df" % (len(data16) // 2), *[struct.unpack("<h", data16[i:i + 2])[0] / 32768.0 for i in range(0, len(data16), 2)])


class MockFileHandler(BaseHTTPRequestHandler):
    payload = b""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)
    def log_message(self, *a):
        pass


def serve_mock(payload):
    h = HTTPServer(("127.0.0.1", 0), MockFileHandler)
    MockFileHandler.payload = payload
    t = threading.Thread(target=h.serve_forever, daemon=True)
    t.start()
    return h, "http://127.0.0.1:%d/clip.wav" % h.server_address[1]


def valid_segments(resp):
    j = resp.get_json()
    segs = j.get("segments") if isinstance(j, dict) else None
    if not isinstance(segs, list) or not segs:
        return None
    for s in segs:
        if not (isinstance(s["start"], (int, float)) and isinstance(s["end"], (int, float)) and isinstance(s["text"], str)):
            return None
        if s["end"] < s["start"]:
            return None
    return segs


# ---------- 用例 ----------
def test_health():
    print("[1] /health")
    r = app.test_client().get("/health")
    check("200", r.status_code == 200)
    j = r.get_json()
    check("status ok", j and j.get("status") == "ok")
    check("version 2", j and j.get("version") == 2)
    check("CORS *", r.headers.get("Access-Control-Allow-Origin") == "*")


def test_transcribe_full():
    print("[2] /transcribe-full (clip.wav -> PCM -> 识别)")
    data16 = wav_to_pcm16k(os.path.join(BASE, "clip.wav"))
    f32 = pcm16_to_f32(data16)
    r = app.test_client().post("/transcribe-full", data=f32, content_type="application/octet-stream")
    check("200", r.status_code == 200, "got %s" % r.status_code)
    segs = valid_segments(r)
    check("segments 非空且时间轴合法", bool(segs))
    if segs:
        check("时间轴单调", all(segs[i]["end"] <= segs[i + 1]["start"] + 0.5 for i in range(len(segs) - 1)))
        check("文本非空", all(s["text"].strip() for s in segs))


def test_transcribe_url_mock():
    print("[3] /transcribe-url (mock 服务器下载 clip.wav -> 识别)")
    with open(os.path.join(BASE, "clip.wav"), "rb") as f:
        payload = f.read()
    h, url = serve_mock(payload)
    try:
        r = app.test_client().post("/transcribe-url", json={"urls": [url]})
        check("200", r.status_code == 200, "got %s %s" % (r.status_code, r.get_data()[:200]))
        segs = valid_segments(r)
        check("segments 非空且时间轴合法", bool(segs))
    finally:
        h.shutdown()


def test_errors():
    print("[4] 错误路径")
    c = app.test_client()
    r = c.post("/transcribe-full", data=b"", content_type="application/octet-stream")
    check("空 body -> 400", r.status_code == 400)
    r = c.post("/transcribe-url", json={"urls": []})
    check("空 urls -> 400", r.status_code == 400)
    r = c.post("/transcribe-url", json={"urls": ["http://127.0.0.1:1/nope.m4s"]})
    check("坏地址 -> 502", r.status_code == 502, "got %s" % r.status_code)
    check("502 带 error 信息", r.get_json() and "error" in r.get_json())


if __name__ == "__main__":
    test_health()
    test_transcribe_full()
    test_transcribe_url_mock()
    test_errors()
    print("----")
    if FAILED:
        print("FAILED:", FAILED)
        sys.exit(1)
    print("ALL TESTS PASSED")
