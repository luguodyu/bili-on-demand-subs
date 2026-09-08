#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch the vendored browser-ASR runtime files that are intentionally NOT in git.

The MV3 extension ships a browser-side fallback transcriber built on
transformers.js + onnxruntime-web (WASM, ~23 MB). Keeping those binaries in
git would bloat the repository, so a fresh clone restores them with this
script (local copies are never touched unless you re-run it).

Sources (both immutable npm releases):
  * extension/transformers.js
        <- @huggingface/transformers@<TRANSFORMERS_VERSION>  (member dist/transformers.js)
  * extension/ort/ort-wasm-simd-threaded.jsep.{mjs,wasm}
        <- onnxruntime-web@<version pinned by the package above>   (member dist/...)

The onnxruntime-web version is read from the transformers.js package
metadata on every run, so the wasm glue always matches the JS bundle.

Integrity: each tarball is verified against the SHA-512 that the registry
itself publishes (dist.integrity) before anything is extracted. Nothing is
written unless every expected member verifies.

Usage (from the repository root):
  python tools/fetch_vendor.py            # download + verify into extension/
  python tools/fetch_vendor.py --check    # only report local state
  python tools/fetch_vendor.py --version 3.1.0   # pin a different release

Bump DEFAULT_VERSION only after validating the new pair end-to-end in a
browser (generate subtitles with the local service stopped so the WASM
fallback path runs).
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")

# Pin the release that was validated end-to-end (see module docstring).
DEFAULT_VERSION = "4.2.0"

# npm mirrors, tried in order (npmmirror is CN-friendly).
REGISTRIES = [
    "https://registry.npmmirror.com",
    "https://registry.npmjs.org",
]

# (npm package, member inside the tarball, destination relative to extension/)
MAPPING = [
    ("@huggingface/transformers", "package/dist/transformers.js", "transformers.js"),
    ("onnxruntime-web", "package/dist/ort-wasm-simd-threaded.jsep.mjs",
     os.path.join("ort", "ort-wasm-simd-threaded.jsep.mjs")),
    ("onnxruntime-web", "package/dist/ort-wasm-simd-threaded.jsep.wasm",
     os.path.join("ort", "ort-wasm-simd-threaded.jsep.wasm")),
]


def log(msg):
    print(msg, flush=True)


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "bili-on-demand-subs/fetch-vendor"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_meta(pkg, version):
    """Fetch the registry metadata for one exact version, trying each mirror."""
    last = None
    for reg in REGISTRIES:
        url = "%s/%s/%s" % (reg, pkg, version)
        try:
            return http_json(url)
        except urllib.error.HTTPError as e:
            last = e
            log("  mirror %s -> HTTP %s" % (reg, e.code))
        except Exception as e:  # noqa: BLE001 - report and try next mirror
            last = e
            log("  mirror %s -> %s" % (reg, e))
    raise RuntimeError("all mirrors failed for %s@%s: %s" % (pkg, version, last))


def fetch_tarball(url, integrity_b64, tmp_dir):
    """Download a tarball and verify it against the registry SHA-512."""
    log("  downloading %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": "bili-on-demand-subs/fetch-vendor"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    digest = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if digest != integrity_b64:
        raise RuntimeError("integrity mismatch for %s\n  expected %s\n  got      %s"
                           % (url, integrity_b64, digest))
    path = os.path.join(tmp_dir, "pkg.tgz")
    with open(path, "wb") as f:
        f.write(data)
    log("  verified sha512 (%d bytes)" % len(data))
    return path


def extract_member(tgz_path, member, dest_dir):
    with tarfile.open(tgz_path, "r:gz") as tf:
        try:
            m = tf.getmember(member)
        except KeyError:
            raise RuntimeError("member not found in tarball: %s" % member)
        data = tf.extractfile(m).read()
    os.makedirs(os.path.dirname(dest_dir) or ".", exist_ok=True)
    with open(dest_dir, "wb") as f:
        f.write(data)
    return len(data)


def cmd_fetch(version):
    tmp = tempfile.mkdtemp(prefix="fetch_vendor_")
    try:
        for pkg, member, rel in MAPPING:
            dest = os.path.join(EXT, rel)
            log("== %s@%s -> %s" % (pkg, version, dest))
            if pkg == "onnxruntime-web":
                # Version must match the one transformers.js was built against.
                meta_t = get_meta("@huggingface/transformers", version)
                dep = meta_t["dependencies"].get("onnxruntime-web")
                if not dep:
                    raise RuntimeError("cannot derive onnxruntime-web version from transformers@%s" % version)
                pkg_version = dep.lstrip("^~")
                log("  (onnxruntime-web version derived: %s)" % pkg_version)
            else:
                pkg_version = version
                meta_t = None
            meta = get_meta(pkg, pkg_version)
            url = meta["dist"]["tarball"]
            integrity = meta["dist"]["integrity"]
            tgz = fetch_tarball(url, integrity, tmp)
            size = extract_member(tgz, member, dest)
            log("  wrote %s (%d bytes)" % (dest, size))
        log("DONE: vendor files are in place under extension/. Reload the extension.")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_check():
    missing = 0
    for pkg, member, rel in MAPPING:
        dest = os.path.join(EXT, rel)
        if os.path.isfile(dest):
            log("  ok  %8d bytes  %s" % (os.path.getsize(dest), rel))
        else:
            log("  MISSING         %s  (run: python tools/fetch_vendor.py)" % rel)
            missing += 1
    if missing:
        sys.exit(1)
    log("CHECK PASSED: all vendor files present.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report whether vendor files are present")
    ap.add_argument("--version", default=DEFAULT_VERSION, help="transformers.js release to fetch (default %s)" % DEFAULT_VERSION)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if args.check:
        cmd_check()
    else:
        cmd_fetch(args.version)


if __name__ == "__main__":
    main()
