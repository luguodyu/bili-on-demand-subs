"""
字幕服务进度诊断 —— 抓出「进度跳 525%」和「进度停滞」的真凶

放在项目根目录运行（和 subtitle-server.py 同级）：
    python diagnose_progress.py

它做三件事：
  1. 自动找 Python 解释器（优先便携包的 engine\\python.exe，否则当前解释器）
  2. 启动 subtitle-server.py，服务端日志直接打在这里
  3. 后台每 0.5 秒轮询 /progress，打印服务端「原始 JSON」并实时算 end/duration

判读：
  end/duration > 105%       -> 越界来源就是服务端（但我已核对源码不该发生）
  duration == 0             -> 分母为零，前端会停滞或显示残留值
  end 出现回退 / duration 跳变 -> 进度跳变来源
  全程正常但 UI 仍显示 525%   -> 说明浏览器里跑的扩展不是这个仓库的代码

本脚本只读不写，不改动任何文件。
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
POLL_INTERVAL = 0.5
SERVER = "subtitle-server.py"

_STOP = threading.Event()


def find_python(here):
    """优先用便携包自带的 python.exe，保证和服务实际运行环境一致。

    便携布局：<包根>/engine/python.exe（与 subtitle-server.py 同级）。
    也接受把本脚本放进便携包目录里运行。
    """
    candidates = [
        os.path.join(here, "engine", "python.exe"),
        os.path.join(here, "build_portable", "engine", "python.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c, "便携包"
    return sys.executable, "当前解释器"


def get_progress():
    req = urllib.request.Request(BASE + "/progress", headers={"User-Agent": "diag"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.read().decode("utf-8", errors="replace")


def poll_progress():
    prev_end = None
    prev_dur = None
    tick = 0
    while not _STOP.is_set():
        tick += 1
        try:
            raw = get_progress()
            j = json.loads(raw)
        except Exception as e:
            print(f"  [{tick:5d}] /progress 读取失败: {type(e).__name__}: {e}")
            _STOP.wait(POLL_INTERVAL)
            continue

        end, dur, ts = j.get("end"), j.get("duration"), j.get("ts")
        notes = []

        if isinstance(dur, (int, float)) and dur > 0 and isinstance(end, (int, float)):
            pct = end / dur * 100
            notes.append(f"end/duration={pct:.1f}%")
            if pct > 105:
                notes.append("<<<< 越界！这里就是 525% 的来源")
        elif dur == 0:
            notes.append("duration==0 <<<< 分母为零，前端会停滞或显示残留值")

        if isinstance(dur, (int, float)) and prev_dur is not None and dur != prev_dur and dur != 0:
            notes.append(f"<<<< duration 跳变 {prev_dur} -> {dur}")
        if isinstance(end, (int, float)) and prev_end is not None and end < prev_end:
            notes.append(f"<<<< end 回退 {prev_end} -> {end}")

        if isinstance(end, (int, float)):
            prev_end = end
        if isinstance(dur, (int, float)):
            prev_dur = dur

        print(f"  [{tick:5d}] raw={raw}" + ("   " + "  ".join(notes) if notes else ""))
        _STOP.wait(POLL_INTERVAL)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    srv = os.path.join(here, SERVER)
    if not os.path.isfile(srv):
        print(f"找不到 {SERVER}，请把本脚本放到与它同级目录")
        return 1

    py, which = find_python(here)
    print("=" * 78)
    print(f"服务脚本: {srv}")
    print(f"解释器  : {py}   ({which})")
    print("=" * 78)

    proc = subprocess.Popen(
        [py, "-u", srv],
        cwd=here,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def relay():
        for line in proc.stdout:
            print("  [server] " + line.rstrip())

    threading.Thread(target=relay, daemon=True).start()

    for _ in range(180):
        if proc.poll() is not None:
            print("\n服务进程已退出，看上面 [server] 的输出")
            return 1
        try:
            req = urllib.request.Request(BASE + "/health", headers={"User-Agent": "diag"})
            with urllib.request.urlopen(req, timeout=1) as r:
                print("\n服务就绪: " + r.read().decode("utf-8", errors="replace"))
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("\n服务 90 秒内没起来")
        proc.kill()
        return 1

    print("\n" + "=" * 78)
    print("现在去浏览器点「生成字幕」。这里每 0.5 秒打印服务端原始进度。")
    print("全部结束后按 Ctrl+C 退出，把整个窗口的输出发出来。")
    print("=" * 78 + "\n")

    threading.Thread(target=poll_progress, daemon=True).start()

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，收尾中…")
    finally:
        _STOP.set()
        if proc.poll() is None:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
