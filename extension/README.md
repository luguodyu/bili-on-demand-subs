# Bili On-Demand Subtitles — extension (v0.5.1)

MV3 extension that generates on-demand subtitles for caption-less Bilibili videos and keeps them in sync with playback. Subtitles are stored locally in `chrome.storage.local` and can be listed / deleted / exported as `.srt` from the built-in library page.

See the [root README](../README.md) for the full picture (architecture, service setup, testing). This file covers the `extension/` folder itself.

## Restore vendored runtime first

`transformers.js` and the `ort/` WASM binaries (~23 MB) are intentionally not in git. After cloning:

```bash
python tools/fetch_vendor.py
```

## File map

| File | Role |
|---|---|
| `manifest.json` | MV3 manifest (v0.5.1) |
| `shared-subs.js` | Pure functions (SRT parse/generate, timestamp lookup) — shared by content script, background, library page and node tests |
| `sub-ui.js` | Video-page pill (⚪ none / 🟢 ready / ⏳ generating) + panel + playback overlay |
| `offscreen.js` | Offscreen page: job orchestration (local service first, WASM fallback), progress reporting |
| `background.js` | Job scheduling: offscreen keep-alive, progress relay, exports, library entry |
| `library.html/js` | Subtitle library manager page |
| `popup.html/js` | Popup → library entry |
| `transformers.js` + `ort/` | WASM recognition fallback path (restored by `tools/fetch_vendor.py`) |

The local service lives at the repo root (`subtitle-server.py`); Windows dev launchers are kept out of git (local convenience, they hard-code the dev conda env).

## Load & use

1. `edge://extensions` (or `chrome://extensions`) → Developer mode → **Load unpacked** → select this `extension/` folder.
2. (Recommended) Start the local service (`python subtitle-server.py` from the repo root, or the portable build's launcher) and wait ~10–20 s for the model.
3. Open a caption-less Bilibili video → bottom-left pill → **Generate** → wait for progress.
4. Play: subtitles sync, survive seeking, and render in fullscreen.

## Development

Shared pure functions are tested with plain Node — no browser needed:

```bash
node ../tests/test_shared.js
```

If you change server-side behavior, run the server suite too (`python tests/test_server.py` from the repo root, ~1–3 min with real transcription).

## Known notes

- If the panel reports *"local service version too old"*, restart the service (a stale `.bat` process may be running).
- Errors: `edge://extensions` → extension details → Inspect views → offscreen page console; server-side logs appear in the service's console.
- The old "realtime caption" (streaming WASM) architecture was removed from the codebase; it lives on in git history only.
