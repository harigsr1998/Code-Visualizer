"""Launch Espressif QEMU as a separate process and read UART0 over TCP (app zone).

QEMU is started with `-serial tcp:...,server=on,wait=on`, so the chip doesn't
start until we're connected and no output is lost. Every UART line is stored
with a host timestamp (time.perf_counter()).
"""

from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from pathlib import Path

from paths import PORT_UART, QEMU

# rst:0x1 (POWERON_RESET) is the normal first boot line; any other reset cause is a crash/watchdog.
PANIC_RE = re.compile(r"Guru Meditation|abort\(\) was called|assert failed|Cache disabled but cached|^rst:0x(?!1 \()|Rebooting\.\.\.")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class QemuSession:
    def __init__(self, image: Path, chip: str = "esp32", extra: list[str] | None = None,
                 uart_port: int = PORT_UART, stderr_file: Path | None = None,
                 monitor_port: int | None = None):
        self.image = Path(image).resolve()
        self.chip = chip
        self.extra = list(extra or [])
        self.uart_port = uart_port
        self.monitor_port = monitor_port
        self.stderr_file = stderr_file
        self.lines: list[tuple[float, str]] = []
        self.t_connect = 0.0
        self.proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._thr: threading.Thread | None = None
        self._stop = threading.Event()

    def args(self) -> list[str]:
        # Relative image name + cwd avoids QEMU's drive-letter parsing quirks.
        mon = f"tcp:127.0.0.1:{self.monitor_port},server=on,wait=off" if self.monitor_port else "none"
        return [str(QEMU), "-machine", self.chip, "-display", "none", "-monitor", mon,
                "-serial", f"tcp:127.0.0.1:{self.uart_port},server=on,wait=on",
                "-drive", f"file={self.image.name},if=mtd,format=raw", *self.extra]

    def start(self) -> "QemuSession":
        err = open(self.stderr_file, "wb") if self.stderr_file else subprocess.DEVNULL
        self.proc = subprocess.Popen(self.args(), cwd=self.image.parent, stdout=subprocess.DEVNULL, stderr=err)
        deadline = time.time() + 15
        while True:
            try:
                self._sock = socket.create_connection(("127.0.0.1", self.uart_port), timeout=1)
                break
            except OSError:
                if time.time() > deadline or self.proc.poll() is not None:
                    self.stop()
                    raise RuntimeError(f"could not connect to QEMU UART on {self.uart_port}")
                time.sleep(0.05)
        self.t_connect = time.perf_counter()
        self._sock.settimeout(0.2)
        self._thr = threading.Thread(target=self._reader, daemon=True)
        self._thr.start()
        return self

    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            buf += data
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                text = ANSI_RE.sub("", ln.decode("latin-1").rstrip("\r"))
                self.lines.append((time.perf_counter() - self.t_connect, text))

    def wait_for(self, pattern: str, timeout: float) -> tuple[float, str] | None:
        rx = re.compile(pattern)
        deadline = time.perf_counter() + timeout
        seen = 0
        while time.perf_counter() < deadline:
            for t, ln in self.lines[seen:]:
                if rx.search(ln):
                    return t, ln
            seen = len(self.lines)
            if self.proc and self.proc.poll() is not None:
                return None
            time.sleep(0.02)
        return None

    def panics(self) -> list[str]:
        return [ln for _, ln in self.lines if PANIC_RE.search(ln)]

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        if self._thr:
            self._thr.join(timeout=2)

    def __enter__(self) -> "QemuSession":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


class Monitor:
    """QEMU human monitor (HMP) over TCP: `log exec,nochain`, `info registers -a`, ..."""

    PROMPT = b"(qemu) "

    def __init__(self, port: int, timeout: float = 10.0):
        deadline = time.time() + timeout
        while True:
            try:
                self.s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.05)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        self._until_prompt()

    def _until_prompt(self) -> str:
        while self.PROMPT not in self.buf:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("monitor closed")
            self.buf += chunk
        out, self.buf = self.buf.split(self.PROMPT, 1)
        return ANSI_RE.sub("", out.decode("latin-1"))

    def cmd(self, text: str) -> str:
        self.s.sendall(text.encode() + b"\n")
        out = self._until_prompt()
        # HMP echoes the command line (with terminal escapes); drop the first line.
        return out.split("\n", 1)[1] if "\n" in out else ""

    def close(self) -> None:
        try:
            self.s.close()
        except OSError:
            pass
