# HDC-Claw 🐾

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-green.svg)](https://www.python.org/downloads/)
[![HarmonyOS](https://img.shields.io/badge/HarmonyOS-NEXT%20%2F%206.0-red.svg)](https://developer.huawei.com/consumer/cn/harmonyos/)
[![OpenHarmony](https://img.shields.io/badge/Protocol-OpenHarmony%20HDC-orange.svg)](https://gitee.com/openharmony)

**Pure-Python HarmonyOS NEXT / 6.0 HDC Wireless Protocol Client & AI Agent Automation Toolkit.**

Zero external binaries. Zero DevEco Studio / SDK requirements. Native RSA-3072 handshake, persistent keep-alive daemon (<15ms latency), kernel-level `/bin/uinput` touch/key fast-path, closed-loop UI diff feedback, and Taobao-resilient zero-inertia visual long-screenshots.

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
- **Universal Progressive JPEG Compression**: Hardware downscaling (`1400x920`, 0.5x scale) + 4:2:0 progressive JPEG (`quality=68`), shrinking screenshots to **50KB–80KB** with 100% universal compatibility across all AI vision models and agent tools.

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

Requires **Python 3.9+**, `cryptography`, and `Pillow`.

---

### 💻 Command Line Interface (CLI)

```powershell
# 1. Connect to tablet (auto-scans ports 30000-49999 if needed)
hdc-claw --host 192.168.1.115 --port 35429 connect

# 2. Keep screen awake
hdc-claw screen --mode keep-awake

# 3. Launch app and get initial UI summary in ONE command
hdc-claw launch --app "淘宝" -F summary

# 4. Tap element by text with warm cache (<5ms) and return UI diff
hdc-claw tap --text "我的淘宝" -F diff

# 5. One-shot text input with auto-focus and Enter submission
hdc-claw input-text --target-text "搜索" --text "华为平板" --submit -F diff

# 6. High-compression snapshot (50-80KB progressive JPEG)
hdc-claw screen --mode snapshot --output screen.jpg

# 7. Long page capture with zero-inertia dead-stop scrolling
hdc-claw long-screen --pages 4 --keep-ms 220 --output manifest.json
```

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
- **通用渐进式高压缩 JPEG**：硬件渲染层降采样（`1400x920`，0.5x 视口）+ Progressive JPEG（`subsampling=2, quality=68`），截图仅 **50KB–80KB**，在所有大模型、Agent 文件解析器与工具链中拥有 **100% 原生兼容性**。

---

### 🧠 大脑-小脑（Brain–Cerebellum）分层架构建议

在构建 AI Agent 时，UI 自动化频繁生成的 Dump 树与截图容易迅速占满上下文。HDC-Claw 原生推荐**大脑-小脑策略**：
- **主 Agent（大脑 / Brain）**：负责全局业务目标规划，单步或简短操作（$\le 2$ 步）可直接调用。
- **子 Agent（小脑 / Cerebellum）**：当遇到多步操作、表单填写或长页面理解时，派发独立的子 Agent 执行 `hdc-claw` 闭环循环，在子上下文中逐片读取 `_slice_01.jpg`, `_slice_02.jpg`，最终仅向主 Agent 汇报精简结构化结论与最终确认截图。

内置开箱即用的 Agent Skill 规范已收录于 [`skills/harmonyos-hdc/SKILL.md`](skills/harmonyos-hdc/SKILL.md)。

---

### 📄 开源许可证

本项目基于 [MIT 许可证](LICENSE) 开源。欢迎 Star、Issue 与 Pull Request！
