---
name: harmonyos-hdc
description: >-
  Pure-Python HarmonyOS NEXT / 6.0 HDC (OpenHarmony Device Connector) wireless
  debugging client and Agent UI automation toolkit (v3.2). Enforces Brain-Cerebellum
  (Main-Agent / Sub-Agent) context optimization, single-shot closed-loop UI actions
  (-F diff), universal progressive JPEG screenshots, zero-inertia dead-stop scrolling
  (-k 220ms) resilient against Taobao waterfall/video bugs, and native viewport slices
  for piece-by-piece AI reading.
---

# HarmonyOS HDC Wireless Debugging & Agent UI Automation (`HDC-Claw`)

## 1. Mandatory Brain–Cerebellum Strategy (大脑-小脑分层上下文优化策略)

> [!IMPORTANT]
> **Context Hygiene Rule for Main Agent (主 Agent “大脑” 准则):**
> UI automation generates high-volume transient data (`dump-ui` JSON trees, coordinate diffs, retry loops, and multiple screenshot slices). To prevent polluting the Main Agent's context window:
> 1. **Direct Execution Threshold ($\le 2$ steps)**: Only run `hdc-claw` directly in the Main Agent when performing a simple 1–2 command action (e.g., checking connection, launching a known app, or taking a single snapshot).
> 2. **Delegate to Cerebellum Subagent ($> 2$ steps or Long-Page Reading)**: Whenever a task involves **multi-step UI navigation, form filling, cross-sandbox file delivery (`deliver-file`), troubleshooting popups, or reading long scrollable pages (`long-screen`)**, the Main Agent (**大脑 / Brain**) MUST delegate device operation to a **Subagent (**小脑 / Cerebellum**)** via `invoke_subagent`:
>    - **`TypeName`**: `"self"`
>    - **`Role`**: `"HarmonyOS Device Operator"`
>    - **What the Brain sends**: High-level goal, target device `host:port` (if known), input files/data, and expected return schema.
>    - **What the Cerebellum Subagent does**: Executes `hdc-claw` closed-loop commands (`-F diff`, `batch`, `long-screen`), reads `slice_01.jpg`, `slice_02.jpg`, ... piece-by-piece in its own isolated context, and returns **only a concise summary/structured result + final verification screenshot path** back to the Brain.

### Recommended Subagent Prompt Template (小脑调用模板)
```text
You are the HarmonyOS Cerebellum Operator. Use `hdc-claw` (or `python -m hdc_claw`) to complete the following device task:
- Task: <describe concrete UI goal or long-page extraction task>
- Guidelines:
  1. Always use `-F diff` or `-F summary` on `tap`, `input-text`, and `launch` to execute and verify in a single closed-loop command.
  2. For long scrollable pages (especially complex ones like Taobao/WeChat), run `hdc-claw long-screen --pages 4 --output <manifest.json>` and call `read_file` on each image in `slices` (`*_slice_01.jpg`, `*_slice_02.jpg`, ...) sequentially one by one—never read only the full stitched image if text is small.
  3. Return ONLY the extracted structured information and the path to the final verification screenshot.
```

---

## 2. Core Architecture & Highlights

1. **Zero Binary Dependency**: Pure Python socket implementation of the HDC binary protocol; no `hdc.exe`, no DevEco Studio, no OpenHarmony SDK required.
2. **Single-Packet Binary-Clean Execution (`CMD_UNITY_EXECUTE = 1001` + `TCP_NODELAY`)**: Eliminates PTY allocation latency, terminal echo noise, and `gzip`/`base64` CPU overhead.
3. **Local Keep-Alive Multiplexer Daemon (`127.0.0.1:18710`)**: Spawns detached via WMI on Windows, maintaining authenticated RSA-3072 session across CLI runs (`~15ms` execution).
4. **Kernel-Level `/bin/uinput` Touch & Key Fast-Path**: Injects touch and keys in `~90ms` on-device (5x faster than `uitest uiInput`).
5. **Warm UI Tree Cache**: Resolves coordinates in `<5ms`, falling back to fresh dump on cache miss.
6. **Closed-Loop Auto-Feedback (`-F diff|summary|full|screen`)**: Action + settle wait + post-action UI diff in 1 on-device command pipeline.
7. **Taobao-Resilient Zero-Inertia Dead-Stop Scrolling (`-k 220`) & Native Slices**: Touch drag with 220ms stationary hold before release completely stops inertial fling in ArkUI/Taobao `WaterFlow` lists, generating seam-free native viewport slices.

---

## 3. Quick Start

```powershell
# 1. Connect and warm up daemon
hdc-claw connect --output conn.json
hdc-claw screen --mode keep-awake --output awake.json

# 2. Launch an app and get post-launch summary in ONE command
hdc-claw launch --app "淘宝" -F summary --output launch.json

# 3. Tap by text and get UI diff feedback in ONE command
hdc-claw tap --text "我的淘宝" -F diff --output tap.json

# 4. Take a high-compression snapshot (~55-80KB Progressive JPEG in ~550ms)
hdc-claw screen --mode snapshot --output snap.jpg

# 5. Capture a long scrollable page
hdc-claw long-screen --pages 4 --output long_page.json
# -> Inspect slices (long_page_slice_01.jpg, ...) piece-by-piece
```
