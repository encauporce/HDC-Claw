"""
Command Line Interface for HDC-Claw.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .daemon import (
    DAEMON_HOST,
    DAEMON_PORT,
    LocalHdcDaemonServer,
    resolve_session,
    spawn_background_daemon,
    try_connect_daemon,
)
from .protocol import (
    ANSI_ESCAPE_RE,
    load_saved_target,
    pull_file_over_shell,
    push_file_over_shell,
)
from .ui import (
    KNOWN_APPS,
    analyze_and_flatten_ui,
    build_base64_input_cmd,
    build_key_shell_cmd,
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
)


def write_json_output(path: str | None, payload: dict):
    if not path:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Success! Output written to: {out_path}")


def cmd_daemon(args):
    saved = load_saved_target()
    target_host = args.host or saved.get("host", "192.168.1.115")
    target_port = args.port or saved.get("port", 35429)

    if args.action == "serve":
        srv = LocalHdcDaemonServer(target_host, target_port)
        srv.serve_forever()
        return

    if args.action == "status":
        client = try_connect_daemon(target_host, target_port, timeout=0.3)
        if client is None:
            res = {"running": False, "daemon_addr": f"{DAEMON_HOST}:{DAEMON_PORT}"}
        else:
            try:
                info = client._call({"op": "ping"})
                res = {"running": True, "daemon_addr": f"{DAEMON_HOST}:{DAEMON_PORT}", **info}
            finally:
                client.close()
        write_json_output(args.output, res)
        return

    if args.action == "stop":
        client = try_connect_daemon(target_host, target_port, timeout=0.3)
        if client is not None:
            try:
                client._call({"op": "stop"})
            except Exception:
                pass
            finally:
                client.close()
        res = {"status": "stopped"}
        write_json_output(args.output, res)
        return

    if args.action == "start":
        client = try_connect_daemon(target_host, target_port, timeout=0.2)
        if client is None:
            spawn_background_daemon(target_host, target_port)
            for _ in range(25):
                time.sleep(0.06)
                client = try_connect_daemon(target_host, target_port, timeout=0.1)
                if client is not None:
                    break
        if client is None:
            raise RuntimeError("Failed to start background HDC daemon")
        try:
            info = client._call({"op": "ping"})
        finally:
            client.close()
        res = {"status": "started", "daemon_addr": f"{DAEMON_HOST}:{DAEMON_PORT}", **info}
        write_json_output(args.output, res)


def cmd_connect(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, auto_scan=True, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        raw = sess.run_shell(
            "param get const.product.name; param get const.ohos.fullname; aa dump -l | head -n 20",
            timeout=6.0,
        )
    finally:
        sess.close()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    write_json_output(
        args.output,
        {
            "status": "connected",
            "host": sess.host,
            "port": sess.port,
            "via_daemon": getattr(sess, "is_daemon_client", False),
            "elapsed_ms": elapsed_ms,
            "device_info": ANSI_ESCAPE_RE.sub("", raw).strip(),
        },
    )


def cmd_shell(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        raw = sess.run_shell(args.cmd, timeout=args.timeout, use_pty=args.pty)
    finally:
        sess.close()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    cleaned = ANSI_ESCAPE_RE.sub("", raw)
    write_json_output(
        args.output,
        {
            "host": sess.host,
            "port": sess.port,
            "via_daemon": getattr(sess, "is_daemon_client", False),
            "elapsed_ms": elapsed_ms,
            "command": args.cmd,
            "output": cleaned,
        },
    )


def cmd_dump_ui(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    used_cache = False
    try:
        if args.use_cache:
            cache = load_ui_cache()
            if (
                cache
                and cache.get("valid")
                and cache.get("tree")
                and (time.time() - cache.get("timestamp", 0)) <= args.cache_ttl
            ):
                tree = cache["tree"]
                used_cache = True
            else:
                tree = sess.dump_ui_tree()
        else:
            tree = sess.dump_ui_tree()
    finally:
        sess.close()

    filter_re = re.compile(args.filter, re.I) if args.filter else None
    compact = not args.raw
    scene, elements = analyze_and_flatten_ui(
        tree, filter_re=filter_re, compact=compact, include_statusbar=args.include_statusbar
    )
    if not used_cache and filter_re is None and compact:
        save_ui_cache(sess.host, sess.port, tree, scene, elements)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    write_json_output(
        args.output,
        {
            "host": sess.host,
            "port": sess.port,
            "via_daemon": getattr(sess, "is_daemon_client", False),
            "used_cache": used_cache,
            "elapsed_ms": elapsed_ms,
            "mode": "raw" if args.raw else "compact",
            "filter": args.filter,
            "scene": scene,
            "element_count": len(elements),
            "elements": elements,
        },
    )


def cmd_tap(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        target_el = None
        before_scene = None
        before_elements = None
        used_cache = False

        if args.x is not None and args.y is not None:
            cx, cy = args.x, args.y
        else:
            if not args.text and not args.key:
                raise ValueError("Must specify --text, --key, or both --x and --y for tap.")
            target_el, before_elements, before_scene, used_cache = (
                resolve_element_from_cache_or_dump(
                    sess,
                    text_query=args.text,
                    key_query=args.key,
                    index=args.index,
                    allow_cache=not args.fresh,
                )
            )
            cx, cy = target_el["center"]

        sh_cmd = build_tap_shell_cmd(cx, cy, action=args.action, engine=args.engine)
        fb_mode = args.feedback
        if fb_mode == "none" and args.dump_after:
            fb_mode = "summary"

        out_str, fb_data = execute_action_with_feedback(
            sess,
            sh_cmd,
            feedback_mode=fb_mode,
            wait_after=args.wait_after,
            before_scene=before_scene,
            before_elements=before_elements,
            wait_change=args.wait_change,
        )
    finally:
        sess.close()

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    res = {
        "action": args.action,
        "engine": args.engine,
        "via_daemon": getattr(sess, "is_daemon_client", False),
        "locator_used_cache": used_cache,
        "elapsed_ms": elapsed_ms,
        "tapped_coords": [cx, cy],
        "matched_element": target_el,
        "command": sh_cmd,
        "output": out_str,
    }
    if fb_data:
        res["feedback"] = fb_data
        if args.dump_after and "after_elements" in fb_data:
            res["after_scene"] = fb_data.get("after_scene")
            res["after_element_count"] = fb_data.get("after_element_count")
            res["after_elements"] = fb_data.get("after_elements")
    write_json_output(args.output, res)


def cmd_input_text(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        target_el = None
        before_scene = None
        before_elements = None
        used_cache = False

        if args.x is not None and args.y is not None:
            cx, cy = args.x, args.y
        else:
            target_el, before_elements, before_scene, used_cache = (
                resolve_element_from_cache_or_dump(
                    sess,
                    text_query=args.target_text,
                    key_query=args.key,
                    elem_types=("TextInput", "TextArea", "SearchField")
                    if not (args.target_text or args.key)
                    else None,
                    allow_cache=not args.fresh,
                )
            )
            cx, cy = target_el["center"]

        cmds = []
        if args.clear_first:
            cmds.append(build_tap_shell_cmd(cx, cy, action="click", engine=args.engine))
        cmds.append(build_base64_input_cmd(cx, cy, args.text))
        if args.press_enter or args.submit:
            cmds.append("sleep 0.15; " + build_key_shell_cmd(2054, engine=args.engine))
        if args.hide_keyboard:
            cmds.append("sleep 0.15; " + build_key_shell_cmd("Back", engine=args.engine))

        full_cmd = "; ".join(cmds)
        out_str, fb_data = execute_action_with_feedback(
            sess,
            full_cmd,
            feedback_mode=args.feedback,
            wait_after=args.wait_after,
            before_scene=before_scene,
            before_elements=before_elements,
            wait_change=args.wait_change,
        )
    finally:
        sess.close()

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    res = {
        "status": "input_sent",
        "via_daemon": getattr(sess, "is_daemon_client", False),
        "locator_used_cache": used_cache,
        "elapsed_ms": elapsed_ms,
        "coords": [cx, cy],
        "matched_element": target_el,
        "text": args.text,
        "submitted": bool(args.press_enter or args.submit),
        "output": out_str,
    }
    if fb_data:
        res["feedback"] = fb_data
    write_json_output(args.output, res)


def cmd_batch(args):
    t0 = time.perf_counter()
    steps = json.loads(args.steps)
    if not isinstance(steps, list):
        raise ValueError("--steps must be a JSON array of step strings")

    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    results = []
    engine = args.engine
    last_scene = None
    last_elements = None

    try:
        for step in steps:
            step_str = step.strip()
            parts = step_str.split(maxsplit=1)
            verb = parts[0].lower()
            rest = parts[1] if len(parts) > 1 else ""

            if verb == "sleep":
                dur = float(rest)
                time.sleep(dur)
                results.append({"step": step_str, "status": f"slept {dur}s"})
            elif verb in ("click", "double-click", "long-click"):
                x_s, y_s = rest.split()
                sh_cmd = build_tap_shell_cmd(int(x_s), int(y_s), action=verb, engine=engine)
                invalidate_ui_cache()
                out = sess.run_shell(sh_cmd, timeout=8.0)
                results.append({"step": step_str, "command": sh_cmd, "output": out.strip()})
            elif verb == "swipe":
                coords = rest.split()
                x1, y1, x2, y2 = map(int, coords[:4])
                dur = int(coords[4]) if len(coords) > 4 else 400
                sh_cmd = build_swipe_shell_cmd(x1, y1, x2, y2, duration_ms=dur, engine=engine)
                invalidate_ui_cache()
                out = sess.run_shell(sh_cmd, timeout=8.0)
                results.append({"step": step_str, "command": sh_cmd, "output": out.strip()})
            elif verb in ("key", "back", "home", "submit"):
                k_val = {"back": "Back", "home": "Home", "submit": "2054"}.get(verb, rest)
                sh_cmd = build_key_shell_cmd(k_val, engine=engine)
                invalidate_ui_cache()
                out = sess.run_shell(sh_cmd, timeout=6.0)
                results.append({"step": step_str, "command": sh_cmd, "output": out.strip()})
            elif verb == "text":
                t_parts = rest.split(maxsplit=2)
                x, y, txt = int(t_parts[0]), int(t_parts[1]), t_parts[2]
                invalidate_ui_cache()
                out = sess.run_shell(build_base64_input_cmd(x, y, txt), timeout=8.0)
                results.append({"step": step_str, "output": out.strip()})
            elif verb in ("tap-text", "tap-key"):
                el, last_elements, last_scene, used_cache = resolve_element_from_cache_or_dump(
                    sess,
                    text_query=rest if verb == "tap-text" else None,
                    key_query=rest if verb == "tap-key" else None,
                    allow_cache=True,
                )
                cx, cy = el["center"]
                sh_cmd = build_tap_shell_cmd(cx, cy, action="click", engine=engine)
                invalidate_ui_cache()
                out = sess.run_shell(sh_cmd, timeout=8.0)
                results.append(
                    {
                        "step": step_str,
                        "tapped_coords": [cx, cy],
                        "used_cache": used_cache,
                        "matched": el,
                        "output": out.strip(),
                    }
                )
            elif verb == "wait-text":
                w_parts = rest.rsplit(maxsplit=1)
                if len(w_parts) == 2 and re.fullmatch(r"\d+(\.\d+)?", w_parts[1]):
                    query, timeout_s = w_parts[0], float(w_parts[1])
                else:
                    query, timeout_s = rest, 6.0
                deadline = time.time() + timeout_s
                found_el = None
                while time.time() < deadline:
                    tree = sess.dump_ui_tree()
                    last_scene, last_elements = analyze_and_flatten_ui(tree, compact=True)
                    save_ui_cache(sess.host, sess.port, tree, last_scene, last_elements)
                    _, all_els = analyze_and_flatten_ui(tree, compact=False, include_statusbar=True)
                    matches = find_matching_elements(all_els, text_query=query)
                    if matches:
                        found_el = matches[0]
                        break
                    time.sleep(0.25)
                if not found_el:
                    raise RuntimeError(f"wait-text '{query}' timed out after {timeout_s}s")
                results.append({"step": step_str, "status": "found", "matched": found_el})
            elif verb == "shell":
                out = sess.run_shell(rest, timeout=15.0)
                results.append({"step": step_str, "output": ANSI_ESCAPE_RE.sub("", out).strip()})
            elif verb in ("dump-ui", "dump-summary", "dump-diff"):
                prev_sc, prev_els = last_scene, last_elements
                tree = sess.dump_ui_tree()
                f_re = re.compile(rest, re.I) if (verb == "dump-ui" and rest) else None
                sc, els = analyze_and_flatten_ui(tree, filter_re=f_re, compact=True)
                if f_re is None:
                    save_ui_cache(sess.host, sess.port, tree, sc, els)
                last_scene, last_elements = sc, els
                if verb == "dump-diff":
                    results.append(
                        {"step": step_str, "diff": compute_ui_diff(prev_sc, prev_els, sc, els)}
                    )
                else:
                    results.append(
                        {"step": step_str, "scene": sc, "element_count": len(els), "elements": els}
                    )
            else:
                raise ValueError(f"Unknown batch verb: {verb}")

        fb_data = None
        if args.feedback and args.feedback != "none":
            _, fb_data = execute_action_with_feedback(
                sess,
                "true",
                feedback_mode=args.feedback,
                wait_after=args.wait_after,
                before_scene=last_scene,
                before_elements=last_elements,
            )
    finally:
        sess.close()

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    res = {
        "via_daemon": getattr(sess, "is_daemon_client", False),
        "elapsed_ms": elapsed_ms,
        "step_count": len(results),
        "results": results,
    }
    if fb_data:
        res["feedback"] = fb_data
    write_json_output(args.output, res)


def cmd_launch(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        if args.list_apps:
            raw = sess.run_shell("bm dump -a", timeout=10.0)
            bundles = [
                line.strip()
                for line in ANSI_ESCAPE_RE.sub("", raw).splitlines()
                if line.strip().startswith(("com.", "ohos.", "cn.", "org."))
            ]
            write_json_output(args.output, {"bundle_count": len(bundles), "bundles": bundles})
            return

        bundle = args.bundle
        ability = args.ability
        if args.app:
            key = args.app.strip().lower()
            if key in KNOWN_APPS:
                bundle, ability = KNOWN_APPS[key]
            elif args.app.strip() in KNOWN_APPS:
                bundle, ability = KNOWN_APPS[args.app.strip()]
            else:
                bundle = args.app.strip()

        if not bundle:
            raise ValueError("Specify --app <AppName>, --bundle <bundleName>, or --list-apps")

        if not ability:
            dump_raw = sess.run_shell(
                f"bm dump -n {bundle} | grep -E '\"name\": \".*Ability\"' | head -n 15",
                timeout=8.0,
            )
            found_abilities = re.findall(r'"name":\s*"([^"]*Ability[^"]*)"', dump_raw)
            for pref in ("MainAbility", "EntryAbility"):
                if pref in found_abilities:
                    ability = pref
                    break
            if not ability and found_abilities:
                ability = found_abilities[0]
            if not ability:
                ability = "MainAbility"

        uri_part = f" -U '{args.uri}'" if args.uri else ""
        sh_cmd = f"aa start -b {bundle} -a {ability}{uri_part}"
        out_str, fb_data = execute_action_with_feedback(
            sess,
            sh_cmd,
            feedback_mode=args.feedback,
            wait_after=args.wait_after,
        )
    finally:
        sess.close()

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    res = {
        "via_daemon": getattr(sess, "is_daemon_client", False),
        "elapsed_ms": elapsed_ms,
        "bundle": bundle,
        "ability": ability,
        "command": sh_cmd,
        "output": out_str,
    }
    if fb_data:
        res["feedback"] = fb_data
    write_json_output(args.output, res)


def cmd_push(args):
    local_path = Path(args.local).resolve()
    if not local_path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        info = push_file_over_shell(sess, local_path, args.remote)
    finally:
        sess.close()
    write_json_output(args.output, {"status": "pushed", **info})


def cmd_pull(args):
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        bytes_len = pull_file_over_shell(sess, args.remote, Path(args.local).resolve())
    finally:
        sess.close()
    print(f"Success! Pulled {bytes_len} bytes to: {args.local}")


def cmd_install(args):
    hap_path = Path(args.hap).resolve()
    if not hap_path.exists():
        raise FileNotFoundError(f"HAP file not found: {hap_path}")
    remote_tmp = f"/data/local/tmp/{hap_path.name}"
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        push_info = push_file_over_shell(sess, hap_path, remote_tmp)
        raw = sess.run_shell(f"bm install -p '{remote_tmp}'; rm -f '{remote_tmp}'", timeout=30.0)
    finally:
        sess.close()
    write_json_output(
        args.output,
        {
            "status": "install_executed",
            "push_info": push_info,
            "install_output": ANSI_ESCAPE_RE.sub("", raw).strip(),
        },
    )


def cmd_screen(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        if args.mode == "keep-awake":
            raw = sess.run_shell("power-shell wakeup; power-shell setmode 602", timeout=5.0)
            write_json_output(
                args.output, {"mode": "keep-awake", "output": ANSI_ESCAPE_RE.sub("", raw)}
            )
        elif args.mode == "normal":
            raw = sess.run_shell("power-shell setmode 600", timeout=5.0)
            write_json_output(
                args.output, {"mode": "normal", "output": ANSI_ESCAPE_RE.sub("", raw)}
            )
        elif args.mode == "snapshot":
            size_flags = "" if getattr(args, "full_res", False) else "-w 1400 -h 920 "
            sh_cmd = (
                f"snapshot_display {size_flags}-f /data/local/tmp/snap_1.jpeg >/dev/null 2>&1 "
                f"&& cat /data/local/tmp/snap_1.jpeg"
            )
            raw_bytes = sess.run_shell_bytes(sh_cmd, timeout=15.0)
            out_path = Path(args.output)
            fmt = "WEBP" if out_path.suffix.lower() == ".webp" else "JPEG"
            quality = getattr(args, "quality", 68)
            comp_bytes, img_w, img_h, raw_len = compress_image_bytes(
                raw_bytes, quality=quality, fmt=fmt
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(comp_bytes)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            print(
                f"Success! Compressed screenshot saved to: {out_path} "
                f"({img_w}x{img_h}, {len(comp_bytes)} bytes [raw {raw_len}B], {elapsed_ms}ms)"
            )
        elif args.mode == "long-snapshot":
            return _run_long_screen_cli(sess, args, t0)
    finally:
        sess.close()


def _run_long_screen_cli(sess, args, t0: float):
    out_path = Path(args.output).resolve()
    if out_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        out_dir = out_path.parent
        stem = out_path.stem
        json_out = out_dir / f"{stem}_manifest.json"
    else:
        out_dir = out_path.parent
        stem = out_path.stem
        json_out = out_path

    res = run_long_screen(
        sess,
        max_pages=getattr(args, "pages", 4),
        quality=getattr(args, "quality", 68),
        scroll_x=getattr(args, "scroll_x", 1400),
        start_y=getattr(args, "start_y", 1420),
        end_y=getattr(args, "end_y", 480),
        duration_ms=getattr(args, "duration", 450),
        keep_ms=getattr(args, "keep_ms", 220),
        scroll_engine=getattr(args, "scroll_engine", "touch-hold"),
        settle_sec=getattr(args, "settle", 0.35),
        out_dir=out_dir,
        stem=stem,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    res["elapsed_ms"] = elapsed_ms
    res["via_daemon"] = getattr(sess, "is_daemon_client", False)
    write_json_output(str(json_out), res)


def cmd_long_screen(args):
    t0 = time.perf_counter()
    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        _run_long_screen_cli(sess, args, t0)
    finally:
        sess.close()


def cmd_ui(args):
    t0 = time.perf_counter()
    action = args.action
    engine = args.engine
    if action in ("click", "double-click", "long-click"):
        sh_cmd = build_tap_shell_cmd(args.x, args.y, action=action, engine=engine)
    elif action == "swipe":
        dur = args.duration or 400
        keep_ms = getattr(args, "keep_ms", 0)
        sh_cmd = build_swipe_shell_cmd(
            args.x, args.y, args.x2, args.y2, duration_ms=dur, engine=engine, keep_ms=keep_ms
        )
    elif action == "key":
        sh_cmd = build_key_shell_cmd(args.val, engine=engine)
    elif action == "text":
        sh_cmd = build_base64_input_cmd(args.x, args.y, args.val)
    else:
        raise ValueError(f"Unsupported action: {action}")

    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    try:
        out_str, fb_data = execute_action_with_feedback(
            sess,
            sh_cmd,
            feedback_mode=args.feedback,
            wait_after=args.wait_after,
        )
    finally:
        sess.close()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    res = {
        "action": action,
        "engine": engine,
        "via_daemon": getattr(sess, "is_daemon_client", False),
        "elapsed_ms": elapsed_ms,
        "command": sh_cmd,
        "output": out_str,
    }
    if fb_data:
        res["feedback"] = fb_data
    write_json_output(args.output, res)


def cmd_deliver_file(args):
    file_path = Path(args.file).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    sess = resolve_session(
        args.host, args.port, force_scan=args.scan, use_daemon=not args.no_daemon
    )
    lan_ip = get_local_lan_ip(sess.host)
    serve_dir = str(file_path.parent)
    filename = file_path.name

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=serve_dir, **kw)

        def log_message(self, fmt, *a):
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    http_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    url = f"http://{lan_ip}:{http_port}/{filename}"
    open_with_result = None
    try:
        sh_cmd = f"aa start -A ohos.want.action.viewData -e entity.system.browsable -U '{url}'"
        raw = sess.run_shell(sh_cmd, timeout=8.0)
        time.sleep(args.wait_seconds)

        if args.open_files_app or args.open_with:
            sess.run_shell("aa start -b com.huawei.hmos.files -a EntryAbility; sleep 0.8", timeout=6.0)

        if args.open_with:
            tree = sess.dump_ui_tree()
            _, els = analyze_and_flatten_ui(tree, compact=False)
            cancel_btns = find_matching_elements(els, key_query="cancel_and_search_xmark")
            if cancel_btns:
                cx, cy = cancel_btns[0]["center"]
                tree = sess.dump_ui_tree(
                    prefix_cmd=build_tap_shell_cmd(cx, cy, "click", "uinput"), settle_sec=0.45
                )
                _, els = analyze_and_flatten_ui(tree, compact=False)

            search_inputs = find_matching_elements(els, key_query="SearchBarComp_Input")
            if search_inputs:
                sx, sy = search_inputs[0]["center"]
                in_cmd = (
                    f"{build_base64_input_cmd(sx, sy, filename)}; "
                    f"sleep 0.3; {build_key_shell_cmd(2054, 'uinput')}"
                )
                tree = sess.dump_ui_tree(prefix_cmd=in_cmd, settle_sec=0.9)
            else:
                tree = sess.dump_ui_tree()

            _, els = analyze_and_flatten_ui(tree, compact=False)
            file_rows = find_matching_elements(els, text_query=re.escape(filename))
            if not file_rows:
                raise RuntimeError(
                    f"Could not find downloaded file '{filename}' in File Manager search results"
                )
            fx, fy = file_rows[0]["center"]
            tree = sess.dump_ui_tree(
                prefix_cmd=build_tap_shell_cmd(fx, fy, "long-click", "uinput"), settle_sec=0.7
            )

            _, els = analyze_and_flatten_ui(tree, compact=False)
            open_mode_items = find_matching_elements(els, key_query="RightClickMenuComp_openMode")
            if not open_mode_items:
                open_mode_items = find_matching_elements(els, text_query="打开方式")
            if not open_mode_items:
                raise RuntimeError("Could not find '打开方式' in context menu")
            ox, oy = open_mode_items[0]["center"]
            tree = sess.dump_ui_tree(
                prefix_cmd=build_tap_shell_cmd(ox, oy, "click", "uinput"), settle_sec=0.7
            )

            _, els = analyze_and_flatten_ui(tree, compact=False)
            app_items = find_matching_elements(els, text_query=re.escape(args.open_with))
            if not app_items:
                raise RuntimeError(f"Could not find app '{args.open_with}' in Open With submenu")
            ax, ay = app_items[0]["center"]
            sess.run_shell(
                f"{build_tap_shell_cmd(ax, ay, 'click', 'uinput')}; sleep 1.0", timeout=6.0
            )
            open_with_result = {"opened_with": args.open_with, "app_menu_coords": [ax, ay]}
    finally:
        httpd.shutdown()
        sess.close()

    write_json_output(
        args.output,
        {
            "status": "delivered",
            "local_file": str(file_path),
            "served_url": url,
            "tablet_download_path": f"/storage/media/100/local/files/Docs/Download/com.huawei.hmos.browser/{filename}",
            "tablet_file_uri": f"file://docs/storage/Users/currentUser/Download/com.huawei.hmos.browser/{filename}",
            "browser_launch_output": ANSI_ESCAPE_RE.sub("", raw).strip(),
            "open_with_result": open_with_result,
        },
    )


def _add_feedback_args(parser: argparse.ArgumentParser, default_wait: float = 0.35):
    parser.add_argument(
        "-F",
        "--feedback",
        choices=["none", "diff", "summary", "full", "screen"],
        default="none",
        help="Closed-loop auto-feedback in a single on-device pipeline (diff, summary, full, screen)",
    )
    parser.add_argument(
        "-W",
        "--wait-after",
        type=float,
        default=default_wait,
        help=f"Seconds to wait on-device before capturing feedback (default {default_wait}s)",
    )
    parser.add_argument(
        "--wait-change",
        action="store_true",
        help="If UI hasn't changed after --wait-after, wait an extra 0.45s and re-check once",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hdc-claw",
        description="HDC-Claw: Pure-Python HarmonyOS NEXT / 6.0 HDC Wireless Client & AI Agent Automation Toolkit.",
    )
    p.add_argument("--host", help="Tablet IP address (defaults to last connected host)")
    p.add_argument("--port", type=int, help="HDC wireless debugging port")
    p.add_argument(
        "--scan", action="store_true", help="Force scan ports 30000-49999 if connection fails/times out"
    )
    p.add_argument(
        "--no-daemon",
        action="store_true",
        help="Bypass local keep-alive multiplexer daemon (127.0.0.1:18710) and open a fresh TCP+RSA connection",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    # daemon
    p_dm = sub.add_parser("daemon", help="Manage local keep-alive HDC multiplexer daemon (127.0.0.1:18710)")
    p_dm.add_argument(
        "--action",
        choices=["start", "stop", "status", "serve"],
        default="status",
        help="Daemon action (default: status)",
    )
    p_dm.add_argument("--output", help="Optional output JSON file path")
    p_dm.set_defaults(func=cmd_daemon)

    # connect
    p_conn = sub.add_parser(
        "connect", help="Connect to HarmonyOS device, auto-scan port if needed, and warm up daemon"
    )
    p_conn.add_argument("--output", help="Optional output JSON file path")
    p_conn.set_defaults(func=cmd_connect)

    # shell
    p_sh = sub.add_parser("shell", help="Run a shell command on device via fast CMD_UNITY_EXECUTE (1001)")
    p_sh.add_argument("--cmd", required=True, help="Shell command string to execute")
    p_sh.add_argument("--timeout", type=float, default=10.0, help="Command timeout in seconds")
    p_sh.add_argument("--pty", action="store_true", help="Use interactive PTY (CMD_SHELL_INIT) instead of fast 1001")
    p_sh.add_argument("--output", help="Optional output JSON file path")
    p_sh.set_defaults(func=cmd_shell)

    # dump-ui
    p_dump = sub.add_parser("dump-ui", help="Dump HarmonyOS UI layout tree (defaults to semantic compact mode)")
    p_dump.add_argument("--filter", help="Optional regex/substring filter for text/label/key/type/description")
    p_dump.add_argument("--raw", action="store_true", help="Disable compact filtering and return all layout containers")
    p_dump.add_argument("--include-statusbar", action="store_true", help="Include top status bar nodes (y <= 97)")
    p_dump.add_argument("--use-cache", action="store_true", help="Reuse cached UI tree if within --cache-ttl")
    p_dump.add_argument("--cache-ttl", type=float, default=2.0, help="Max age in seconds for --use-cache (default 2.0)")
    p_dump.add_argument("--output", help="Optional output JSON file path")
    p_dump.set_defaults(func=cmd_dump_ui)

    # tap
    p_tap = sub.add_parser(
        "tap",
        help="Smart locate element by --text/--key (with warm cache) or --x/--y, tap via uinput, and return closed-loop feedback (-F diff)",
    )
    p_tap.add_argument("--text", help="Substring or regex matching element text, aggregated button label, or description")
    p_tap.add_argument("--key", help="Substring or regex matching element key")
    p_tap.add_argument("--x", type=int, help="Direct X coordinate")
    p_tap.add_argument("--y", type=int, help="Direct Y coordinate")
    p_tap.add_argument("--index", type=int, default=0, help="Match index if multiple elements match (default 0)")
    p_tap.add_argument(
        "--action", choices=["click", "double-click", "long-click"], default="click", help="Tap type"
    )
    p_tap.add_argument(
        "--engine", choices=["uinput", "uitest"], default="uinput", help="Touch injection engine (default: uinput fast-path)"
    )
    p_tap.add_argument("--fresh", action="store_true", help="Force a fresh dumpLayout before locating element (ignore UI cache)")
    p_tap.add_argument("--dump-after", action="store_true", help="Alias for --feedback summary (backward compatible)")
    _add_feedback_args(p_tap, default_wait=0.35)
    p_tap.add_argument("--output", help="Optional output JSON file path")
    p_tap.set_defaults(func=cmd_tap)

    # input-text
    p_in = sub.add_parser(
        "input-text",
        help="One-shot UTF-8 Chinese/ASCII text injection with optional auto-focus, Enter submission (--submit), and closed-loop feedback (-F)",
    )
    p_in.add_argument("--text", required=True, help="UTF-8 Chinese or ASCII string to input")
    p_in.add_argument("--x", type=int, help="Input box X coordinate (auto-detected if omitted)")
    p_in.add_argument("--y", type=int, help="Input box Y coordinate (auto-detected if omitted)")
    p_in.add_argument("--key", help="Locate input box by key substring/regex")
    p_in.add_argument("--target-text", "--tap-text", dest="target_text", help="Locate input box by placeholder/text")
    p_in.add_argument("--clear-first", action="store_true", help="Click input box before typing")
    p_in.add_argument("--press-enter", action="store_true", help="Send Enter key (2054) after typing")
    p_in.add_argument("--submit", action="store_true", help="Alias for --press-enter: send Enter key (2054) after typing")
    p_in.add_argument("--hide-keyboard", action="store_true", help="Send Back key to hide keyboard after typing")
    p_in.add_argument("--engine", choices=["uinput", "uitest"], default="uinput", help="Key/touch engine (default: uinput)")
    p_in.add_argument("--fresh", action="store_true", help="Force fresh dumpLayout when locating input box")
    _add_feedback_args(p_in, default_wait=0.45)
    p_in.add_argument("--output", help="Optional output JSON file path")
    p_in.set_defaults(func=cmd_input_text)

    # batch
    p_batch = sub.add_parser(
        "batch",
        help="Execute multiple semantic/coordinate UI & shell steps in a single session (supports tap-text, wait-text, dump-diff, -F)",
    )
    p_batch.add_argument(
        "--steps",
        required=True,
        help='JSON array of steps, e.g. \'["tap-text 搜索", "wait-text 历史", "dump-diff"]\'',
    )
    p_batch.add_argument("--engine", choices=["uinput", "uitest"], default="uinput", help="Touch/key engine (default: uinput)")
    _add_feedback_args(p_batch, default_wait=0.35)
    p_batch.add_argument("--output", help="Optional output JSON file path")
    p_batch.set_defaults(func=cmd_batch)

    # launch
    p_launch = sub.add_parser("launch", help="Launch HarmonyOS app by Chinese/English name or bundle name")
    p_launch.add_argument("--app", help="App name (e.g. 日历, 文件管理, 设置, 浏览器, 备忘录, 淘宝) or bundle name")
    p_launch.add_argument("--bundle", help="Explicit bundleName")
    p_launch.add_argument("--ability", help="Explicit abilityName (auto-resolved if omitted)")
    p_launch.add_argument("--uri", help="Optional URI to pass (-U)")
    p_launch.add_argument("--list-apps", action="store_true", help="List all installed bundle names")
    _add_feedback_args(p_launch, default_wait=0.8)
    p_launch.add_argument("--output", help="Optional output JSON file path")
    p_launch.set_defaults(func=cmd_launch)

    # push
    p_push = sub.add_parser(
        "push", help="Push local binary/text file to device (e.g. /data/local/tmp/...) with SHA256 check"
    )
    p_push.add_argument("--local", required=True, help="Local file path")
    p_push.add_argument("--remote", required=True, help="Remote path (e.g. /data/local/tmp/file.bin)")
    p_push.add_argument("--output", help="Optional output JSON file path")
    p_push.set_defaults(func=cmd_push)

    # pull
    p_pull = sub.add_parser("pull", help="Pull remote file from device to local path via binary-clean stream")
    p_pull.add_argument("--remote", required=True, help="Remote file path")
    p_pull.add_argument("--local", required=True, help="Local destination file path")
    p_pull.set_defaults(func=cmd_pull)

    # install
    p_inst = sub.add_parser("install", help="Push and install a local .hap package onto the HarmonyOS device")
    p_inst.add_argument("--hap", required=True, help="Local .hap file path")
    p_inst.add_argument("--output", help="Optional output JSON file path")
    p_inst.set_defaults(func=cmd_install)

    # screen
    p_scr = sub.add_parser(
        "screen",
        help="Control screen wake state or capture high-compression low-loss snapshot / long-snapshot",
    )
    p_scr.add_argument(
        "--mode",
        choices=["keep-awake", "normal", "snapshot", "long-snapshot"],
        required=True,
        help="keep-awake (mode 602), normal (mode 600), snapshot (compressed JPEG), or long-snapshot (zero-inertia scroll + native viewport slices)",
    )
    p_scr.add_argument("--quality", type=int, default=68, help="JPEG compression quality (default 68)")
    p_scr.add_argument("--full-res", action="store_true", help="Capture full 2800x1840 resolution instead of AI-optimized 1400x920")
    p_scr.add_argument("--pages", type=int, default=4, help="Max pages to scroll for long-snapshot (default 4)")
    p_scr.add_argument("--scroll-x", type=int, default=1400, help="X coordinate for vertical scroll (default 1400)")
    p_scr.add_argument("--start-y", type=int, default=1420, help="Scroll start Y coordinate (default 1420)")
    p_scr.add_argument("--end-y", type=int, default=480, help="Scroll end Y coordinate (default 480)")
    p_scr.add_argument("--duration", type=int, default=450, help="Scroll drag duration in ms (default 450)")
    p_scr.add_argument("--keep-ms", type=int, default=220, help="Hold stationary time at end of drag in ms to kill inertial fling (default 220)")
    p_scr.add_argument(
        "--scroll-engine",
        choices=["touch-hold", "wheel"],
        default="touch-hold",
        help="Scroll engine: touch-hold (uinput -T -m -k dead-stop) or wheel (uinput -M -s mouse wheel)",
    )
    p_scr.add_argument("--settle", type=float, default=0.35, help="Settle wait in seconds after each scroll (default 0.35)")
    p_scr.add_argument("--output", required=True, help="Output JSON or image file path")
    p_scr.set_defaults(func=cmd_screen)

    # long-screen (dedicated alias for scrolling long screenshot + slice generation)
    p_long = sub.add_parser(
        "long-screen",
        help="Zero-inertia auto-scroll (-k 220ms), capture native viewport slices, and stitch overview (Taobao waterfall & video resilient)",
    )
    p_long.add_argument("--pages", type=int, default=4, help="Max scroll pages to capture (default 4, stops early at bottom)")
    p_long.add_argument("--quality", type=int, default=68, help="JPEG compression quality (default 68)")
    p_long.add_argument("--scroll-x", type=int, default=1400, help="X coordinate for vertical scroll (default 1400)")
    p_long.add_argument("--start-y", type=int, default=1420, help="Scroll start Y coordinate (default 1420)")
    p_long.add_argument("--end-y", type=int, default=480, help="Scroll end Y coordinate (default 480)")
    p_long.add_argument("--duration", type=int, default=450, help="Scroll drag duration in ms (default 450)")
    p_long.add_argument("--keep-ms", type=int, default=220, help="Hold stationary time at end of drag in ms to kill inertial fling (default 220)")
    p_long.add_argument(
        "--scroll-engine",
        choices=["touch-hold", "wheel"],
        default="touch-hold",
        help="Scroll engine: touch-hold (uinput -T -m -k dead-stop) or wheel (uinput -M -s mouse wheel)",
    )
    p_long.add_argument("--settle", type=float, default=0.35, help="Settle wait in seconds after each scroll (default 0.35)")
    p_long.add_argument("--output", required=True, help="Output manifest JSON path (slices saved alongside)")
    p_long.set_defaults(func=cmd_long_screen)

    # ui
    p_ui = sub.add_parser("ui", help="Execute low-level touch or key action (defaults to fast uinput engine)")
    p_ui.add_argument(
        "--action",
        choices=["click", "double-click", "long-click", "swipe", "key", "text"],
        required=True,
    )
    p_ui.add_argument("--x", type=int, help="X coordinate")
    p_ui.add_argument("--y", type=int, help="Y coordinate")
    p_ui.add_argument("--x2", type=int, help="End X coordinate (for swipe)")
    p_ui.add_argument("--y2", type=int, help="End Y coordinate (for swipe)")
    p_ui.add_argument("--duration", type=int, default=400, help="Swipe duration in ms (default 400)")
    p_ui.add_argument("--keep-ms", type=int, default=0, help="Hold stationary ms at end of swipe to prevent inertial fling (default 0, use 220 for dead-stop)")
    p_ui.add_argument("--val", help="Key code/name (Back, Home, 2054) or UTF-8 text")
    p_ui.add_argument("--engine", choices=["uinput", "uitest"], default="uinput", help="Engine (default: uinput)")
    _add_feedback_args(p_ui, default_wait=0.35)
    p_ui.add_argument("--output", help="Optional output JSON file path")
    p_ui.set_defaults(func=cmd_ui)

    # deliver-file
    p_deliv = sub.add_parser(
        "deliver-file",
        help="Deliver a local file into HarmonyOS public Download folder and optionally open with target app",
    )
    p_deliv.add_argument("--file", required=True, help="Local file path to deliver")
    p_deliv.add_argument("--wait-seconds", type=float, default=4.0, help="Seconds to wait for browser download")
    p_deliv.add_argument(
        "--open-files-app",
        action="store_true",
        help="Launch com.huawei.hmos.files after downloading",
    )
    p_deliv.add_argument(
        "--open-with",
        help="Automatically search in File Manager, open '打开方式', and hand off URI to target app (e.g. '日历')",
    )
    p_deliv.add_argument("--output", help="Optional output JSON file path")
    p_deliv.set_defaults(func=cmd_deliver_file)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
