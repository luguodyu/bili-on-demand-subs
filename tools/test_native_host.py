# -*- coding: utf-8 -*-
"""验证 subtitle-native.exe 的 Native Messaging 协议实现。

Chrome Native Messaging 协议：每次消息 = 4 字节小端长度 + UTF-8 JSON。
本脚本只测协议与帧格式（发 ping / 未知消息），不触发拉起服务，
因此不会加载 460MB 模型，可放心运行。

用法： <任意 python> tools/test_native_host.py
退出码 0 = 通过。
"""
import json
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOST = os.path.join(ROOT, "subtitle-native.exe")

if not os.path.isfile(HOST):
    print("找不到 %s，请先编译：" % HOST)
    print(r'  C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo '
          r'/out:subtitle-native.exe tools\native_host.cs')
    sys.exit(2)

FAILED = []


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILED.append(name)


def send(proc, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    proc.stdin.write(struct.pack("<I", len(body)))
    proc.stdin.write(body)
    proc.stdin.flush()


def recv(proc, timeout=15):
    """读一条消息；返回 (obj, raw_len)"""
    head = proc.stdout.read(4)
    if len(head) < 4:
        return None, None
    (n,) = struct.unpack("<I", head)
    body = proc.stdout.read(n)
    return json.loads(body.decode("utf-8")), n


def main():
    env = dict(os.environ)
    env["DSH_HOST_DEBUG"] = "1"          # 让宿主写 native-host-debug.log
    print("启动宿主:", HOST)
    proc = subprocess.Popen(
        [HOST],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    print("\n[1] ping（不拉起服务）")
    send(proc, {"type": "ping"})
    obj, n = recv(proc)
    check("收到响应", obj is not None, obj)
    check("ok=true", bool(obj and obj.get("ok") is True), obj)
    check("含 running 字段", bool(obj and "running" in obj), obj)
    check("长度前缀与实际一致", isinstance(n, int) and n > 0, n)

    print("\n[2] 未知消息不应崩")
    send(proc, {"type": "totally-unknown-xyz"})
    obj2, _ = recv(proc)
    check("收到响应", obj2 is not None, obj2)
    check("ok=false", bool(obj2 and obj2.get("ok") is False), obj2)
    check("带 error 说明", bool(obj2 and obj2.get("error")), obj2)

    print("\n[3] 关闭 stdin（模拟浏览器退出）应干净结束")
    proc.stdin.close()
    try:
        rc = proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    check("进程已退出", rc is not None, "超时未退出")
    check("退出码 0", rc == 0, rc)

    err = proc.stderr.read().decode("utf-8", errors="replace").strip()
    check("stderr 无异常输出", err == "", err[:300])

    log = os.path.join(ROOT, "native-host-debug.log")
    if os.path.isfile(log):
        print("\n宿主调试日志 %s：" % log)
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                print("   ", line.rstrip())

    print("\n----")
    if FAILED:
        print("FAILED:", FAILED)
        return 1
    print("NATIVE HOST TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
