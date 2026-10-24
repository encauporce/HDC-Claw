"""
UI Layout Analysis, Semantic Flattening, Warm Cache & Closed-Loop Feedback Engine.

Features:
- Fast uinput kernel touch injection (click, double-click, long-click, drag, key).
- Smart warm UI cache (~/.gemini/hdc_last_ui_cache.json) for <5ms coordinate resolution.
- Single-command closed-loop feedback (-F diff, -F summary, -F full, -F screen).
- Semantic element flattening, label aggregation, and dialog/keyboard scene detectors.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import time
from pathlib import Path
from typing import Any

from .protocol import ANSI_ESCAPE_RE, HdcSession

UI_CACHE_PATH = Path.home() / ".gemini" / "hdc_last_ui_cache.json"
BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

KEYCODE_MAP = {
    "home": 1,
    "back": 2,
    "call": 3,
    "endcall": 4,
    "clear": 5,
    "volumeup": 16,
    "volumedown": 17,
    "power": 18,
    "camera": 19,
    "tab": 2049,
    "space": 2050,
    "enter": 2054,
    "backspace": 2055,
    "delete": 2071,
    "escape": 2070,
    "ctrl": 2072,
}

KNOWN_APPS = {
    "日历": ("com.huawei.hmos.calendar", "MainAbility"),
    "calendar": ("com.huawei.hmos.calendar", "MainAbility"),
    "文件管理": ("com.huawei.hmos.files", "EntryAbility"),
    "文件": ("com.huawei.hmos.files", "EntryAbility"),
    "files": ("com.huawei.hmos.files", "EntryAbility"),
    "浏览器": ("com.huawei.hmos.browser", "MainAbility"),
    "browser": ("com.huawei.hmos.browser", "MainAbility"),
    "设置": ("com.huawei.hmos.settings", "com.huawei.hmos.settings.MainAbility"),
    "settings": ("com.huawei.hmos.settings", "com.huawei.hmos.settings.MainAbility"),
    "备忘录": ("com.huawei.hmos.notepad", "MainAbility"),
    "笔记": ("com.huawei.hmos.notepad", "MainAbility"),
    "notepad": ("com.huawei.hmos.notepad", "MainAbility"),
    "图库": ("com.huawei.hmos.photos", "com.huawei.hmos.photos.MainAbility"),
    "相册": ("com.huawei.hmos.photos", "com.huawei.hmos.photos.MainAbility"),
    "photos": ("com.huawei.hmos.photos", "com.huawei.hmos.photos.MainAbility"),
    "计算器": ("com.huawei.hmos.calculator", "com.huawei.hmos.calculator.TwCalculatorAbility"),
    "calculator": ("com.huawei.hmos.calculator", "com.huawei.hmos.calculator.TwCalculatorAbility"),
    "时钟": ("com.huawei.hmos.clock", "com.huawei.hmos.clock.phone"),
    "clock": ("com.huawei.hmos.clock", "com.huawei.hmos.clock.phone"),
    "天气": ("com.huawei.hmos.weather", "MainAbility"),
    "weather": ("com.huawei.hmos.weather", "MainAbility"),
    "电子邮件": ("com.huawei.hmos.email", "MainAbility"),
    "邮箱": ("com.huawei.hmos.email", "MainAbility"),
    "email": ("com.huawei.hmos.email", "MainAbility"),
    "淘宝": ("com.taobao.taobao", "MainAbility"),
    "taobao": ("com.taobao.taobao", "MainAbility"),
}


def load_ui_cache() -> dict | None:
    if UI_CACHE_PATH.exists():
        try:
            return json.loads(UI_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_ui_cache(host: str, port: int, tree: dict, scene: dict, elements: list):
    try:
        UI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UI_CACHE_PATH.write_text(
            json.dumps(
                {
                    "timestamp": time.time(),
                    "valid": True,
                    "host": host,
                    "port": port,
                    "scene": scene,
                    "elements": elements,
                    "tree": tree,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def invalidate_ui_cache():
    cache = load_ui_cache()
    if cache and cache.get("valid"):
        cache["valid"] = False
        try:
            UI_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def parse_bounds(bounds_str: str) -> dict | None:
    m = BOUNDS_RE.search(bounds_str or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": x2 - x1,
        "height": y2 - y1,
        "center": [(x1 + x2) // 2, (y1 + y2) // 2],
    }


def collect_child_texts(node: dict, max_depth: int = 3) -> list[str]:
    if max_depth <= 0:
        return []
    texts = []
    for c in node.get("children", []):
        ca = c.get("attributes", {})
        ct = (ca.get("text") or "").strip()
        if ct and ct not in texts:
            texts.append(ct)
        for sub in collect_child_texts(c, max_depth - 1):
            if sub not in texts:
                texts.append(sub)
    return texts


def analyze_and_flatten_ui(
    tree: dict,
    filter_re: re.Pattern | None = None,
    compact: bool = True,
    include_statusbar: bool = False,
) -> tuple[dict, list]:
    scene = {
        "has_system_dialog": False,
        "has_sheet": False,
        "keyboard_visible": False,
        "window_keys": [],
    }
    elements = []

    def walk(node: dict):
        attrs = node.get("attributes", {})
        t = attrs.get("type", "")
        text = (attrs.get("text") or "").strip()
        key = (attrs.get("key") or "").strip()
        desc = (attrs.get("description") or "").strip()
        bounds_str = attrs.get("bounds", "")
        clickable = attrs.get("clickable", "") == "true"
        checked = attrs.get("checked", "") == "true"
        selected = attrs.get("selected", "") == "true"
        b_info = parse_bounds(bounds_str)

        if key == "SHOW_DIALOG_NAVIGATION_CUSTOM_ID" or "Dialog" in key:
            scene["has_system_dialog"] = True
        if t in ("SheetWrapper", "SheetPage"):
            scene["has_sheet"] = True
        if "keyboard" in key.lower() or key == "KeyboardCanvas":
            scene["keyboard_visible"] = True
        if t == "WindowScene" and key:
            scene["window_keys"].append(key)

        if not include_statusbar and b_info and b_info["y2"] <= 97 and b_info["y1"] >= 0:
            return

        label = text
        if not label and clickable and t in ("Button", "MenuItem", "Row", "ListItem", "Column", "Stack"):
            child_texts = collect_child_texts(node, max_depth=2)
            if child_texts:
                label = " | ".join(child_texts[:3])

        clean_desc = "" if desc in ("", " ", "单指双击即可执行") else desc

        keep = False
        if not compact:
            keep = bool(text or key or desc or clickable)
        else:
            is_layout_container = t in (
                "Row",
                "Column",
                "Stack",
                "Flex",
                "RelativeContainer",
                "__Common__",
                "List",
                "Swiper",
                "Tabs",
                "Scroll",
                "Grid",
                "WindowScene",
                "Navigation",
                "NavDestination",
            )
            if text or clean_desc:
                keep = True
            elif clickable and (
                label
                or key
                or t in ("Button", "MenuItem", "TextInput", "TextArea", "Checkbox", "Toggle", "Radio")
            ):
                keep = True
            elif key and not is_layout_container:
                keep = True
            elif key and any(
                w in key.lower()
                for w in (
                    "button",
                    "input",
                    "search",
                    "menu",
                    "tab",
                    "add",
                    "delete",
                    "edit",
                    "back",
                    "today",
                    "agenda_block",
                    "open",
                    "confirm",
                    "cancel",
                    "select",
                )
            ):
                keep = True

        if keep:
            searchable = f"{t} {key} {text} {label} {clean_desc}"
            if filter_re is None or filter_re.search(searchable):
                item = {
                    "id": len(elements) + 1,
                    "type": t,
                    "key": key,
                    "text": text,
                    "clickable": clickable,
                    "bounds": bounds_str,
                    "center": b_info["center"] if b_info else None,
                }
                if label and label != text:
                    item["label"] = label
                if clean_desc:
                    item["description"] = clean_desc
                if checked:
                    item["checked"] = True
                if selected:
                    item["selected"] = True
                elements.append(item)

        for child in node.get("children", []):
            walk(child)

    walk(tree)
    return scene, elements


def find_matching_elements(
    elements: list[dict], text_query: str | None = None, key_query: str | None = None
) -> list[dict]:
    text_re = re.compile(text_query, re.I) if text_query else None
    key_re = re.compile(key_query, re.I) if key_query else None

    matches = []
    for el in elements:
        if not el.get("center"):
            continue
        if text_re:
            t_cand = f"{el.get('text', '')} {el.get('label', '')} {el.get('description', '')}"
            if not text_re.search(t_cand):
                continue
        if key_re:
            if not key_re.search(el.get("key", "")):
                continue
        matches.append(el)

    if text_query:
        def score(el):
            exact = (el.get("text") == text_query) or (el.get("label") == text_query)
            is_btn = el.get("type") in ("Button", "MenuItem", "TextInput", "TextArea")
            return (1 if exact else 0, 1 if el.get("clickable") else 0, 1 if is_btn else 0)
        matches.sort(key=score, reverse=True)
    return matches


def resolve_element_from_cache_or_dump(
    sess: Any,
    text_query: str | None = None,
    key_query: str | None = None,
    elem_types: tuple[str, ...] | None = None,
    index: int = 0,
    allow_cache: bool = True,
    max_cache_age: float = 120.0,
) -> tuple[dict, list[dict], dict, bool]:
    if allow_cache:
        cache = load_ui_cache()
        if (
            cache
            and cache.get("valid")
            and cache.get("host") == sess.host
            and (time.time() - cache.get("timestamp", 0)) <= max_cache_age
            and cache.get("tree")
        ):
            _, all_els = analyze_and_flatten_ui(cache["tree"], compact=False, include_statusbar=True)
            if text_query or key_query:
                matches = find_matching_elements(all_els, text_query=text_query, key_query=key_query)
            elif elem_types:
                matches = [e for e in all_els if e.get("type") in elem_types and e.get("center")]
            else:
                matches = []
            if matches:
                idx = index if index < len(matches) else 0
                sc, comp_els = analyze_and_flatten_ui(cache["tree"], compact=True)
                return matches[idx], comp_els, sc, True

    # Cache miss or stale: perform fresh dump
    tree = sess.dump_ui_tree()
    sc, comp_els = analyze_and_flatten_ui(tree, compact=True)
    save_ui_cache(sess.host, sess.port, tree, sc, comp_els)
    _, all_els = analyze_and_flatten_ui(tree, compact=False, include_statusbar=True)
    if text_query or key_query:
        matches = find_matching_elements(all_els, text_query=text_query, key_query=key_query)
    elif elem_types:
        matches = [e for e in all_els if e.get("type") in elem_types and e.get("center")]
    else:
        matches = []
    if not matches:
        raise RuntimeError(
            f"No UI element matched text={text_query!r} key={key_query!r} types={elem_types!r}. "
            f"Run dump-ui to inspect current screen."
        )
    idx = index if index < len(matches) else 0
    return matches[idx], comp_els, sc, False


def build_tap_shell_cmd(cx: int, cy: int, action: str = "click", engine: str = "uinput") -> str:
    if engine == "uinput":
        if action == "click":
            return f"uinput -T -c {cx} {cy} 10"
        elif action == "double-click":
            return f"uinput -T -d {cx} {cy} -i 15 -u {cx} {cy} -i 50 -d {cx} {cy} -i 15 -u {cx} {cy}"
        elif action == "long-click":
            return f"uinput -T -d {cx} {cy} -i 600 -u {cx} {cy}"
    ui_verb = {
        "click": "click",
        "double-click": "doubleClick",
        "long-click": "longClick",
    }[action]
    return f"uitest uiInput {ui_verb} {cx} {cy}"


def build_swipe_shell_cmd(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 400,
    keep_ms: int = 0,
    engine: str = "uinput",
) -> str:
    if engine == "uinput":
        if keep_ms > 0:
            return f"uinput -T -m {x1} {y1} {x2} {y2} -k {keep_ms} {duration_ms}"
        return f"uinput -T -m {x1} {y1} {x2} {y2} {duration_ms}"
    return f"uitest uiInput swipe {x1} {y1} {x2} {y2} {duration_ms}"


def build_scroll_shell_cmd(
    scroll_x: int = 1400,
    start_y: int = 1420,
    end_y: int = 520,
    duration_ms: int = 450,
    keep_ms: int = 220,
    scroll_engine: str = "touch-hold",
) -> str:
    """
    Zero-inertia scroll command generator.
    - 'touch-hold': uses `uinput -T -m x1 y1 x2 y2 -k 220 450` holding touch stationary for 220ms
      before release, completely killing inertial fling in Taobao and waterfall lists.
    - 'wheel': uses `uinput -M -m x y -s -8` mouse wheel discrete scroll.
    """
    if scroll_engine == "wheel":
        mid_y = (start_y + end_y) // 2
        return f"uinput -M -m {scroll_x} {mid_y} -s -8"
    return build_swipe_shell_cmd(
        scroll_x, start_y, scroll_x, end_y, duration_ms=duration_ms, keep_ms=keep_ms, engine="uinput"
    )


def build_key_shell_cmd(key_val: str | int, engine: str = "uinput") -> str:
    s_val = str(key_val).strip()
    code = None
    if s_val.isdigit():
        code = int(s_val)
    elif s_val.lower() in KEYCODE_MAP:
        code = KEYCODE_MAP[s_val.lower()]
    if engine == "uinput" and code is not None:
        return f"uinput -K -d {code} -u {code}"
    return f"uitest uiInput keyEvent {s_val}"


def build_base64_input_cmd(x: int, y: int, text: str) -> str:
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f'uitest uiInput inputText {x} {y} "$(echo {b64} | base64 -d)"'


def compute_ui_diff(
    before_scene: dict | None,
    before_elements: list[dict] | None,
    after_scene: dict,
    after_elements: list[dict],
) -> dict:
    def sig(el: dict) -> tuple:
        return (
            el.get("type", ""),
            el.get("key", ""),
            el.get("text", ""),
            el.get("label", ""),
            el.get("description", ""),
        )

    before_list = before_elements or []
    before_sigs = {sig(e): e for e in before_list}
    after_sigs = {sig(e): e for e in after_elements}

    added = [e for s, e in after_sigs.items() if s not in before_sigs]
    removed = [e for s, e in before_sigs.items() if s not in after_sigs]

    changed = []
    for s, after_el in after_sigs.items():
        if s in before_sigs:
            before_el = before_sigs[s]
            if before_el.get("checked") != after_el.get("checked") or before_el.get("selected") != after_el.get("selected"):
                changed.append({"before": before_el, "after": after_el})

    removed_texts = []
    for e in removed:
        lbl = e.get("text") or e.get("label") or e.get("description") or e.get("key")
        if lbl and lbl not in removed_texts:
            removed_texts.append(lbl)

    interactive = [
        {
            "type": e["type"],
            "text": e.get("text") or e.get("label") or e.get("description") or "",
            "key": e.get("key", ""),
            "center": e.get("center"),
        }
        for e in after_elements
        if (e.get("clickable") or e.get("type") in ("Button", "MenuItem", "TextInput", "TextArea"))
        and (e.get("text") or e.get("label") or e.get("description") or e.get("key"))
    ]

    scene_changed = (before_scene != after_scene) or bool(added) or bool(removed) or bool(changed)
    return {
        "mode": "diff",
        "scene_changed": scene_changed,
        "after_scene": after_scene,
        "after_element_count": len(after_elements),
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "added_elements": added[:35],
        "removed_texts": removed_texts[:25],
        "changed_elements": changed[:15],
        "interactive_targets": interactive[:30],
    }


def execute_action_with_feedback(
    sess: Any,
    action_shell_cmd: str,
    feedback_mode: str = "none",
    wait_after: float = 0.35,
    before_scene: dict | None = None,
    before_elements: list[dict] | None = None,
    wait_change: bool = False,
) -> tuple[str, dict | None]:
    if not feedback_mode or feedback_mode == "none":
        invalidate_ui_cache()
        out = sess.run_shell(action_shell_cmd, timeout=10.0)
        return ANSI_ESCAPE_RE.sub("", out).strip(), None

    if before_elements is None:
        cache = load_ui_cache()
        if cache and cache.get("elements"):
            before_scene = cache.get("scene")
            before_elements = cache.get("elements")

    if feedback_mode in ("diff", "summary", "full"):
        tree = sess.dump_ui_tree(prefix_cmd=action_shell_cmd, settle_sec=wait_after)
        after_scene, after_elements = analyze_and_flatten_ui(tree, compact=True)

        if wait_change and before_elements:
            diff_check = compute_ui_diff(before_scene, before_elements, after_scene, after_elements)
            if not diff_check["scene_changed"]:
                tree = sess.dump_ui_tree(prefix_cmd=None, settle_sec=0.45)
                after_scene, after_elements = analyze_and_flatten_ui(tree, compact=True)

        save_ui_cache(sess.host, sess.port, tree, after_scene, after_elements)

        if feedback_mode == "diff":
            fb = compute_ui_diff(before_scene, before_elements, after_scene, after_elements)
        elif feedback_mode == "summary":
            fb = {
                "mode": "summary",
                "after_scene": after_scene,
                "after_element_count": len(after_elements),
                "after_elements": after_elements[:50],
            }
        else:
            fb = {
                "mode": "full",
                "after_scene": after_scene,
                "after_element_count": len(after_elements),
                "after_elements": after_elements,
            }
        return "", fb

    if feedback_mode == "screen":
        from .vision import compress_image_bytes
        invalidate_ui_cache()
        snap_path = Path.home() / ".gemini" / "hdc_last_feedback.jpeg"
        sh_cmd = (
            f"({action_shell_cmd}) >/dev/null 2>&1; "
            f"sleep {wait_after:.2f}; "
            f"snapshot_display -w 1400 -h 920 -f /data/local/tmp/hdc_snap.jpeg >/dev/null 2>&1 "
            f"&& cat /data/local/tmp/hdc_snap.jpeg"
        )
        raw_bytes = sess.run_shell_bytes(sh_cmd, timeout=15.0)
        comp_bytes, img_w, img_h, raw_len = compress_image_bytes(
            raw_bytes, quality=68, fmt="JPEG"
        )
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_path.write_bytes(comp_bytes)
        return "", {
            "mode": "screen",
            "screenshot_path": str(snap_path),
            "image_resolution": [img_w, img_h],
            "native_resolution": [2800, 1840],
            "coord_scale": 2.0,
            "raw_bytes": raw_len,
            "compressed_bytes": len(comp_bytes),
        }

    raise ValueError(f"Unsupported feedback mode: {feedback_mode}")


def get_local_lan_ip(target_host: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target_host, 80))
        return s.getsockname()[0]
