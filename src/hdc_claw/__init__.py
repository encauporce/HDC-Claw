"""
HDC-Claw: Pure-Python HarmonyOS NEXT / 6.0 HDC Protocol Client & AI Agent UI Automation Toolkit.
"""

from .daemon import (
    DAEMON_HOST,
    DAEMON_PORT,
    DaemonClientSession,
    LocalHdcDaemonServer,
    resolve_session,
    spawn_background_daemon,
    try_connect_daemon,
)
from .protocol import (
    HdcSession,
    ensure_harmony_keys,
    probe_hdc_port,
    pull_file_over_shell,
    push_file_over_shell,
    scan_hdc_ports,
)
from .ui import (
    KNOWN_APPS,
    analyze_and_flatten_ui,
    build_base64_input_cmd,
    build_key_shell_cmd,
    build_scroll_shell_cmd,
    build_swipe_shell_cmd,
    build_tap_shell_cmd,
    compute_ui_diff,
    execute_action_with_feedback,
    find_matching_elements,
    get_local_lan_ip,
    invalidate_ui_cache,
    load_ui_cache,
    resolve_element_from_cache_or_dump,
    save_ui_cache,
)
from .vision import (
    compress_image_bytes,
    run_long_screen,
    stitch_and_slice_frames,
)

__version__ = "1.0.0"
__all__ = [
    "HdcSession",
    "DaemonClientSession",
    "LocalHdcDaemonServer",
    "resolve_session",
    "try_connect_daemon",
    "spawn_background_daemon",
    "probe_hdc_port",
    "scan_hdc_ports",
    "ensure_harmony_keys",
    "push_file_over_shell",
    "pull_file_over_shell",
    "build_tap_shell_cmd",
    "build_swipe_shell_cmd",
    "build_scroll_shell_cmd",
    "build_key_shell_cmd",
    "build_base64_input_cmd",
    "analyze_and_flatten_ui",
    "find_matching_elements",
    "compute_ui_diff",
    "execute_action_with_feedback",
    "resolve_element_from_cache_or_dump",
    "compress_image_bytes",
    "run_long_screen",
    "stitch_and_slice_frames",
    "get_local_lan_ip",
    "DAEMON_HOST",
    "DAEMON_PORT",
    "KNOWN_APPS",
]
