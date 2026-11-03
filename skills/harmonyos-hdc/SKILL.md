---
name: harmonyos-hdc
description: >-
  Pure-Python HarmonyOS NEXT / 6.0 HDC (OpenHarmony Device Connector) wireless
  debugging client and Agent UI automation toolkit (v3.3). Enforces Brain-Cerebellum
  (Main-Agent / Sub-Agent) context optimization, single-shot closed-loop UI actions
  (-F diff/both/auto/crop), annotated screenshots, universal progressive JPEG
  screenshots, zero-inertia dead-stop scrolling (-k 220ms) resilient against Taobao
  waterfall/video bugs, native viewport slices, device temp auto-cleanup, and a live
  daemon console log with per-command duration and gap timing.
---

# HarmonyOS HDC Wireless Debugging & Agent UI Automation (`HDC-Claw` v3.3)

## 1. Mandatory Brain–Cerebellum Strategy (大脑-小脑分层上下文优化策略)

> [!IMPORTANT]
> **Context Hygiene Rule for Main Agent (主 Agent “大脑” 准则):**
> UI automation generates high-volume transient data (`dump-ui` JSON trees, coordinate diffs, retry loops, and multiple screenshot slices). To prevent polluting the Main Agent's context window:
> 1. **Direct Execution Threshold ($\le 2$ steps)**: Only run `hdc-claw` directly in the Main Agent when performing a simple 1–2 command action (e.g., checking connection, launching a known app, or taking a single snapshot).
> 2. **Delegate to Cerebellum Subagent ($> 2$ steps or Long-Page Reading)**: Whenever a task involves **multi-step UI navigation, form filling, cross-sandbox file delivery (`deliver-file`), troubleshooting popups, or reading long scrollable pages (`long-screen`)**, the Main Agent (**大脑 / Brain**) MUST delegate device operation to a **Subagent (**小脑 / Cerebellum**)**:
>    - **What the Brain sends**: High-level goal, target device `host:port` (if known), input files/data, and expected return schema.
>    - **What the Cerebellum Subagent does**: Executes `hdc-claw` closed-loop commands (`-F diff`, `-F both`, `batch`, `long-screen`), reads `slice_01.jpg`, `slice_02.jpg`, ... piece-by-piece in its own isolated context, and returns **only a concise summary/structured result + final verification screenshot path** back to the Brain.

### Recommended Subagent Prompt Template (小脑调用模板)
```text
You are the HarmonyOS Cerebellum Operator. Use `hdc-claw` (or `python -m hdc_claw`) to complete the following device task:
- Task: <describe concrete UI goal or long-page extraction task>
- Guidelines:
  1. Always use `-F diff` or `-F summary` on `tap`, `input-text`, and `launch` to execute and verify in a single closed-loop command.
  2. Use `-F auto` when the surface may be canvas-only (Taobao PDP/评价, JD H5); use `-F both --annotate` when you need the UI diff and a visual proof in one call.
  3. For long scrollable pages (especially complex ones like Taobao/WeChat), run `hdc-claw long-screen --pages 4` and call `read_file` on each image in `slices` (`*_slice_01.jpg`, `*_slice_02.jpg`, ...) sequentially one by one—never read only the full stitched image if text is small.
  4. Return ONLY the extracted structured information and the path to the final verification screenshot.
```

---

## 2. Core v3.3 Architecture & Highlights

1. **Zero Binary Dependency**: Pure Python socket implementation of the HDC binary protocol; no `hdc.exe`, no DevEco Studio, no OpenHarmony SDK required.
2. **Single-Packet Binary-Clean Execution (`CMD_UNITY_EXECUTE = 1001` + `TCP_NODELAY`)**: Eliminates PTY allocation latency, terminal echo noise, and `gzip`/`base64` CPU overhead.
3. **Local Keep-Alive Multiplexer Daemon (`127.0.0.1:18710`)**: Spawns detached via WMI on Windows, maintaining an authenticated RSA-3072 session across CLI runs.
4. **Kernel-Level `/bin/uinput` Touch & Key Fast-Path**: Injects touch and keys in `~90ms` on-device (5x faster than `uitest uiInput`).
5. **Warm UI Tree Cache**: Resolves coordinates in `<5ms`, falling back to a fresh dump on cache miss.
6. **Closed-Loop Feedback (`-F`)**: `diff`/`summary`/`full` (UI), `screen` (image), `both` (UI+image in one pipeline), `auto` (UI unless the tree is canvas-empty, then image), `crop` (matched element region). Add `--annotate` to draw element boxes and IDs on the image.
7. **Taobao-Resilient Zero-Inertia Scrolling (`-k 220`) & Native Slices**: A 220 ms stationary hold before release kills inertial fling in ArkUI/Taobao `WaterFlow` lists, generating seam-free native viewport slices.
8. **Device Temp Hygiene**: `/data/local/tmp/{snap_1.jpeg,hdc_snap.jpeg,hdc_long.jpeg,hdc_ui*.json}` are deleted after capture by default. Use `--keep-device-temp` to retain them or `clean-temp` to sweep leftovers.
9. **Live Daemon Console**: The keep-alive window prints a `TIME | ID | DUR | GAP | CMD` table with `SLOW`/`ERR` markers and optional per-op detail (`daemon --verbose`), mirrored to `~/.gemini/hdc_daemon.log`.
10. **Zero-Config Outputs**: `--output` is optional on every command. JSON → `~/.gemini/hdc_last_output.json`, snapshot → `hdc_last_screen.jpg`, long-screen → `hdc_last_long.json`. Configure once with `hdc-claw config --output-dir D:\hdc_runs`.
11. **Correct Power Modes**: `keep-awake` uses `power-shell wakeup` + `power-shell timeout -o <ms>` (not performance mode 602). `performance` is opt-in; `normal` restores mode 600 and the default timeout.

---

## 3. Quick Start

```powershell
# 1. Connect and warm up the daemon
hdc-claw connect
hdc-claw screen --mode keep-awake --timeout-ms 7200000

# 2. Launch an app and get a post-launch summary in ONE command
hdc-claw launch --app "淘宝" -F summary

# 3. Tap by text and get a UI diff in ONE command
hdc-claw tap --text "我的淘宝" -F diff

# 4. UI diff + annotated screenshot from the same on-device pipeline
hdc-claw tap --text "我的订单" -F both --annotate

# 5. Auto feedback for mixed/canvas pages
hdc-claw tap --text "立即购买" -F auto

# 6. Crop the matched element only
hdc-claw tap --text "筛选" -F crop

# 7. High-compression snapshot (~55-80KB Progressive JPEG)
hdc-claw screen --mode snapshot

# 8. Long page capture with zero-inertia dead-stop scrolling
hdc-claw long-screen --pages 4
# -> Inspect slices (long_screen_slice_01.jpg, ...) piece-by-piece
```

---

## 4. Feedback modes in one line

| Mode | Returns |
|---|---|
| `-F diff` | Structured UI diff (`scene_changed`, added/removed elements, interactive targets) |
| `-F summary` / `-F full` | Post-action element list (50 / all) |
| `-F screen` | Compressed JPEG (`~/.gemini/hdc_last_feedback.jpeg`); add `--annotate` for boxes + IDs |
| `-F both` | UI diff **and** screenshot from one on-device pipeline |
| `-F auto` | UI when the tree has readable text; screenshot on canvas-only pages |
| `-F crop` | Only the matched element's region (`-F crop` requires `tap`/`input-text`) |

`-W <sec>` sets the settle wait (default 0.35 s; 0.8 s for `launch`). `--wait-change` re-dumps once after an extra 0.45 s for animations.

---

## 5. Output defaults

```powershell
hdc-claw config                              # show current output_dir
hdc-claw config --output-dir D:\hdc_runs     # set once; every later command reuses it
hdc-claw screen --mode snapshot              # -> D:\hdc_runs\hdc_last_screen.jpg
hdc-claw tap --text "搜索" -F diff --output tap.json   # explicit path still wins
```

A failed command overwrites the default JSON with `{"status":"error","subcommand":"...","error":"..."}` so you never read a stale success.

---

## 6. Daemon console

The window at `127.0.0.1:18710` prints one row per CLI command:

```text
TIME          ID    DUR     GAP      CMD
18:10:16.678  #001  2.15s   —        connect
18:10:20.114  #002  486ms   598ms    tap --text 搜索 -F diff
18:10:23.902  #003  2.76s   2.76s    dump-ui                     SLOW
```

- `DUR` = client end-to-end time; `GAP` = previous command end → this start (includes agent think time).
- `daemon --verbose` (or `HDC_DAEMON_VERBOSE=1`) adds per-op sub-lines; multi-op commands get a one-line ops summary by default.
- Full log: `~/.gemini/hdc_daemon.log` (UTF-8, rotates at 2 MB, keeps one backup).

---

## 7. Lock screen and safe operation

- **No remote unlock.** `power-shell wakeup` only lights the screen; the PIN page is a secure window. Never blind-tap PIN digits — repeated failures can lock the device. Ask the user for one physical unlock, then keep the screen awake.
- `uitest dumpLayout -i` exposes the bouncer tree and the 3x3+0 keypad geometry, but digit labels are blank and keys are not clickable — diagnostic only. `-i` also includes lock-screen notification text; delete the dump after use.
- `uitest screenCap` / `snapshot_display` on the PIN page are black except the status bar/home indicator (secure-layer mask).
- Prevent locking: `hdc-claw screen --mode keep-awake --timeout-ms 7200000` at session start; enable the developer option "充电时屏幕不休眠"; on a dedicated device, removing the lock-screen PIN is the zero-maintenance option.

---

## 8. Troubleshooting

- `Connection ... failed` → wireless debugging port rotated or the screen slept. Run `hdc-claw --scan connect`, then `hdc-claw screen --mode keep-awake`.
- Stale daemon holding a dead port → `hdc-claw daemon --action stop`, then re-connect (it respawns).
- `No UI element matched ...` → run `hdc-claw dump-ui` and inspect the scene; a system dialog or soft keyboard usually covers the target.
- Chinese in `--steps` JSON: quote the whole array with single quotes in Git Bash; nested single quotes need `'"'"'`.
- `pull`/`push` from Git Bash mangles remote absolute paths (`/data/local/tmp/...` → `C:/Program Files/Git/...`): prefix `MSYS_NO_PATHCONV=1` or run from PowerShell.
