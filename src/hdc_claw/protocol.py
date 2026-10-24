"""
Pure-Python HarmonyOS NEXT / 6.0 HDC (OpenHarmony Device Connector) Protocol Implementation.

Features:
- Pure socket implementation of the HDC binary framing protocol.
- RSA-3072 key generation, certificate persistence, and authentication handshake.
- Fast-path single-packet execution via CMD_UNITY_EXECUTE (1001) + TCP_NODELAY.
- Binary-clean streams for shell commands, UI layout trees, and screen captures.
- Automatic multi-threaded port scanner for dynamic wireless debugging (30000-49999).
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
import random
import re
import socket
import struct
import sys
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HARMONY_DIR = Path.home() / ".harmony"
STATE_PATH = Path.home() / ".gemini" / "hdc_last_target.json"

HANDSHAKE_BANNER = b"OHOS HDC"
HDC_VERSION = b"Ver: 3.1.0ecdb2b9136c20031c"

CMD_KERNEL_HANDSHAKE = 1
CMD_KERNEL_CHANNEL_CLOSE = 2
CMD_KERNEL_ECHO = 9
CMD_KERNEL_ECHO_RAW = 10
CMD_UNITY_EXECUTE = 1001
CMD_SHELL_INIT = 2000
CMD_SHELL_DATA = 2001

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\].*?\x07")


def ensure_harmony_keys() -> tuple[rsa.RSAPrivateKey, bytes]:
    """
    Ensure local RSA-3072 keypair exists in ~/.harmony/hdckey(.pub).
    Generates a new keypair if missing.
    """
    HARMONY_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = HARMONY_DIR / "hdckey"
    pub_path = HARMONY_DIR / "hdckey.pub"
    if priv_path.exists() and pub_path.exists():
        priv_pem = priv_path.read_bytes()
        pub_pem = pub_path.read_bytes()
        priv_key = serialization.load_pem_private_key(priv_pem, password=None)
        return priv_key, pub_pem

    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    priv_pem = priv_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    pub_pem = priv_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    return priv_key, pub_pem


def encode_varint(val: int) -> bytes:
    out = bytearray()
    while val > 0x7F:
        out.append((val & 0x7F) | 0x80)
        val >>= 7
    out.append(val & 0x7F)
    return bytes(out)


def decode_varint(buf: bytes, idx: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        b = buf[idx]
        idx += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, idx


def encode_field_varint(field_num: int, val: int) -> bytes:
    tag = (field_num << 3) | 0
    return encode_varint(tag) + encode_varint(val)


def encode_field_bytes(field_num: int, data: bytes | str) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    tag = (field_num << 3) | 2
    return encode_varint(tag) + encode_varint(len(data)) + data


def parse_proto(buf: bytes) -> dict[int, int | bytes]:
    idx = 0
    fields: dict[int, int | bytes] = {}
    while idx < len(buf):
        tag, idx = decode_varint(buf, idx)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val, idx = decode_varint(buf, idx)
            fields[field_num] = val
        elif wire_type == 2:
            length, idx = decode_varint(buf, idx)
            fields[field_num] = buf[idx : idx + length]
            idx += length
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
    return fields


def build_packet(channel_id: int, command_flag: int, data: bytes = b"") -> bytes:
    protect = (
        encode_field_varint(1, channel_id)
        + encode_field_varint(2, command_flag)
        + encode_field_varint(3, 0)
        + encode_field_varint(4, 0x09)
    )
    head = struct.pack(">2s2sBHI", b"HW", b"\x00\x00", 1, len(protect), len(data))
    return head + protect + data


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed by remote endpoint")
        buf.extend(chunk)
    return bytes(buf)


def recv_packet(sock: socket.socket) -> tuple[int, int, bytes]:
    head = recv_exact(sock, 11)
    flag, _reserve, _ver, head_size, data_size = struct.unpack(">2s2sBHI", head)
    if flag != b"HW":
        raise RuntimeError(f"Invalid HDC packet magic: {flag!r}")
    protect_buf = recv_exact(sock, head_size)
    data_buf = recv_exact(sock, data_size) if data_size > 0 else b""
    protect = parse_proto(protect_buf)
    return int(protect.get(1, 0)), int(protect.get(2, 0)), data_buf


def build_handshake(
    banner: bytes, auth_type: int, session_id: int, connect_key: bytes, buf: bytes, version: bytes
) -> bytes:
    return (
        encode_field_bytes(1, banner)
        + encode_field_varint(2, auth_type)
        + encode_field_varint(3, session_id)
        + encode_field_bytes(4, connect_key)
        + encode_field_bytes(5, buf)
        + encode_field_bytes(6, version)
    )


def rsa_private_encrypt_pkcs1(priv_key: rsa.RSAPrivateKey, token_bytes: bytes) -> bytes:
    key_size = priv_key.key_size // 8
    pad_len = key_size - 3 - len(token_bytes)
    padded = b"\x00\x01" + (b"\xff" * pad_len) + b"\x00" + token_bytes
    numbers = priv_key.private_numbers()
    m = int.from_bytes(padded, byteorder="big")
    c = pow(m, numbers.d, numbers.public_numbers.n)
    enc = c.to_bytes(key_size, byteorder="big")
    return base64.b64encode(enc)


def load_saved_target() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"host": "192.168.1.115", "port": 35429}


def save_target(host: str, port: int):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"host": host, "port": port, "updated_at": time.time()}, indent=2),
        encoding="utf-8",
    )


def probe_hdc_port(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            hs1 = build_handshake(
                HANDSHAKE_BANNER, 0, 12345678, f"{host}:{port}".encode(), b"", HDC_VERSION
            )
            s.sendall(build_packet(0, CMD_KERNEL_HANDSHAKE, hs1))
            head = s.recv(11)
            return len(head) == 11 and head.startswith(b"HW")
    except Exception:
        return False


def scan_hdc_ports(host: str, start_port: int = 30000, end_port: int = 49999) -> list[int]:
    found = []
    ports = list(range(start_port, end_port + 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=256) as ex:
        futs = {ex.submit(probe_hdc_port, host, p, 0.2): p for p in ports}
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                if fut.result():
                    found.append(p)
            except Exception:
                pass
    return sorted(found)


class HdcSession:
    """
    Direct authenticated TCP session to a HarmonyOS HDC device.
    Supports single-packet execution (CMD_UNITY_EXECUTE = 1001) for ultra-fast,
    echo-free, binary-clean shell command execution.
    """
    is_daemon_client = False

    def __init__(self, host: str, port: int, auth_timeout: float = 25.0):
        self.host = host
        self.port = port
        self.auth_timeout = auth_timeout
        self.sock: socket.socket | None = None
        self.session_id = random.randint(10000000, 0x7FFFFFFF)
        self._next_channel_id = 101
        self._lock = threading.Lock()

    def connect(self):
        priv_key, pub_pem = ensure_harmony_keys()
        connect_key = f"{self.host}:{self.port}".encode()

        sock = socket.create_connection((self.host, self.port), timeout=4.0)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self.auth_timeout)
        self.sock = sock

        hs1 = build_handshake(HANDSHAKE_BANNER, 0, self.session_id, connect_key, b"", HDC_VERSION)
        self.sock.sendall(build_packet(0, CMD_KERNEL_HANDSHAKE, hs1))

        while True:
            ch_id, cmd_flag, payload = recv_packet(self.sock)
            if cmd_flag == CMD_KERNEL_HANDSHAKE:
                hs = parse_proto(payload)
                auth_type = hs.get(2, 0)
                if auth_type == 3:  # AUTH_PUBLICKEY
                    hostname = socket.gethostname().encode("utf-8")
                    buf = hostname + b"\x0c" + pub_pem
                    hs_pub = build_handshake(
                        HANDSHAKE_BANNER, 3, self.session_id, connect_key, buf, HDC_VERSION
                    )
                    self.sock.sendall(build_packet(0, CMD_KERNEL_HANDSHAKE, hs_pub))
                elif auth_type == 2:  # AUTH_SIGNATURE
                    token = hs.get(5, b"")
                    sig_b64 = rsa_private_encrypt_pkcs1(priv_key, token)
                    hs_sig = build_handshake(
                        HANDSHAKE_BANNER, 2, self.session_id, connect_key, sig_b64, HDC_VERSION
                    )
                    self.sock.sendall(build_packet(0, CMD_KERNEL_HANDSHAKE, hs_sig))
                elif auth_type == 4:  # AUTH_OK
                    break
                else:
                    raise PermissionError(f"HDC handshake rejected (auth_type={auth_type}): {hs}")

        ch_id, cmd_flag, _payload = recv_packet(self.sock)
        if cmd_flag == CMD_KERNEL_CHANNEL_CLOSE:
            self.sock.sendall(build_packet(ch_id, CMD_KERNEL_CHANNEL_CLOSE, b"\x00"))

        save_target(self.host, self.port)

    def run_shell_bytes(self, command: str, timeout: float = 15.0, use_pty: bool = False) -> bytes:
        """
        Execute shell command on tablet and return raw 8-bit bytes.
        Uses CMD_UNITY_EXECUTE (1001) by default:
        - Single packet out, zero sleep, zero PTY echo, 100% 8-bit binary clean.
        """
        with self._lock:
            if not self.sock:
                self.connect()
            assert self.sock is not None
            channel_id = self._next_channel_id
            self._next_channel_id += 1

            self.sock.settimeout(timeout)
            if not use_pty:
                self.sock.sendall(
                    build_packet(channel_id, CMD_UNITY_EXECUTE, command.encode("utf-8"))
                )
            else:
                self.sock.sendall(build_packet(channel_id, CMD_SHELL_INIT, b""))
                time.sleep(0.08)
                self.sock.sendall(
                    build_packet(channel_id, CMD_SHELL_DATA, (command + "; exit\n").encode("utf-8"))
                )

            output = bytearray()
            try:
                while True:
                    ch_id, cmd_flag, payload = recv_packet(self.sock)
                    if cmd_flag in (CMD_KERNEL_ECHO, CMD_KERNEL_ECHO_RAW):
                        if ch_id == channel_id:
                            output.extend(payload)
                    elif cmd_flag == CMD_KERNEL_CHANNEL_CLOSE:
                        self.sock.sendall(build_packet(ch_id, CMD_KERNEL_CHANNEL_CLOSE, b"\x00"))
                        if ch_id == channel_id:
                            break
            except socket.timeout:
                pass
            return bytes(output)

    def run_shell(self, command: str, timeout: float = 15.0, use_pty: bool = False) -> str:
        raw_bytes = self.run_shell_bytes(command, timeout=timeout, use_pty=use_pty)
        return raw_bytes.decode("utf-8", errors="replace")

    def dump_ui_tree(self, prefix_cmd: str | None = None, settle_sec: float = 0.0) -> dict:
        """
        Fast-path UI dump over CMD_UNITY_EXECUTE (1001).
        Optionally runs `prefix_cmd` and `sleep settle_sec` in a single on-device pipeline.
        """
        parts = []
        if prefix_cmd:
            parts.append(f"({prefix_cmd}) >/dev/null 2>&1")
        if settle_sec > 0:
            parts.append(f"sleep {settle_sec:.2f}")
        parts.append(
            "uitest dumpLayout -p /data/local/tmp/hdc_ui.json >/dev/null 2>&1 "
            "&& cat /data/local/tmp/hdc_ui.json"
        )
        sh_cmd = "; ".join(parts)
        raw_bytes = self.run_shell_bytes(sh_cmd, timeout=15.0)
        idx = raw_bytes.find(b"{")
        if idx < 0:
            raise RuntimeError(
                f"Failed to dump UI layout tree. Raw output: {raw_bytes[:300]!r}"
            )
        tree = json.loads(raw_bytes[idx:].decode("utf-8", errors="replace"))
        return tree

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


def push_file_over_shell(sess: HdcSession | Any, local_path: Path, remote_path: str) -> dict:
    data = local_path.read_bytes()
    local_sha256 = hashlib.sha256(data).hexdigest()
    b64_str = base64.b64encode(data).decode("ascii")

    sess.run_shell(f"rm -f '{remote_path}' '{remote_path}.b64'", timeout=5.0)
    chunk_size = 49152
    for i in range(0, len(b64_str), chunk_size):
        chunk = b64_str[i : i + chunk_size]
        sess.run_shell(f"echo -n '{chunk}' >> '{remote_path}.b64'", timeout=10.0)

    verify_raw = sess.run_shell(
        f"base64 -d '{remote_path}.b64' > '{remote_path}'; "
        f"rm -f '{remote_path}.b64'; "
        f"chmod 644 '{remote_path}'; "
        f"sha256sum '{remote_path}'; ls -l '{remote_path}'",
        timeout=15.0,
    )
    clean_verify = ANSI_ESCAPE_RE.sub("", verify_raw)
    if local_sha256 not in clean_verify:
        raise RuntimeError(
            f"SHA256 mismatch after pushing {local_path} -> {remote_path}!\n"
            f"Expected: {local_sha256}\nActual output:\n{clean_verify}"
        )
    return {
        "local_path": str(local_path),
        "remote_path": remote_path,
        "size_bytes": len(data),
        "sha256": local_sha256,
    }


def pull_file_over_shell(sess: HdcSession | Any, remote_path: str, local_path: Path) -> int:
    file_bytes = sess.run_shell_bytes(f"cat '{remote_path}'", timeout=20.0)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(file_bytes)
    return len(file_bytes)
