"""
Persistent paths, output defaults, and shared runtime metadata for HDC-Claw.

`--output` is optional on every subcommand: JSON results default to
`~/.gemini/hdc_last_output.json`, snapshots to `~/.gemini/hdc_last_screen.jpg`,
and long-screen manifests to `~/.gemini/hdc_last_long.json`. `config --output-dir`
changes the default directory once.
"""

from __future__ import annotations

import json
from pathlib import Path

DAEMON_LOG_PATH = Path.home() / ".gemini" / "hdc_daemon.log"
CONFIG_PATH = Path.home() / ".gemini" / "hdc_config.json"
DEFAULT_OUTPUT_NAME = "hdc_last_output.json"
DEFAULT_SCREENSHOT_NAME = "hdc_last_screen.jpg"
DEFAULT_LONG_NAME = "hdc_last_long.json"

# Set by cli.main() so every JSON carries its originating subcommand.
CURRENT_SUBCOMMAND = ""
# Set by cli.main(): identifies this CLI invocation in daemon telemetry.
CLIENT_META: dict = {}

DEVICE_TMP_FILES = [
    "/data/local/tmp/snap_1.jpeg",
    "/data/local/tmp/hdc_snap.jpeg",
    "/data/local/tmp/hdc_long.jpeg",
    "/data/local/tmp/hdc_ui.json",
    "/data/local/tmp/hdc_ui_i.json",
]


def load_hdc_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def hdc_output_dir() -> Path:
    configured = load_hdc_config().get("output_dir")
    if configured:
        try:
            path = Path(str(configured)).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            pass
    return Path.home() / ".gemini"


def resolve_output_path(
    path: str | Path | None, default_name: str = DEFAULT_OUTPUT_NAME
) -> Path:
    if path:
        return Path(path)
    return hdc_output_dir() / default_name


def write_json_output(
    path: str | Path | None,
    payload: dict,
    default_name: str = DEFAULT_OUTPUT_NAME,
):
    if CURRENT_SUBCOMMAND:
        payload.setdefault("subcommand", CURRENT_SUBCOMMAND)
    out_path = resolve_output_path(path, default_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Success! Output written to: {out_path}")


def fmt_ms(ms: float) -> str:
    return f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.2f}s"


def cleanup_device_temp_shell(include_b64: bool = False) -> str:
    cmd = "rm -f " + " ".join(DEVICE_TMP_FILES)
    if include_b64:
        cmd += " /data/local/tmp/*.b64"
    return cmd
