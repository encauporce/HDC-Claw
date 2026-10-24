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
from pathlib import Path
from typing import Callable

from .protocol import (
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

    def dump_ui_tree(self, prefix_cmd: str | None = None, settle_sec: float = 0.0) -> dict:
        resp = self._call(
            {
                "op": "dump_ui",
                "host": self.host,
                "port": self.port,
                "prefix_cmd": prefix_cmd,
                "settle_sec": settle_sec,
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


class LocalHdcDaemonServer:
    """
    Multiplexer daemon that keeps an open, authenticated HdcSession to the device
    and serves local client requests over a lightweight IPC socket.
    """
    def __init__(self, initial_host: str, initial_port: int):
        self.host = initial_host
        self.port = initial_port
        self.session: HdcSession | None = None
        self.lock = threading.Lock()
        self.last_active = time.time()
        self.running = True
        self.started_at = time.time()
        self.commands_served = 0

    def get_or_connect_session(self, req_host: str | None, req_port: int | None) -> HdcSession:
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
        sess = HdcSession(target_host, target_port)
        sess.connect()
        self.session = sess
        self.host = target_host
        self.port = target_port
        return sess

    def execute_with_reconnect(self, req_host: str | None, req_port: int | None, fn: Callable[[HdcSession], Any]) -> Any:
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
                sess = self.get_or_connect_session(req_host, req_port)
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
                        },
                    )
                elif op == "stop":
                    _send_ipc_msg(conn, {"ok": True, "status": "stopping"})
                    self.running = False
                    break
                elif op == "shell_bytes":
                    cmd = req["command"]
                    timeout = float(req.get("timeout", 15.0))
                    use_pty = bool(req.get("use_pty", False))
                    raw = self.execute_with_reconnect(
                        req.get("host"),
                        req.get("port"),
                        lambda s: s.run_shell_bytes(cmd, timeout=timeout, use_pty=use_pty),
                    )
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
                    tree = self.execute_with_reconnect(
                        req.get("host"),
                        req.get("port"),
                        lambda s: s.dump_ui_tree(prefix_cmd=prefix_cmd, settle_sec=settle_sec),
                    )
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

        # Pre-connect in background
        threading.Thread(
            target=lambda: self.execute_with_reconnect(self.host, self.port, lambda _s: None),
            daemon=True,
        ).start()

        try:
            while self.running:
                if time.time() - self.last_active > DAEMON_IDLE_TIMEOUT:
                    break
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(target=self.handle_client, args=(conn,), daemon=True)
                t.start()
        finally:
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


def spawn_background_daemon(host: str, port: int):
    # Support launching via module or executable
    cmd_line = f'"{sys.executable}" -m hdc_claw.cli --host {host} --port {port} daemon --action serve'
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
