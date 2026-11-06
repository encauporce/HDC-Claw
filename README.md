# HDC-Claw 🐾

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-green.svg)](https://www.python.org/downloads/)
[![HarmonyOS](https://img.shields.io/badge/HarmonyOS-NEXT%20%2F%206.0-red.svg)](https://developer.huawei.com/consumer/cn/harmonyos/)
[![OpenHarmony](https://img.shields.io/badge/Protocol-OpenHarmony%20HDC-orange.svg)](https://gitee.com/openharmony)
[![Release: v3.3.0](https://img.shields.io/badge/Release-v3.3.0-brightgreen)](https://github.com/encauporce/HDC-Claw/releases/tag/v3.3.0)

**Pure-Python HarmonyOS NEXT / 6.0 HDC Wireless Protocol Client & AI Agent Automation Toolkit.**

📦 **Download / changelog: [Releases](https://github.com/encauporce/HDC-Claw/releases)** — latest is [`v3.3.0`](https://github.com/encauporce/HDC-Claw/releases/tag/v3.3.0), matching `hdc-claw --version`.

Zero external binaries. Zero DevEco Studio / SDK requirements. Native RSA-3072 handshake, persistent keep-alive daemon (<15ms latency), kernel-level `/bin/uinput` touch/key fast-path, closed-loop UI diff feedback, and Taobao-resilient zero-inertia visual long-screenshots.

### 🆕 What's new in v3.3

- **Richer closed-loop feedback**: `-F both` (UI diff + screenshot in one on-device pipeline), `-F auto` (UI when the tree is readable, screenshot on canvas-only pages), `-F crop` (matched element crop), and `--annotate` (element boxes + IDs drawn on the image).
- **Device temp hygiene**: temp images/UI dumps under `/data/local/tmp` are deleted after capture; `--keep-device-temp` retains them and `clean-temp` sweeps leftovers.
- **Live daemon console**: a `TIME | ID | DUR | GAP | CMD` timing table with `SLOW`/`ERR` markers and optional per-op detail, mirrored to `~/.gemini/hdc_daemon.log`.
- **Zero-config outputs**: `--output` is optional; JSON/screenshot/manifest default to `~/.gemini/` and `config --output-dir` changes the default directory once.
- **Correct power modes**: `keep-awake` uses `power-shell wakeup` + `timeout -o` (not performance mode 602); `performance` is opt-in and `normal` restores both mode 600 and the default timeout.

[中文文档](#中文文档) | [English Documentation](#english-documentation)

---

<a name="english-documentation"></a>
## English Documentation

### 🌟 Key Highlights

- **Zero Binary Dependency**: Full pure Python socket implementation of the HDC framing protocol and RSA-3072 auth handshake. Works without `hdc.exe`, DevEco Studio, or OpenHarmony SDK.
- **Lightning Fast (<15ms latency)**:
  - Single-packet `CMD_UNITY_EXECUTE (1001)` execution: eliminates PTY allocation delay and terminal echo noise.
  - Background keep-alive multiplexer daemon (`127.0.0.1:18710`) keeps the authenticated session open, cutting invocation overhead from `~450ms` to **`<15ms`**.
- **Kernel-Level `/bin/uinput` Fast-Path**: Direct kernel touch/key injection (`~90ms`), 5x faster than `uitest uiInput` (`~480ms`).
- **Single-Shot Closed-Loop Feedback (`-F diff`)**:
  - Combines `Action + Settle Wait + Post-Action dumpLayout` in **1 single on-device pipeline**.
  - Automatically computes UI tree diffs (`scene_changed`, `added_elements`, `removed_texts`).
- **Taobao-Resilient Zero-Inertia Scrolling (`-k 220`) & Native Viewport Slices**:
  - **Dead-Stop Scroll**: Uses `uinput -T -m x1 y1 x2 y2 -k 220 450` (holding touch stationary for 220ms before lift-off), completely killing inertial fling in ArkUI `Scroll` and `WaterFlow` lists.
  - **Multi-Patch 2D Consensus**: 6 independent sub-patches across left and right waterfall columns compute median scroll distance, immune to live video cards and animated GIF badges.
  - **Native Viewport Slices**: Saves un-stitched native viewport frames (`_slice_01.jpg`, `_slice_02.jpg`) for 100% seam-free, artifact-free AI reading.
- **Universal Progressive JPEG Compression**: Native-resolution capture + 4:2:0 progressive JPEG (`quality=68`, 1000px wide by default), shrinking screenshots to **50KB–100KB** with 100% universal compatibility across all AI vision models and agent tools.

---

### 📊 Performance Comparison

| Metric / Feature | Official `hdc.exe` | Typical UI Automator / Appium | **HDC-Claw** |
| :--- | :---: | :---: | :---: |
| **External Dependencies** | Requires DevEco / SDK | Node.js + Drivers + Server | **Pure Python Only** |
| **Connection Handshake** | ~450ms per call | Reconnect / Session check | **<15ms (via Daemon)** |
| **Touch Execution** | ~480ms (`uitest`) | ~500ms | **~90ms (`uinput` fast-path)** |
| **Action + Feedback Loop** | 2–3 commands (~1500ms) | Multiple HTTP roundtrips | **1 command (`-F diff`, ~350ms)** |
| **Long Page Scrolling** | Prone to inertial fling | Breaks in waterfall/videos | **Zero-Inertia Dead-Stop (`-k 220`)** |
| **Screenshot Size & Speed** | ~4MB raw / ~1200ms | PNG ~600KB / ~900ms | **~60KB JPEG / ~550ms** |

---

### 🚀 Installation

```bash
git clone https://github.com/encauporce/HDC-Claw.git
cd HDC-Claw
pip install -e .
```

Or install a pinned release archive instead of the moving `main` branch:

```bash
pip install https://github.com/encauporce/HDC-Claw/archive/refs/tags/v3.3.0.tar.gz
```

Requires **Python 3.9+**, `cryptography`, and `Pillow`. Verify with:

```bash
hdc-claw --version   # -> hdc-claw 3.3.0
```

---

### 🔌 Device prerequisites

Nothing needs to be installed on the tablet, but wireless HDC must be enabled before the first connection:

1. On the device, turn on **Developer options → Wireless debugging**, and note the `IP:port` it shows. HDC listens on a *random high port that changes* every time the toggle (or the device) is restarted.
2. Put the PC and the device on the **same L2 segment** (same LAN / same hotspot). Cross-NAT or VLAN-isolated networks will time out at the TCP handshake.
3. Run `hdc-claw --host <ip> --port <port> connect`. The first handshake makes the device pop up an **HDC debugging authorization dialog** (RSA-3072 fingerprint) — accept it, otherwise the session is refused.
4. Lost the port? `hdc-claw --host <ip> --scan connect` scans `30000-49999`. If the device reports a port outside that range, pass it explicitly with `--port`.
5. Reboot the tablet, or toggle wireless debugging, and re-run step 3 — the cached daemon session is no longer valid.

---

### 💻 Command Line Interface (CLI)

```powershell
# 1. Connect to tablet (auto-scans ports 30000-49999 if needed)
hdc-claw --host 192.168.1.115 --port 35429 connect

# 2. Wake plus extend the screen-off timeout (NOT performance mode)
hdc-claw screen --mode keep-awake --timeout-ms 7200000

# 3. Launch app and get initial UI summary in ONE command
hdc-claw launch --app "淘宝" -F summary

# 4. Tap element by text with warm cache (<5ms) and return UI diff
hdc-claw tap --text "我的淘宝" -F diff

# 5. One-shot text input with auto-focus and Enter submission
hdc-claw input-text --target-text "搜索" --text "华为平板" --submit -F diff

# 6. UI diff + annotated screenshot from the same on-device pipeline
hdc-claw tap --text "我的订单" -F both --annotate

# 7. Auto feedback: UI on readable pages, screenshot on canvas-only pages
hdc-claw tap --text "立即购买" -F auto

# 8. Crop only the matched element (tap/input-text only)
hdc-claw tap --text "筛选" -F crop

# 9. High-compression snapshot (50-80KB progressive JPEG)
hdc-claw screen --mode snapshot

# 10. Long page capture with zero-inertia dead-stop scrolling
hdc-claw long-screen --pages 4 --keep-ms 220

# Outputs are optional: JSON -> ~/.gemini/hdc_last_output.json,
# snapshot -> hdc_last_screen.jpg, long-screen -> hdc_last_long.json.
# Change the default directory once:
hdc-claw config --output-dir D:\hdc_runs

# Sweep leftover device temp files
hdc-claw clean-temp
```

---

### 📦 Subcommand reference

All 17 subcommands, straight from `argparse` in `src/hdc_claw/cli.py`:

| Subcommand | What it does |
| :--- | :--- |
| `connect` | Handshake, warm up the daemon, and cache the session |
| `daemon` | Manage the keep-alive multiplexer on `127.0.0.1:18710` (`--action start\|stop\|status\|serve`) |
| `shell` | Run one device shell command through `CMD_UNITY_EXECUTE (1001)` |
| `dump-ui` | Dump the layout tree (semantic-compact by default) |
| `tap` | Locate by `--text` / `--key` (warm cache) or `--x` / `--y`, tap, return feedback |
| `input-text` | One-shot UTF-8 (Chinese) injection: `--target-text` focus, `--submit`, `--hide-keyboard` |
| `batch` | Several semantic/coordinate steps in one session (`tap-text`, `wait-text`, …) |
| `launch` | Start an app by Chinese/English display name or bundle name (`--list-apps` to enumerate) |
| `push` | Local → device transfer with SHA256 verification |
| `pull` | Device → local binary-clean stream |
| `install` | Push and install a local `.hap` |
| `screen` | Power modes `keep-awake` / `performance` / `normal`, plus `snapshot` and `long-snapshot` |
| `long-screen` | Zero-inertia dead-stop scroll capture → native viewport slices + manifest |
| `ui` | Low-level primitive: `click` / `double-click` / `long-click` / `swipe` / `key` / `text` |
| `clean-temp` | Delete leftover temp images and UI dumps on the device |
| `config` | Show or set persistent defaults (`--output-dir`) |
| `deliver-file` | Drop a file into the public Download folder and open it with a target app |

Flags that are shared across the action subcommands:

| Flag | Values / default |
| :--- | :--- |
| `--host` / `--port` | Falls back to the last connected host; port is the wireless-debugging port |
| `--scan` | Force a `30000-49999` port sweep when the connection times out |
| `--no-daemon` | Skip `127.0.0.1:18710` and open a fresh TCP + RSA session |
| `-F`, `--feedback` | `none` (default) `diff` `summary` `full` `screen` `both` `auto` `crop` |
| `-W`, `--wait-after` | On-device settle wait before capture (default `0.35` s) |
| `--wait-change` | Re-check once after an extra 0.45 s if the UI has not changed |
| `--annotate` | Draw element boxes / IDs on `screen` / `both` / `auto` images |
| `--keep-device-temp` | Keep `/data/local/tmp` artifacts instead of deleting them after capture |

---

### ⚠️ Known limitations

- **Wireless HDC only.** There is no USB transport in this codebase; the device must be reachable over the network.
- **The HDC port is ephemeral.** It changes whenever wireless debugging is toggled or the device reboots, and the daemon's cached session goes stale at the same time — re-run `connect`.
- **`--scan` only covers `30000-49999`.** If the panel shows a port outside that band, pass `--port` yourself.
- **Default output paths are `~/.gemini/`** (`hdc_last_output.json`, `hdc_last_screen.jpg`, `hdc_last_long.json`), plus `~/.gemini/hdc_config.json` and `~/.gemini/hdc_daemon.log`. That directory name is a leftover from the agent runtime this tool was written in, not a dependency on any other product — change it once with `hdc-claw config --output-dir <dir>`.
- **The performance table is single-device self-measurement**, not a cross-device benchmark; your numbers will differ with Wi-Fi quality, model, and OS build.
- **`-F crop` needs a matched element**, so it applies to `tap` / `input-text` only.

---

### 🐍 Python SDK Usage

```python
from hdc_claw import resolve_session, build_tap_shell_cmd, execute_action_with_feedback

# Connects via persistent keep-alive daemon (<15ms)
sess = resolve_session(host="192.168.1.115", port=35429)

# 1. Execute fast shell command
print(sess.run_shell("param get const.product.name"))

# 2. Execute closed-loop tap with instant diff feedback
tap_cmd = build_tap_shell_cmd(cx=500, cy=800, action="click")
out, diff = execute_action_with_feedback(sess, tap_cmd, feedback_mode="diff", wait_after=0.35)

print("Scene changed:", diff["scene_changed"])
print("Newly added elements:", diff["added_elements"])

# v3.3: UI diff + annotated screenshot from one on-device pipeline
_, fb = execute_action_with_feedback(
    sess, tap_cmd, feedback_mode="both", annotate=True, wait_after=0.35
)
print(fb["mode"], fb["screenshot_path"], fb["coord_scale"], fb["annotated"])

# Or let the toolkit choose: UI on readable pages, screenshot on canvas-only pages
_, auto = execute_action_with_feedback(sess, tap_cmd, feedback_mode="auto")
print("visual fallback:", auto.get("visual"))

sess.close()
```

---

<a name="中文文档"></a>
## 中文文档

### 🌟 核心特性与架构亮点

- **零外部二进制依赖**：纯 Python socket 完整实现 HDC 二进制通信协议与 RSA-3072 握手鉴权。无需下载安装 DevEco Studio、无需 OpenHarmony SDK，更不需要任何外部 `hdc.exe`。
- **极致速度（单指令 <15ms 延迟）**：
  - 单包二进制无污染执行（`CMD_UNITY_EXECUTE = 1001`）：告别 PTY 终端分配延迟与控制字符噪声。
  - 本地常驻多路复用守护进程（`127.0.0.1:18710`）：通过 Windows WMI 跨作业对象保活认证 Session，免去每次执行约 `450ms` 的 TCP + RSA 握手重连开销。
- **内核级 `/bin/uinput` 触控极速通道**：直接注入 Linux input event，触控响应低至 `~90ms`（官方 `uitest` 约需 `~480ms`）。
- **预热 UI 缓存机制（Warm UI Cache）**：语义查找控件（`tap --text`、`input-text --target-text`）在 `<5ms` 内直接完成坐标命中与点击。
- **单指令闭环反馈机制（`-F diff`）**：
  - 将「动作执行 + 页面渲染等待 + 页面 Dump」合并为设备端 **一条指令管道**，彻底省去中间多次网络往返。
  - 自动输出结构化 Diff（`scene_changed`、`added_elements`、`removed_texts`），大幅减少大模型 Agent 思考与调度步数。
- **淘宝复杂瀑布流抗性与零惯性定距滚动（`-k 220`）**：
  - **零惯性急停触控**：调用 `/bin/uinput -T -m x1 y1 x2 y2 -k 220 450`，在拖动结束后保持悬停按压 220ms 再松手，彻底吸收 ArkUI `Scroll` / `WaterFlow` 动能，解决传统长截屏在淘宝等瀑布流应用中的滑动漂移 Bug。
  - **左右双列 6 区域共识测距**：左右两列商品流各采样 3 个独立 Patch 计算位移中位数，即使某张商品卡片正在播放视频或有动态浮标，依然能 100% 精准判定滚动触底与视口衔接。
  - **原生视口切片直出（Native Viewport Slices）**：直接输出无拼接拼缝的原生视口切片（`_slice_01.jpg`、`_slice_02.jpg`），避免大模型因切片撕裂卡片和文字而产生幻觉。
- **通用渐进式高压缩 JPEG**：原生分辨率采集 + Progressive JPEG（`subsampling=2, quality=68`，默认 1000px 宽），截图仅 **50KB–100KB**，在所有大模型、Agent 文件解析器与工具链中拥有 **100% 原生兼容性**。

---

### 🆕 v3.3 更新

- **闭环反馈扩展**：`-F both`（UI diff + 截图，一条设备端管道）、`-F auto`（有可读文本回 UI，画布页回截图）、`-F crop`（只裁匹配元素）、`--annotate`（截图上画元素框和 ID）。
- **设备临时文件清理**：`/data/local/tmp` 下的临时图和 UI dump 默认在采集后删除；`--keep-device-temp` 保留，`clean-temp` 扫尾。
- **daemon 实时控制台**：`TIME | ID | DUR | GAP | CMD` 计时表，`SLOW`/`ERR` 标记，`--verbose` 显示每步操作细节，日志镜像到 `~/.gemini/hdc_daemon.log`。
- **输出零配置**：所有 `--output` 可选，默认 `~/.gemini/hdc_last_output.json` / `hdc_last_screen.jpg` / `hdc_last_long.json`；`config --output-dir` 一次改默认目录。
- **电源模式修正**：`keep-awake` = `power-shell wakeup` + `timeout -o`，不再误开 602 性能模式；`performance` 改为显式开启，`normal` 恢复 600 + 默认超时。

---

### 📦 安装与发布版

```bash
pip install -e .                                            # 跟踪 main
pip install https://github.com/encauporce/HDC-Claw/archive/refs/tags/v3.3.0.tar.gz   # 锁定发布版
```

发布记录与打包下载见 [Releases](https://github.com/encauporce/HDC-Claw/releases)，最新为 [`v3.3.0`](https://github.com/encauporce/HDC-Claw/releases/tag/v3.3.0)。开箱即用的 Agent Skill 规范在 [`skills/harmonyos-hdc/SKILL.md`](skills/harmonyos-hdc/SKILL.md)，`examples/` 下有两条可直接跑的最小脚本。

### 📖 子命令总表

17 个子命令（`connect` `daemon` `shell` `dump-ui` `tap` `input-text` `batch` `launch` `push` `pull` `install` `screen` `long-screen` `ui` `clean-temp` `config` `deliver-file`）与共享参数（`-F` `-W` `--scan` `--no-daemon` `--annotate` `--wait-change` `--keep-device-temp`）的逐项说明，见上文 [Subcommand reference](#-subcommand-reference)。

### 🔌 设备端准备

1. 设备打开「开发者选项 → 无线调试」，记下显示的 `IP:端口`。这个端口在每次重新开关无线调试或重启后都会变化。
2. 电脑与设备必须在**同一局域网/同一热点**，跨网段会在 TCP 握手阶段直接超时。
3. 首次 `connect` 时设备端会弹出 **HDC 调试授权**（RSA-3072 指纹）对话框，必须点允许，否则鉴权被拒。
4. 忘记端口就加 `--scan`（扫描 `30000-49999`）；不在该区间就手动 `--port`。

### ⚠️ 已知限制

- **只支持无线 HDC**，代码里没有 USB 通道，设备必须网络可达。
- 端口变化或设备重启后，守护进程缓存的 session 即失效，需要重新 `connect`。
- 默认产物写在 `~/.gemini/`（`hdc_last_output.json` / `hdc_last_screen.jpg` / `hdc_last_long.json`），配置在 `~/.gemini/hdc_config.json`，daemon 日志在 `~/.gemini/hdc_daemon.log`。这个目录名是本项目早期运行环境的遗留，不代表依赖任何其它产品，可用 `config --output-dir` 一次性改掉。
- 性能对比表是作者单台设备的自测数据，不是跨机型基准；受 Wi-Fi 质量、机型和系统版本影响很大。
- `-F crop` 依赖命中的元素，只对 `tap` / `input-text` 有效。

---

### 🧠 大脑-小脑（Brain–Cerebellum）分层架构建议

在构建 AI Agent 时，UI 自动化频繁生成的 Dump 树与截图容易迅速占满上下文。HDC-Claw 原生推荐**大脑-小脑策略**：
- **主 Agent（大脑 / Brain）**：负责全局业务目标规划，单步或简短操作（$\le 2$ 步）可直接调用。
- **子 Agent（小脑 / Cerebellum）**：当遇到多步操作、表单填写或长页面理解时，派发独立的子 Agent 执行 `hdc-claw` 闭环循环，在子上下文中逐片读取 `_slice_01.jpg`, `_slice_02.jpg`，最终仅向主 Agent 汇报精简结构化结论与最终确认截图。

内置开箱即用的 Agent Skill 规范已收录于 [`skills/harmonyos-hdc/SKILL.md`](skills/harmonyos-hdc/SKILL.md)。

---

### 📄 开源许可证

本项目基于 [MIT 许可证](LICENSE) 开源。欢迎 Star、Issue 与 Pull Request！
