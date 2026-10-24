#!/usr/bin/env python3
"""
HDC-Claw: Brain-Cerebellum Closed-Loop Agent Loop Example.

Demonstrates how an AI Agent executes multi-step UI navigation with
zero context pollution and 100% reliable feedback.
"""

from hdc_claw import (
    resolve_session,
    build_tap_shell_cmd,
    execute_action_with_feedback,
    resolve_element_from_cache_or_dump,
    run_long_screen,
)
from pathlib import Path

def agent_search_and_read(sess, keyword: str):
    print(f"[*] Agent searching for '{keyword}'...")

    # 1. Locate search box using warm UI cache (<5ms)
    target_el, _, _, used_cache = resolve_element_from_cache_or_dump(
        sess, text_query="搜索", allow_cache=True
    )
    print(f"[*] Located search box at {target_el['center']} (used_cache={used_cache})")

    # 2. Perform closed-loop tap with instant post-action diff
    cx, cy = target_el["center"]
    tap_cmd = build_tap_shell_cmd(cx, cy, "click")
    _, diff = execute_action_with_feedback(sess, tap_cmd, feedback_mode="diff", wait_after=0.35)

    print(f"[*] Scene changed: {diff['scene_changed']}, newly added targets: {diff['added_count']}")

    # 3. If reading a long page (e.g., search results or waterfall cards)
    print("[*] Capturing zero-inertia long screenshot with native slices...")
    manifest = run_long_screen(
        sess,
        max_pages=3,
        keep_ms=220,  # Dead-stop eliminates inertial fling in Taobao / ArkUI lists!
        out_dir=Path("./output_slices"),
        stem="search_results",
    )

    print(f"[*] Long screen done! Generated {manifest['slice_count']} slices.")
    for s in manifest["slices"]:
        print(f"    - Slice {s['slice_index']}: {s['path']} ({s['size_bytes']} bytes)")

def main():
    sess = resolve_session()
    try:
        agent_search_and_read(sess, "日历")
    finally:
        sess.close()

if __name__ == "__main__":
    main()
