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
import time
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
S = mod                      # 服务端模块对象（subtitle-server.py）
app = mod.app
CLIENT_STALE_SEC_ORIG = mod.CLIENT_STALE_SEC
MODEL_OK = getattr(mod, "model", None) is not None

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


class ThrottledFileHandler(BaseHTTPRequestHandler):
    """限速下载：让「下载阶段」持续数秒，使进度推进可被观测。

    不限速时 640KB 几毫秒就下完，reset_progress 会在轮询线程看到任何东西之前清零，
    进度测试会得到一串 0（假象），无法验证进度真的会动。
    """
    payload = b""
    chunk = 64 * 1024
    delay = 0.15

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        for i in range(0, len(self.payload), self.chunk):
            try:
                self.wfile.write(self.payload[i:i + self.chunk])
                self.wfile.flush()
            except Exception:
                return
            time.sleep(self.delay)

    def log_message(self, *a):
        pass


def serve_mock(payload, handler=MockFileHandler):
    h = HTTPServer(("127.0.0.1", 0), handler)
    handler.payload = payload
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


# ---------- 新增：取消 / 进度隔离 ----------
# 背景：客户端 abort 后服务端仍把整片转写完（孤儿线程空烧 CPU、占着转写锁、
# 并继续写全局进度），导致下一次任务的进度条停滞或算出 525% 之类的越界值。
# 以下用例覆盖修复点，不依赖网络。


def test_tracker_progress_rules():
    """进度只增不减、封顶在 duration 内"""
    print("[5] TaskTracker 进度规则")
    t = S.TaskTracker("t1")
    check("初始 end=0", t.snapshot()["end"] == 0.0)
    t.set_duration(100.0)
    t.advance(40)
    check("advance(40) -> 40", t.snapshot()["end"] == 40.0)
    t.advance(20)
    check("回退请求被忽略 -> 仍 40", t.snapshot()["end"] == 40.0)
    t.advance(9999)
    check("越过 duration 被封顶 -> 100", t.snapshot()["end"] == 100.0)
    check("end 永不超过 duration", t.snapshot()["end"] <= t.snapshot()["duration"])


def test_tracker_cancel_and_staleness():
    """显式取消与心跳停止都要判定为应放弃"""
    print("[6] TaskTracker 取消 / 心跳判定")
    t = S.TaskTracker("t1")
    check("新任务不应放弃", t.should_abandon() is False)
    t.mark_polled()
    check("刚轮询过不应放弃", t.should_abandon() is False)
    t.cancel()
    check("显式取消 -> 放弃", t.should_abandon() is True)

    t2 = S.TaskTracker("t2")
    t2.mark_polled()
    t2._last_poll = time.time() - (S.CLIENT_STALE_SEC + 1)
    check("心跳超时 -> 放弃", t2.should_abandon() is True)

    t3 = S.TaskTracker("t3")     # 从未被轮询（curl 直接调用）不应被心跳规则误杀
    t3._last_poll = time.time() - (S.CLIENT_STALE_SEC + 1)
    check("从未轮询则不按心跳判放弃", t3.should_abandon() is False)


def test_hub_isolation_and_supersede():
    """每个任务独立 tracker；新任务开始即作废旧任务"""
    print("[7] ProgressHub 任务隔离")
    hub = S.ProgressHub()
    a = hub.begin("A")
    check("current 是 A", hub.current() is a)
    a.set_duration(100)
    a.advance(50)
    b = hub.begin("B")
    check("新局面 -> current 是 B", hub.current() is b)
    check("旧任务 A 被取消", a.cancelled() is True)
    check("新任务 B 未被取消", b.cancelled() is False)
    check("/progress 快照来自 B（不被 A 污染）", hub.snapshot()["end"] == 0.0)
    check("快照带 taskId", hub.snapshot()["taskId"] == "B")
    hub.clear(b)
    check("clear 后快照为空任务", hub.snapshot()["taskId"] is None)


def test_progress_route_is_heartbeat():
    """/progress 既是进度也是心跳登记"""
    print("[8] /progress 路由 = 心跳")
    t = S.HUB.begin("route-t")
    try:
        r = app.test_client().get("/progress")
        check("200", r.status_code == 200)
        j = r.get_json()
        check("回传 taskId", j and j.get("taskId") == "route-t")
        check("心跳已登记", t._ever_polled is True)
    finally:
        S.HUB.clear(t)


def test_cancel_route():
    print("[9] /cancel 路由")
    t = S.HUB.begin("cancel-t")
    try:
        r = app.test_client().post("/cancel", json={"taskId": "cancel-t"})
        check("200", r.status_code == 200)
        check("cancelled 返回 taskId", r.get_json() and r.get_json().get("cancelled") == "cancel-t")
        check("tracker 进入取消态", t.cancelled() is True)
    finally:
        S.HUB.clear(t)
    r = app.test_client().post("/cancel", json={})
    check("无活动任务时也返回 200", r.status_code == 200)


def test_cancel_stops_transcription():
    """核心复现：客户端放弃后，转写必须在分段边界停下（旧实现会跑完整片）

    用假的分段生成器 + 已取消的 tracker 走 run_segments，不加载模型、不耗时间。
    """
    print("[10] 取消后转写立即停止（本 bug 的核心复现）")
    consumed = {"n": 0}

    class Seg:
        def __init__(self, s, e, text):
            self.start, self.end, self.text = s, e, text

    def fake_segments(total=50):
        for i in range(total):
            consumed["n"] += 1
            yield Seg(i, i + 1, "第%d句" % i)

    t = S.TaskTracker("cancel-stop")
    t.set_duration(50.0)
    t.mark_polled()
    t._last_poll = time.time() - (S.CLIENT_STALE_SEC + 1)   # 模拟客户端已消失

    out, abandoned = S.run_segments(fake_segments(50), t)
    check("判定为放弃", abandoned is True)
    check("生成器未被消费完", consumed["n"] < 50, "实际消费 %d 段" % consumed["n"])
    check("至多消费 1 段即返回", consumed["n"] <= 1, "实际消费 %d 段" % consumed["n"])

    t2 = S.TaskTracker("normal")
    t2.set_duration(50.0)
    out2, abandoned2 = S.run_segments(fake_segments(5), t2)
    check("正常路径不放弃", abandoned2 is False)
    check("正常路径产出 5 段", len(out2) == 5)
    check("正常路径进度推进到 duration", t2.snapshot()["end"] == 50.0)


def test_transcribe_url_mock_after_fix():
    """回归：取消机制接入路由后，正常路径仍须可用"""
    if not MODEL_OK:
        print("[11] /transcribe-url 回归 —— SKIP（未加载模型）")
        return
    print("[11] /transcribe-url 回归（修复后正常路径）")
    with open(os.path.join(BASE, "clip.wav"), "rb") as f:
        payload = f.read()
    h, url = serve_mock(payload)
    try:
        poll_stop = threading.Event()

        def poll():
            c = app.test_client()
            while not poll_stop.is_set():
                try:
                    c.get("/progress")
                except Exception:
                    pass
                poll_stop.wait(0.5)

        th = threading.Thread(target=poll, daemon=True)
        th.start()
        try:
            r = app.test_client().post("/transcribe-url", json={"urls": [url]})
        finally:
            poll_stop.set()
            th.join(timeout=3)
        check("200", r.status_code == 200, "got %s %s" % (r.status_code, r.get_data()[:200]))
        segs = valid_segments(r)
        check("segments 非空且时间轴合法", bool(segs))
    finally:
        h.shutdown()


def test_download_phase_reports_progress():
    """下载阶段也要报进度（长视频下载期间进度条不能钉死在 15%）"""
    print("[12] 下载阶段进度可报")
    t = S.TaskTracker("dl")
    t.set_duration(600.0)

    def on_download(f):
        d = t.duration
        if d > 0:
            t.advance(min(f * d, d * 0.5))

    on_download(0.25)
    check("下载 25% -> end=150（600*25%）", t.snapshot()["end"] == 150.0, "实际 %s" % t.snapshot()["end"])
    on_download(1.0)
    check("下载 100% -> 封顶在 duration 一半", t.snapshot()["end"] == 300.0, "实际 %s" % t.snapshot()["end"])

    t.reset_progress()
    check("reset_progress 后归零", t.snapshot()["end"] == 0.0)
    check("reset 后 duration 保留", t.snapshot()["duration"] == 600.0)


def test_progress_moves_during_download():
    """端到端回归：下载阶段进度必须真的往上走

    用限速 mock 让下载持续数秒，从而能观测到中间进度。
    这正是用户反馈「进度停滞」的那个阶段——单独断言它。
    """
    if not MODEL_OK:
        print("[13] 下载阶段进度推进 —— SKIP（未加载模型）")
        return
    print("[13] 下载阶段进度推进（限速 mock）")
    with open(os.path.join(BASE, "clip.wav"), "rb") as f:
        payload = f.read()
    h, url = serve_mock(payload, ThrottledFileHandler)
    mid = []
    try:
        stop = threading.Event()

        def watch():
            c = app.test_client()
            while not stop.is_set():
                try:
                    j = c.get("/progress").get_json()
                    if j and (j.get("duration") or 0) > 0 and (j.get("end") or 0) > 0:
                        mid.append(j["end"] / j["duration"] * 100.0)
                except Exception:
                    pass
                stop.wait(0.1)

        th = threading.Thread(target=watch, daemon=True)
        th.start()
        try:
            r = app.test_client().post("/transcribe-url", json={"urls": [url], "duration": 20})
        finally:
            stop.set()
            th.join(timeout=3)
        check("200", r.status_code == 200, "got %s" % r.status_code)
        check("下载阶段观测到非零进度", len(mid) > 0, "样本数 %d" % len(mid))
        check("进度不超过 100.5%", all(p <= 100.5 for p in mid),
              "最大 %s" % (max(mid) if mid else None))
        check("进度单调不回退", all(mid[i] <= mid[i + 1] + 0.01 for i in range(len(mid) - 1)),
              "序列 %s" % [round(p, 1) for p in mid][:20])
        if mid:
            check("下载阶段进度达到有意义水平(>=10%)", max(mid) >= 10.0, "最大 %.1f%%" % max(mid))
    finally:
        h.shutdown()
    print("     下载阶段进度(%):", [round(p, 1) for p in mid][:24])


if __name__ == "__main__":
    test_health()
    test_transcribe_full()
    test_transcribe_url_mock()
    test_errors()
    test_tracker_progress_rules()
    test_tracker_cancel_and_staleness()
    test_hub_isolation_and_supersede()
    test_progress_route_is_heartbeat()
    test_cancel_route()
    test_cancel_stops_transcription()
    test_transcribe_url_mock_after_fix()
    test_download_phase_reports_progress()
    test_progress_moves_during_download()
    print("----")
    if FAILED:
        print("FAILED:", FAILED)
        sys.exit(1)
    print("ALL TESTS PASSED")
