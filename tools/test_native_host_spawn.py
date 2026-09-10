# -*- coding: utf-8 -*-
"""验证「浏览器自动拉起」整条链路：宿主 start -> 拉起本地服务 -> /health 就绪 -> stop 回收。

与 test_native_host.py 的区别：这会真的启动 subtitle-server.py（加载 460MB 模型，
约需 10~30 秒），因此耗时较长，属于集成验证，按需运行。

用法： <conda python> tools/test_native_host_spawn.py
退出码 0 = 通过。
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST = os.path.join(ROOT, "subtitle-native.exe")
HEALTH = "http://127.0.0.1:8765/health"
READY_TIMEOUT = 90

FAILED = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILED.append(name)


def send(proc, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    proc.stdin.write(struct.pack("<I", len(body)) + body)
    proc.stdin.flush()


def recv(proc):
    head = proc.stdout.read(4)
    if len(head) < 4:
        return None
    (n,) = struct.unpack("<I", head)
    return json.loads(proc.stdout.read(n).decode("utf-8"))


def health():
    try:
        with urllib.request.urlopen(HEALTH, timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def server_pids():
    """找出正在跑 subtitle-server.py 的 python 进程 pid"""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        if "subtitle-server.py" in line:
            parts = [p for p in line.strip().split(",") if p.strip()]
            for p in reversed(parts):
                if p.strip().isdigit():
                    pids.add(int(p.strip()))
                    break
    return pids


def main():
    if not os.path.isfile(HOST):
        print("找不到 " + HOST)
        return 2

    before = server_pids()
    print("启动前已在跑的 subtitle-server 进程:", before or "无")
    if before:
        print("请先停掉这些进程再测（否则无法判断是谁拉起的）")
        return 2

    print("\n[1] 启动宿主，发 {type:start}")
    env = dict(os.environ)
    env["DSH_HOST_DEBUG"] = "1"
    proc = subprocess.Popen([HOST], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    t0 = time.time()
    send(proc, {"type": "start"})
    obj = recv(proc)
    check("宿主回应 ok=true", bool(obj and obj.get("ok") is True), obj)

    print("\n[2] 等待本地服务就绪（模型加载，最长 %ds）" % READY_TIMEOUT)
    hj = None
    while time.time() - t0 < READY_TIMEOUT:
        hj = health()
        if hj:
            break
        time.sleep(1.0)
    elapsed = time.time() - t0
    check("服务已就绪", hj is not None, "超时 %.0fs" % elapsed)
    if hj:
        print("     /health -> %s  (耗时 %.1fs)" % (json.dumps(hj, ensure_ascii=False), elapsed))
        check("接口版本为 3", hj.get("version") == 3, hj.get("version"))

    mid = server_pids()
    print("\n[3] 拉起后的服务进程:", mid or "无")
    check("存在服务子进程", len(mid) > 0, mid)
    check("子进程是新拉起的（不在启动前集合里）", mid and not (mid <= before), mid)

    print("\n[4] 发 {type:stop}，宿主应回收服务")
    send(proc, {"type": "stop"})
    obj2 = recv(proc)
    check("stop 回应 ok=true", bool(obj2 and obj2.get("ok") is True), obj2)
    time.sleep(3)
    after = server_pids()
    print("     stop 之后仍在跑的服务进程:", after or "无")
    check("服务已被回收", len(after) == 0, after)

    print("\n[5] 关闭 stdin，宿主应退出")
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        rc = proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    check("宿主已退出", rc is not None, "超时")
    check("退出码 0", rc == 0, rc)

    print("\n----")
    if FAILED:
        print("FAILED:", FAILED)
        return 1
    print("NATIVE HOST SPAWN TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
