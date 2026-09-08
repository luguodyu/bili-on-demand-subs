# Third-party notices

This project vendors or references the following third-party components. Their
binaries are **not committed** to git; a fresh clone restores them via
[`tools/fetch_vendor.py`](../tools/fetch_vendor.py), which downloads and
verifies the exact npm releases listed below.

## @huggingface/transformers (transformers.js)

- Used for: in-browser (WASM) speech-recognition fallback — file `extension/transformers.js`
- License: **Apache-2.0** ([LICENSE](https://github.com/huggingface/transformers.js/blob/main/LICENSE))
- Source: https://github.com/huggingface/transformers.js · https://www.npmjs.com/package/@huggingface/transformers

## onnxruntime-web

- Used for: ONNX Runtime WASM backend — files `extension/ort/ort-wasm-simd-threaded.jsep.{mjs,wasm}`
- License: **MIT** ([LICENSE](https://github.com/microsoft/onnxruntime/blob/main/LICENSE))
- Source: https://github.com/microsoft/onnxruntime · https://www.npmjs.com/package/onnxruntime-web

The exact fetched versions are the ones pinned in `tools/fetch_vendor.py`
(the onnxruntime-web version is derived from the transformers.js package
dependencies so the two always match).

## Python-side components (local service / portable build)

Runtime dependencies of `subtitle-server.py` and of the portable package built
by `tools/build_package.py`: faster-whisper (MIT), ctranslate2 (MIT),
Flask (BSD-3-Clause), PyAV (BSD-3-Clause), OpenCC (Apache-2.0), and the
`Systran/faster-whisper-small` model (MIT). See each package's own license.
