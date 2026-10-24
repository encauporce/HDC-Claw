#!/usr/bin/env python3
"""
HDC-Claw: Quick Start Example.

Demonstrates:
1. Connecting to HarmonyOS NEXT / 6.0 device over wireless debugging.
2. Executing shell commands with ultra-low latency (<15ms via daemon).
3. Dumping and filtering the UI layout tree.
4. Tapping an element by semantic text with automatic closed-loop diff feedback.
"""

from hdc_claw import (
    resolve_session,
    build_tap_shell_cmd,
    execute_action_with_feedback,
    analyze_and_flatten_ui,
    run_long_screen,
)

def main():
    # 1. Connect to device (automatically reuses daemon if running)
    sess = resolve_session(host="192.168.1.115", port=35429)
    print(f"Connected to {sess.host}:{sess.port} (via daemon: {getattr(sess, 'is_daemon_client', False)})")

    # 2. Run shell command
    sys_name = sess.run_shell("param get const.product.name").strip()
    print(f"Device Model: {sys_name}")

    # 3. Dump compact UI tree
    tree = sess.dump_ui_tree()
    scene, elements = analyze_and_flatten_ui(tree, compact=True)
    print(f"Detected {len(elements)} clickable/text elements on screen.")

    # 4. Closed-loop action with instant UI diff feedback
    # Executes tap + wait + dumpLayout in a single on-device pipeline!
    sh_cmd = build_tap_shell_cmd(cx=500, cy=500, action="click")
    _, feedback = execute_action_with_feedback(sess, sh_cmd, feedback_mode="diff", wait_after=0.35)
    print("Action diff feedback:", feedback.get("scene_changed"), f"Added {feedback.get('added_count')} elements")

    sess.close()

if __name__ == "__main__":
    main()
