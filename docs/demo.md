# Demo assets checklist

The README links to a demo GIF and screenshots. This page lists exactly what to
record/capture and where to save it, so the links in the README can be filled in
without guessing.

## Target state

| Asset | Path to save | Shown in README at |
|---|---|---|
| Demo GIF (~15–30 s) | `docs/screenshots/demo.gif` | "Demo GIF" line at top |
| Generate flow screenshot | `docs/screenshots/generate.png` | Quick start / How it works |
| Playback sync screenshot | `docs/screenshots/playback.png` | Features |
| Library page screenshot | `docs/screenshots/library.png` | Features |
| Popup screenshot | `docs/screenshots/popup.png` | (optional) |

After the files exist, replace the placeholder lines in `README.md` / `docs/README.zh-CN.md`
with markdown image links, e.g.:

```markdown
![Generate subtitles](docs/screenshots/generate.png)
```

## What to record (demo GIF script, ~20 s)

1. Open a Bilibili video that has **no** official subtitles (search filters: 无字幕).
2. Click the bottom-left **Subtitle** pill → **Generate**.
3. Show progress reaching 100 % (a short clip keeps the GIF small — pick a ≤3 min video; generation is roughly real-time with the local service).
4. Play a few seconds: subtitles appear in sync; drag the progress bar — they catch up.
5. Open the library page and show the generated entry (optional: export `.srt`).

Tips:

- Record at 720p or lower, then convert to GIF (e.g. ffmpeg `-vf fps=12,scale=960:-1`); keep it under ~8 MB.
- The local service must be running (see root README) so the GIF shows the fast path.
- Blur/avoid showing anything in the video you would not want public; Bilibili player chrome is fine.

## Screenshot notes

- `generate.png`: the panel mid-progress with the pill visible.
- `playback.png`: subtitles overlaid while playing (fullscreen is a nice plus).
- `library.png`: the library page listing one or two subtitle entries.
