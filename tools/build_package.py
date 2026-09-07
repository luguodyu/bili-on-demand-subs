#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""便携打包（Windows x64）——把本地字幕服务打成「无 Python 用户也能跑」的绿色版。

方案：**离线拼装**。从本机 conda 环境复制 Python 核心（python.exe/python310.dll/
vcruntime/DLLs/Lib 标准库）与**白名单依赖**（与开发环境版本一致）到 engine/，
再复制模型与服务文件。全程不联网、可重复、可离线验证。

流程：
  1) 拼装 engine/（conda 核心 + 白名单 site-packages + ssl/sqlite 运行库）
  2) 复制 faster-whisper-small 模型快照 -> models/
  3) 复制 subtitle-server.py / 测试 / 音频夹具 / 启动脚本 / 说明
  4) 用包内引擎自检（tests/test_server.py，含真实转写）——可 --skip-tests 跳过
  5) 打成 zip 到 dist/

用法（仓库根目录执行）：
  <python> tools/build_package.py
  <python> tools/build_package.py --conda-env <路径>     # 指定来源环境
  <python> tools/build_package.py --skip-tests           # 只构建不自检

说明：构建产物只在 build_portable/（可整删），不修改任何开发文件。
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = "B站字幕服务-便携版"
DEFAULT_ENV = r"C:\Users\86150\.conda\envs\bilibili_subtitle"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# site-packages 白名单（顶层目录名；缺失一个 P2 自检就会暴露，届时补上）
SP_TOP_DIRS = {
    "av", "av.libs", "onnxruntime", "ctranslate2", "numpy", "numpy.libs",
    "tokenizers", "faster_whisper", "flask", "opencc", "huggingface_hub",
    "tqdm", "werkzeug", "jinja2", "itsdangerous", "click", "markupsafe",
    "yaml", "certifi", "typing_extensions", "filelock", "fsspec", "colorama",
    "idna", "blinker", "packaging", "pip", "setuptools", "_distutils_hack",
    "google",  # onnxruntime -> protobuf
}
# dist-info 归一化：条目前缀 -> 允许保留（按上面白名单归一化判断）
DISTINFO_KEEP = {
    "av", "blinker", "certifi", "click", "colorama", "ctranslate2",
    "faster_whisper", "filelock", "flask", "fsspec", "huggingface_hub",
    "idna", "itsdangerous", "jinja2", "markupsafe", "numpy", "onnxruntime",
    "opencc_python_reimplemented", "packaging", "pip", "protobuf", "pyyaml",
    "setuptools", "tokenizers", "tqdm", "typing_extensions", "werkzeug",
}
# engine 根目录需要的运行库（conda 环境 Library\\bin -> engine\\）
RUNTIME_DLLS = ["libssl-3-x64.dll", "libcrypto-3-x64.dll", "sqlite3.dll",
                "libbz2.dll", "liblzma.dll", "ffi-7.dll", "ffi-8.dll", "ffi.dll",
                "zlib1.dll", "libexpat-1.dll"]


def log(msg):
    print(msg, flush=True)


def fmt_mb(n):
    return "%.1f MB" % (n / 1048576.0)


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def copy_skip_pycache(src, dst):
    """copytree 但跳过 __pycache__。"""
    if not os.path.exists(dst):
        os.makedirs(dst, exist_ok=True)
    n = 0
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(target_dir, f))
            n += 1
    return n


def build_engine(conda_env, engine_dir):
    """从 conda 环境拼装可搬移的 engine/。"""
    os.makedirs(engine_dir, exist_ok=True)
    log("== P1.1 Python 核心 ==")
    # 1) 根目录核心（排除 *.pdb）
    for f in os.listdir(conda_env):
        p = os.path.join(conda_env, f)
        if not os.path.isfile(p) or f.endswith(".pdb"):
            continue
        if re.match(r"^(python|pythonw|python3|vcruntime|zlib)", f, re.I):
            shutil.copy2(p, os.path.join(engine_dir, f))
    # 2) DLLs（标准库扩展模块）
    copy_skip_pycache(os.path.join(conda_env, "DLLs"), os.path.join(engine_dir, "DLLs"))
    # 3) Lib 标准库（整份，不含 site-packages/__pycache__）
    lib_src = os.path.join(conda_env, "Lib")
    std_dst = os.path.join(engine_dir, "Lib")
    n = 0
    for root, dirs, files in os.walk(lib_src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "site-packages")]
        rel = os.path.relpath(root, lib_src)
        target_dir = std_dst if rel == "." else os.path.join(std_dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(target_dir, f))
            n += 1
    log("  标准库文件数: %d" % n)

    log("== P1.2 依赖（site-packages 白名单） ==")
    sp_src = os.path.join(conda_env, "Lib", "site-packages")
    sp_dst = os.path.join(engine_dir, "Lib", "site-packages")
    os.makedirs(sp_dst, exist_ok=True)
    copied = []
    for entry in sorted(os.listdir(sp_src)):
        src = os.path.join(sp_src, entry)
        low = entry.lower()
        base = low.split("-")[0]
        keep = False
        if low in SP_TOP_DIRS or base in SP_TOP_DIRS:
            keep = True
        elif low.endswith(".dist-info") and base in DISTINFO_KEEP:
            keep = True
        if not keep:
            continue
        dst = os.path.join(sp_dst, entry)
        if os.path.isdir(src):
            copy_skip_pycache(src, dst)
        else:
            shutil.copy2(src, dst)
        copied.append(entry)
    log("  复制包数: %d" % len(copied))

    log("== P1.3 运行库（ssl/sqlite/bz2/lzma…） ==")
    bin_src = os.path.join(conda_env, "Library", "bin")
    got = []
    if os.path.isdir(bin_src):
        for name in RUNTIME_DLLS:
            p = os.path.join(bin_src, name)
            if os.path.isfile(p):
                shutil.copy2(p, os.path.join(engine_dir, name))
                got.append(name)
    log("  复制运行库: %s" % (", ".join(got) if got else "(无)"))


def copy_model(build_root):
    cands = []
    for snap in glob.glob(os.path.join(ROOT, "hf-cache", "hub", "models--Systran--faster-whisper-*", "snapshots", "*")):
        if os.path.isfile(os.path.join(snap, "model.bin")):
            cands.append(snap)
    if not cands:
        raise RuntimeError("hf-cache 里找不到含 model.bin 的快照，请先跑过服务/测试")
    src = sorted(cands)[0]
    dst = os.path.join(build_root, "models", "faster-whisper-small")
    log("复制模型 %s -> %s" % (src, dst))
    copy_skip_pycache(src, dst)
    log("  模型体积: " + fmt_mb(dir_size(dst)))


def write_launchers(build_root):
    with open(os.path.join(build_root, "启动字幕服务.bat"), "w", encoding="utf-8") as f:
        f.write(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "title B站字幕服务（便携版）— 关闭本窗口即停止服务\r\n"
            'cd /d "%~dp0"\r\n'
            "echo 正在启动本地字幕服务，模型加载约需 10~20 秒…\r\n"
            'echo 看到 "Running on http://127.0.0.1:8765" 即就绪。请保持本窗口打开。\r\n'
            "echo.\r\n"
            'engine\\python.exe subtitle-server.py\r\n'
            "pause\r\n"
        )
    with open(os.path.join(build_root, "启动字幕服务-静默.vbs"), "w", encoding="utf-8") as f:
        f.write(
            "' 无窗口启动便携版字幕服务（双击运行；已运行则忽略）\r\n"
            "Set shell = CreateObject(\"WScript.Shell\")\r\n"
            "Set fso = CreateObject(\"Scripting.FileSystemObject\")\r\n"
            "base = fso.GetParentFolderName(WScript.ScriptFullName)\r\n"
            'shell.Run """" & base & "\\engine\\python.exe"" """ & base & "\\subtitle-server.py""", 0, False\r\n'
        )


def write_readme(build_root, size_est):
    with open(os.path.join(build_root, "使用说明.md"), "w", encoding="utf-8") as f:
        f.write(
            "# B站字幕生成插件 · 便携版字幕服务（v0.4.1）\n\n"
            "本文件夹是「本地字幕识别服务」的绿色便携版：内置 Python 引擎与 faster-whisper-small 模型，"
            "**不需要安装 Python**，解压即可用。\n\n"
            "## 使用方法\n\n"
            "1. 把整个文件夹解压到任意位置（建议英文/数字路径，如 `D:\\tools\\bili-sub-server`）；\n"
            "2. **双击 `启动字幕服务.bat`**（想无黑窗口则双击 `启动字幕服务-静默.vbs`）；\n"
            "3. 等待命令行出现 `Running on http://127.0.0.1:8765`（模型加载约 10~20 秒）；\n"
            "4. 在浏览器里正常使用「B站按需字幕」插件，自动连接本服务；\n"
            "5. 不需要时：关闭启动窗口即停止（静默版用任务管理器结束 python 进程）。\n\n"
            "## 技术信息\n\n"
            "- 系统要求：仅支持 **64 位 Windows 10/11**（无需安装 Python/任何运行库）\n"
            "- 内置 Python 3.10 + 依赖（faster-whisper 1.2.1 / ctranslate2 4.8.2 / onnxruntime / av / flask 等，"
            "与开发环境同版本）\n"
            "- 模型：faster-whisper-small（int8，CPU）；接口 /health 返回 version=2\n"
            "- 服务只监听本机 127.0.0.1:8765，不对外网开放\n\n"
            "## 常见问题\n\n"
            "- **端口被占用**：先关闭旧服务窗口（或任务管理器结束 python），再启动。\n"
            "- **杀毒/系统拦截**：首次运行如被提示，选择“仍要运行”；一切文件均在本地，不上传数据。\n"
            "- **插件提示“本地服务版本过旧”**：确认启动的是本文件夹内的服务并看到 version=2。\n"
            "- **移动/删除**：绿色版无安装痕迹，删整个文件夹即卸载。\n\n"
            "构建信息：解压后整体约 %s。\n" % size_est
        )


def make_zip(build_root, dist_dir):
    os.makedirs(dist_dir, exist_ok=True)
    zip_path = os.path.join(dist_dir, "%s-v0.4.1.zip" % APP)
    log("打包 -> %s" % zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(build_root):
            dirs[:] = [d for d in dirs if d != "__pycache__"]  # 不打包缓存目录
            for fn in files:
                if fn.endswith(".pyc"):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, build_root)
                z.write(full, os.path.join(APP, rel))
    return zip_path


def main():
    ap = argparse.ArgumentParser(description="便携打包本地字幕服务（离线拼装）")
    ap.add_argument("--conda-env", default=DEFAULT_ENV, help="来源 conda 环境（默认开发环境）")
    ap.add_argument("--skip-tests", action="store_true", help="跳过 P2 自检")
    ap.add_argument("--rezip", action="store_true",
                    help="只用现有 build_portable/ 重新打包（先剔除 __pycache__/.pyc），不再重建")
    args = ap.parse_args()

    if args.rezip:
        build_root = os.path.join(ROOT, "build_portable")
        if not os.path.isdir(build_root):
            raise RuntimeError("没有 build_portable/，请先完整构建一次")
        log("== 清理 __pycache__/.pyc ==")
        removed = 0
        for root, dirs, files in os.walk(build_root, topdown=False):
            for d in dirs:
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(root, d))
                    removed += 1
            for f in files:
                if f.endswith(".pyc"):
                    os.remove(os.path.join(root, f))
                    removed += 1
        log("  清理条目: %d" % removed)
        log("== 重新打包 zip ==")
        zip_path = make_zip(build_root, os.path.join(ROOT, "dist"))
        log("  zip 文件: %s (%s)" % (zip_path, fmt_mb(os.path.getsize(zip_path))))
        log("DONE")
        return

    conda_env = os.path.abspath(args.conda_env)
    for need in ("python.exe", "python310.dll", "Lib"):
        if not os.path.exists(os.path.join(conda_env, need)):
            raise RuntimeError("来源环境缺 %s：%s" % (need, conda_env))

    build_root = os.path.join(ROOT, "build_portable")
    if os.path.isdir(build_root):
        log("整删旧 build_portable/ 重建")
        shutil.rmtree(build_root)
    os.makedirs(build_root)

    engine_dir = os.path.join(build_root, "engine")
    build_engine(conda_env, engine_dir)
    engine_py = os.path.join(engine_dir, "python.exe")
    sizes = {"engine": dir_size(engine_dir)}
    log("  engine 体积: " + fmt_mb(sizes["engine"]))

    log("== P1.4 模型 ==")
    copy_model(build_root)
    sizes["model"] = dir_size(os.path.join(build_root, "models"))

    log("== P1.5 服务/夹具/启动件 ==")
    shutil.copy2(os.path.join(ROOT, "subtitle-server.py"), os.path.join(build_root, "subtitle-server.py"))
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(build_root, "tests"))
    for wav in glob.glob(os.path.join(ROOT, "clip*.wav")):
        shutil.copy2(wav, os.path.join(build_root, os.path.basename(wav)))
    write_launchers(build_root)
    write_readme(build_root, fmt_mb(sizes["engine"] + sizes["model"]))

    log("== 导入冒烟（engine python） ==")
    r = subprocess.run([engine_py, "-c",
                        "import sys, ssl, sqlite3, flask, faster_whisper, ctranslate2, tokenizers, av, numpy, "
                        "onnxruntime, opencc; print('engine ok', sys.version.split()[0])"])
    if r.returncode != 0:
        raise RuntimeError("engine 导入冒烟失败（多半缺运行库/包，见上方报错）")

    if not args.skip_tests:
        log("== P2 自检（engine 跑 tests/test_server.py，含真实转写，需 1~3 分钟） ==")
        r = subprocess.run([engine_py, os.path.join("tests", "test_server.py")], cwd=build_root)
        if r.returncode != 0:
            raise RuntimeError("P2 自检失败（exit %d）" % r.returncode)
    else:
        log("（已跳过 P2 自检）")

    log("== P3 打包 zip ==")
    zip_path = make_zip(build_root, os.path.join(ROOT, "dist"))
    log("---- 体积汇总 ----")
    log("  engine(含依赖): %s" % fmt_mb(sizes["engine"]))
    log("  模型:           %s" % fmt_mb(sizes["model"]))
    log("  zip 文件:       %s (%s)" % (zip_path, fmt_mb(os.path.getsize(zip_path))))
    log("DONE: 便携包已生成。构建目录 build_portable/ 可整删。")


if __name__ == "__main__":
    main()
