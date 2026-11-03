"""
Local Keep-Alive Multiplexer Daemon (127.0.0.1:18710) for HDC-Claw.

Maintains an authenticated background persistent TCP+RSA session so subsequent
CLI invocations avoid the ~450ms handshake latency, achieving <15ms execution.
On Windows, uses WMI (Win32_Process.Create) to spawn detached outside parent Job Objects.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import CLIENT_META, DAEMON_LOG_PATH, fmt_ms as _fmt_ms
from .protocol import (
    ANSI_ESCAPE_RE,
    HdcSession,
    load_saved_target,
    recv_exact,
    scan_hdc_ports,
)

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 18710
DAEMON_IDLE_TIMEOUT = 1200.0  # 20 minutes idle timeout


def _send_ipc_msg(sock: socket.socket, obj: dict):
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)


def _recv_ipc_msg(sock: socket.socket) -> dict:
    head = recv_exact(sock, 4)
    (length,) = struct.unpack(">I", head)
    body = recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


class DaemonClientSession:
    """
    Drop-in replacement for HdcSession that forwards calls to the local
    keep-alive daemon on 127.0.0.1:18710 in <2ms, avoiding TCP + RSA-3072 handshake.
    """
    is_daemon_client = True

    def __init__(self, host: str, port: int, ipc_sock: socket.socket):
        self.host = host
        self.port = port
        self._ipc_sock = ipc_sock

    def _call(self, req: dict, timeout: float = 20.0) -> dict:
        req = dict(req)
        if CLIENT_META:
            req["client"] = CLIENT_META
        self._ipc_sock.settimeout(timeout + 5.0)
        _send_ipc_msg(self._ipc_sock, req)
        resp = _recv_ipc_msg(self._ipc_sock)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "Unknown daemon error"))
        return resp

    def run_shell_bytes(self, command: str, timeout: float = 15.0, use_pty: bool = False) -> bytes:
        resp = self._call(
            {
                "op": "shell_bytes",
                "host": self.host,
                "port": self.port,
                "command": command,
                "timeout": timeout,
                "use_pty": use_pty,
            },
            timeout=timeout,
        )
        return base64.b64decode(resp["data_b64"])

    def run_shell(self, command: str, timeout: float = 15.0, use_pty: bool = False) -> str:
        return self.run_shell_bytes(command, timeout=timeout, use_pty=use_pty).decode(
            "utf-8", errors="replace"
        )

    def dump_ui_tree(
        self,
        prefix_cmd: str | None = None,
        settle_sec: float = 0.0,
        keep_temp: bool = False,
    ) -> dict:
        resp = self._call(
            {
                "op": "dump_ui",
                "host": self.host,
                "port": self.port,
                "prefix_cmd": prefix_cmd,
                "settle_sec": settle_sec,
                "keep_temp": keep_temp,
            },
            timeout=20.0,
        )
        return resp["tree"]

    def close(self):
        if self._ipc_sock:
            try:
                self._ipc_sock.close()
            except Exception:
                pass
            self._ipc_sock = None


def _count_tree_nodes(node: dict) -> int:
    count = 1
    for child in node.get("children") or []:
        if isinstance(child, dict):
            count += _count_tree_nodes(child)
    return count


class LocalHdcDaemonServer:
    def __init__(self, initial_host: str, initial_port: int, verbose: bool = False):
        self.host = initial_host
        self.port = initial_port
        self.session: HdcSession | None = None
        self.lock = threading.Lock()
        self.last_active = time.time()
        self.running = True
        self.started_at = time.time()
        self.commands_served = 0
        self.verbose = verbose
        try:
            self.slow_ms = float(os.environ.get("HDC_DAEMON_SLOW_MS", "2500"))
        except Exception:
            self.slow_ms = 2500.0
        self.cmd_index = 0
        self.durations: list[float] = []
        self.gaps: list[float] = []
        self.last_cmd_end: float | None = None
        self.lines_since_header = 0
        self.ops_by_client: dict = {}
        self.ops_lock = threading.Lock()
        self.stats_lock = threading.RLock()
        self.log_lock = threading.RLock()
        self._console_ok = False
        self._setup_console()
        self.log(
            f"HDC daemon {DAEMON_HOST}:{DAEMON_PORT} -> {self.host}:{self.port}  "
            f"pid {os.getpid()}  verbose={self.verbose}"
        )
        self.log(f"log: {DAEMON_LOG_PATH}")
        self.emit_header()

    # -- console / log -----------------------------------------------------
    def _setup_console(self):
        try:
            if sys.stdout:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        try:
            self._console_ok = bool(sys.stdout and sys.stdout.isatty())
        except Exception:
            self._console_ok = False
        if os.name != "nt":
            return
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleTitleW(f"HDC daemon {self.host}:{self.port}")
            h = k32.GetStdHandle(-11)
            mode = ctypes.c_uint()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                self._console_ok = True
        except Exception:
            pass

    def _set_console_title(self):
        if os.name != "nt":
            return
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(f"HDC daemon {self.host}:{self.port}")
        except Exception:
            pass

    def _rotate_log(self):
        try:
            if DAEMON_LOG_PATH.exists() and DAEMON_LOG_PATH.stat().st_size > 2_000_000:
                backup = DAEMON_LOG_PATH.with_name("hdc_daemon.log.1")
                if backup.exists():
                    backup.unlink()
                DAEMON_LOG_PATH.replace(backup)
        except Exception:
            pass

    def log(self, text: str = ""):
        with self.log_lock:
            try:
                print(text, flush=True)
            except Exception:
                pass
            try:
                self._rotate_log()
                with open(DAEMON_LOG_PATH, "a", encoding="utf-8") as fh:
                    fh.write(ANSI_ESCAPE_RE.sub("", text) + "\n")
            except Exception:
                pass

    def _ts(self, started_at: float | None = None) -> str:
        try:
            return datetime.fromtimestamp(started_at or time.time()).strftime("%H:%M:%S.%f")[:-3]
        except Exception:
            return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _color(self, text: str, code: str) -> str:
        if not self._console_ok:
            return text
        return f"\033[{code}m{text}\033[0m"

    def emit_header(self):
        self.log(f"{'TIME':<12}  {'ID':<4} {'DUR':>7}  {'GAP':>8}  CMD")
        with self.stats_lock:
            self.lines_since_header = 0

    def emit_event(self, label: str, detail: str = ""):
        text = f"{label} {detail}".strip()
        self.log(f"{self._ts():<12}  {'--':<4} {'':>7}  {'':>8}  {text}")

    def emit_command(
        self,
        client: dict,
        argv: str,
        started_at: float,
        elapsed_ms: float,
        ok: bool = True,
        error: str = "",
        ops: list | None = None,
    ):
        ops = ops or []
        with self.stats_lock:
            self.cmd_index += 1
            idx = self.cmd_index
            gap = None
            if self.last_cmd_end is not None:
                gap = max(0.0, (started_at - self.last_cmd_end) * 1000.0)
            self.last_cmd_end = started_at + elapsed_ms / 1000.0
            self.durations.append(elapsed_ms)
            if gap is not None:
                self.gaps.append(gap)
            self.lines_since_header += 1
            if self.lines_since_header > 20:
                self.emit_header()
            cmd = " ".join((argv or "").split())
            if len(cmd) > 110:
                cmd = cmd[:107] + "..."
            id_str = f"#{idx:03d}"
            dur_str = f"{_fmt_ms(elapsed_ms):>7}"
            gap_str = "—" if gap is None else _fmt_ms(gap)
            if elapsed_ms >= self.slow_ms:
                dur_str = self._color(dur_str, "33")
            mark = ""
            if not ok:
                mark = "  " + self._color("ERR", "31")
            elif elapsed_ms >= self.slow_ms:
                mark = "  " + self._color("SLOW", "33")
            self.log(f"{self._ts():<12}  {id_str:<4} {dur_str}  {gap_str:>8}  {cmd}{mark}")
            indent = " " * 38
            if not ok and error:
                self.log(indent + self._color(f"└ {error[:160]}", "31"))
            if ops and (self.verbose or len(ops) > 1):
                if self.verbose:
                    for i, op in enumerate(ops[:10]):
                        branch = "└" if i == len(ops[:10]) - 1 else "├"
                        detail = f"  {op.get('detail', '')}" if op.get("detail") else ""
                        size = f" {op.get('size', 0) // 1024}KB" if op.get("size") else ""
                        self.log(
                            indent
                            + f"{branch} {op.get('op', '?')} {_fmt_ms(float(op.get('ms', 0)))}{size}{detail}"
                        )
                else:
                    summary = " · ".join(
                        f"{op.get('op', '?')} {_fmt_ms(float(op.get('ms', 0)))}" for op in ops[:6]
                    )
                    self.log(indent + f"└ ops {len(ops)}: {summary}")

    def record_op(self, client: dict, op_name: str, ms: float, size: int = 0, detail: str = ""):
        cid = (client or {}).get("id") or "unknown"
        now = time.time()
        with self.ops_lock:
            entry = self.ops_by_client.setdefault(cid, {"ts": now, "ops": []})
            entry["ts"] = now
            entry["ops"].append({"op": op_name, "ms": ms, "size": size, "detail": detail[:80]})
            if len(entry["ops"]) > 40:
                entry["ops"] = entry["ops"][-40:]
            if len(self.ops_by_client) > 50:
                for key, val in list(self.ops_by_client.items()):
                    if now - val.get("ts", now) > 300:
                        self.ops_by_client.pop(key, None)

    def pop_ops(self, client: dict) -> list:
        cid = (client or {}).get("id") or "unknown"
        with self.ops_lock:
            entry = self.ops_by_client.pop(cid, None)
            return list(entry.get("ops", [])) if entry else []

    def session_summary(self, reason: str = "idle"):
        with self.stats_lock:
            n = len(self.durations)
            total = sum(self.durations)
            mx = max(self.durations) if self.durations else 0
            avg = total / n if n else 0
            avg_gap = sum(self.gaps) / len(self.gaps) if self.gaps else 0
        if n:
            self.emit_event(
                "session end",
                f"{n} cmds · avg {_fmt_ms(avg)} · max {_fmt_ms(mx)} · "
                f"avg gap {_fmt_ms(avg_gap)} · {reason}",
            )
        else:
            self.emit_event("session end", f"no commands · {reason}")

    def get_or_connect_session(
        self, req_host: str | None, req_port: int | None, label: str = "connect"
    ) -> HdcSession:
        target_host = req_host or self.host
        target_port = req_port or self.port
        if (
            self.session is not None
            and self.session.sock is not None
            and self.session.host == target_host
            and self.session.port == target_port
        ):
            return self.session

        if self.session is not None:
            self.session.close()
        t0 = time.perf_counter()
        sess = HdcSession(target_host, target_port)
        sess.connect()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.session = sess
        self.host = target_host
        self.port = target_port
        self._set_console_title()
        self.emit_event(label, f"{target_host}:{target_port} {_fmt_ms(elapsed_ms)}")
        return sess

    def execute_with_reconnect(self, req_host: str | None, req_port: int | None, fn):
        with self.lock:
            self.last_active = time.time()
            self.commands_served += 1
            try:
                sess = self.get_or_connect_session(req_host, req_port)
                return fn(sess)
            except (ConnectionError, OSError, BrokenPipeError, RuntimeError):
                if self.session:
                    self.session.close()
                    self.session = None
                sess = self.get_or_connect_session(req_host, req_port, label="reconnect")
                return fn(sess)

    def handle_client(self, conn: socket.socket):
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while True:
                req = _recv_ipc_msg(conn)
                op = req.get("op")
                if op == "ping":
                    _send_ipc_msg(
                        conn,
                        {
                            "ok": True,
                            "host": self.host,
                            "port": self.port,
                            "connected": bool(self.session and self.session.sock),
                            "uptime_sec": round(time.time() - self.started_at, 1),
                            "commands_served": self.commands_served,
                            "log_path": str(DAEMON_LOG_PATH),
                            "verbose": self.verbose,
                        },
                    )
                elif op == "stop":
                    self.emit_event("stop", "requested")
                    _send_ipc_msg(conn, {"ok": True, "status": "stopping"})
                    self.running = False
                    break
                elif op == "log":
                    try:
                        self.emit_command(
                            req.get("client") or {},
                            req.get("argv") or "",
                            float(req.get("started_at") or time.time()),
                            float(req.get("elapsed_ms") or 0.0),
                            bool(req.get("ok", True)),
                            req.get("error") or "",
                            self.pop_ops(req.get("client") or {}),
                        )
                    except Exception:
                        pass
                    _send_ipc_msg(conn, {"ok": True})
                elif op == "shell_bytes":
                    cmd = req["command"]
                    timeout = float(req.get("timeout", 15.0))
                    use_pty = bool(req.get("use_pty", False))
                    client = req.get("client") or {}
                    t0 = time.perf_counter()
                    raw = self.execute_with_reconnect(
                        req.get("host"),
                        req.get("port"),
                        lambda s: s.run_shell_bytes(cmd, timeout=timeout, use_pty=use_pty),
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    detail = cmd.strip().splitlines()[0] if cmd.strip() else ""
                    self.record_op(client, "shell", elapsed_ms, len(raw), detail)
                    _send_ipc_msg(
                        conn,
                        {
                            "ok": True,
                            "host": self.host,
                            "port": self.port,
                            "data_b64": base64.b64encode(raw).decode("ascii"),
                        },
                    )
                elif op == "dump_ui":
                    prefix_cmd = req.get("prefix_cmd")
                    settle_sec = float(req.get("settle_sec", 0.0))
                    keep_temp = bool(req.get("keep_temp", False))
                    client = req.get("client") or {}
                    t0 = time.perf_counter()
                    tree = self.execute_with_reconnect(
                        req.get("host"),
                        req.get("port"),
                        lambda s: s.dump_ui_tree(
                            prefix_cmd=prefix_cmd, settle_sec=settle_sec, keep_temp=keep_temp
                        ),
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    size = len(json.dumps(tree, ensure_ascii=False).encode("utf-8"))
                    nodes = _count_tree_nodes(tree)
                    detail = f"nodes={nodes}" + (f" settle={settle_sec:.2f}s" if settle_sec else "")
                    self.record_op(client, "dump_ui", elapsed_ms, size, detail)
                    _send_ipc_msg(
                        conn,
                        {
                            "ok": True,
                            "host": self.host,
                            "port": self.port,
                            "tree": tree,
                        },
                    )
                else:
                    _send_ipc_msg(conn, {"ok": False, "error": f"Unknown op: {op}"})
        except (ConnectionError, EOFError):
            pass
        except Exception as exc:
            try:
                _send_ipc_msg(conn, {"ok": False, "error": str(exc)})
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def serve_forever(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((DAEMON_HOST, DAEMON_PORT))
        srv.listen(16)
        srv.settimeout(1.0)

        # Pre-connect immediately in a background thread so accept() starts in <5ms
        threading.Thread(
            target=lambda: self.execute_with_reconnect(self.host, self.port, lambda _s: None),
            daemon=True,
        ).start()

        reason = "stop"
        try:
            while self.running:
                if time.time() - self.last_active > DAEMON_IDLE_TIMEOUT:
                    reason = "idle"
                    break
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                t.start()
        finally:
            self.session_summary(reason)
            if self.session:
                self.session.close()
            srv.close()


def try_connect_daemon(host: str, port: int, timeout: float = 0.15) -> DaemonClientSession | None:
    try:
        sock = socket.create_connection((DAEMON_HOST, DAEMON_PORT), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return DaemonClientSession(host, port, sock)
    except Exception:
        return None


def spawn_background_daemon(host: str, port: int, verbose: bool = False):
    verbose_flag = " --verbose" if (
        verbose or os.environ.get("HDC_DAEMON_VERBOSE", "") == "1"
    ) else ""
    # Support launching via module or executable
    cmd_line = (
        f'"{sys.executable}" -m hdc_claw.cli --host {host} --port {port} '
        f"daemon --action serve{verbose_flag}"
    )
    if os.name == "nt":
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{cmd_line}'}} | Out-Null",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
            )
            return
        except Exception:
            pass

    cmd = [
        sys.executable,
        "-m",
        "hdc_claw.cli",
        "--host",
        host,
        "--port",
        str(port),
        "daemon",
        "--action",
        "serve",
    ]
    if verbose_flag:
        cmd.append("--verbose")
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def resolve_session(
    host: str | None = None,
    port: int | None = None,
    auto_scan: bool = True,
    force_scan: bool = False,
    use_daemon: bool = True,
) -> HdcSession | DaemonClientSession:
    """
    Resolve and return an active, connected session (either multiplexed via daemon or direct).
    Automatically uses the local daemon for ultra-low latency (<15ms) when enabled.
    """
    saved = load_saved_target()
    target_host = host or saved.get("host", "192.168.1.115")
    target_port = port or saved.get("port", 35429)

    if use_daemon and not force_scan:
        client = try_connect_daemon(target_host, target_port)
        if client is not None:
            return client
        spawn_background_daemon(target_host, target_port)
        deadline = time.time() + 1.2
        while time.time() < deadline:
            time.sleep(0.06)
            client = try_connect_daemon(target_host, target_port, timeout=0.1)
            if client is not None:
                return client

    try:
        sess = HdcSession(target_host, target_port)
        sess.connect()
        return sess
    except (ConnectionRefusedError, TimeoutError, socket.timeout, OSError) as exc:
        is_timeout = isinstance(exc, (TimeoutError, socket.timeout))
        if not auto_scan or (is_timeout and not force_scan):
            raise RuntimeError(
                f"Connection to {target_host}:{target_port} failed ({exc}). "
                f"If the tablet screen is locked/asleep, wake it up or re-check Wireless Debugging IP:Port "
                f"(or pass --scan to scan ports 30000-49999)."
            ) from exc
        print(
            f"[HDC-Claw] Connection to {target_host}:{target_port} failed ({exc}). "
            f"Scanning {target_host} for active HDC wireless debugging port...",
            file=sys.stderr,
        )
        found = scan_hdc_ports(target_host)
        if not found:
            raise RuntimeError(
                f"Could not connect to {target_host}:{target_port} and no open HDC port "
                f"was found in range 30000-49999 on {target_host}. Please verify wireless "
                f"debugging is enabled on the tablet and pass --host / --port."
            ) from exc
        new_port = found[0]
        print(f"[HDC-Claw] Discovered active HDC port: {target_host}:{new_port}", file=sys.stderr)
        sess = HdcSession(target_host, new_port)
        sess.connect()
        if use_daemon:
            spawn_background_daemon(target_host, new_port)
        return sess
