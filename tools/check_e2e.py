# -*- coding: utf-8 -*-
"""真实 HTTP 端到端验证：模拟扩展调用 /transcribe-url，同时并发轮询 /progress。

目的：确认修复后「服务端识别」这条主路径在真实网络栈下仍然成功，
      排除「失败回退」是主路径本身被我改坏导致。
只读诊断，不改动项目文件。
"""
import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
SRV = "http://127.0.0.1:8765"

clip = os.path.join(BASE, "clip.wav")
if not os.path.isfile(clip):
    print("找不到 clip.wav")
    sys.exit(1)
with open(clip, "rb") as f:
    PAYLOAD = f.read()
print(f"clip.wav = {len(PAYLOAD)/1024:.1f} KB")


class Mock(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


mock = HTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=mock.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/clip.wav" % mock.server_address[1]
print("mock 音频地址:", url)

# ---- 并发轮询 /progress，模拟扩展的心跳 ----
polls = {"n": 0, "err": None, "seen": []}
stop = threading.Event()


def poll():
    while not stop.is_set():
        try:
            with urllib.request.urlopen(SRV + "/progress", timeout=2) as r:
                j = json.loads(r.read().decode("utf-8"))
            polls["n"] += 1
            dur, end = j.get("duration") or 0, j.get("end") or 0
            if dur > 0:
                polls["seen"].append(round(end / dur * 100, 1))
        except Exception as e:
            polls["err"] = "%s: %s" % (type(e).__name__, e)
        stop.wait(0.5)


th = threading.Thread(target=poll, daemon=True)
th.start()

print("\nPOST /transcribe-url ...")
t0 = time.time()
body = json.dumps({"urls": [url]}).encode("utf-8")
req = urllib.request.Request(
    SRV + "/transcribe-url",
    data=body,
    headers={"Content-Type": "text/plain;charset=utf-8"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        status = r.status
        raw = r.read().decode("utf-8")
    elapsed = time.time() - t0
    print(f"HTTP {status}   耗时 {elapsed:.1f}s")
    j = json.loads(raw)
    segs = j.get("segments") or []
    print(f"segments = {len(segs)}   language = {j.get('language')}")
    for s in segs[:5]:
        print("   ", s)
    if len(segs) > 5:
        print("    ... 共 %d 段" % len(segs))
    ok = status == 200 and len(segs) > 0
except urllib.error.HTTPError as e:
    print("HTTPError", e.code, e.read().decode("utf-8", errors="replace")[:400])
    ok = False
except Exception as e:
    print("异常:", type(e).__name__, e)
    ok = False

stop.set()
th.join(timeout=3)
print(f"\n轮询次数 = {polls['n']}   轮询异常 = {polls['err']}")
print("进度采样(%):", polls["seen"][:20], "..." if len(polls["seen"]) > 20 else "")
if polls["seen"]:
    print("进度是否越界(>105):", any(p > 105 for p in polls["seen"]))

print("\n=== 结论 ===")
print("主路径成功" if ok else "主路径失败  <<<< 这就是回退的原因")
mock.shutdown()
