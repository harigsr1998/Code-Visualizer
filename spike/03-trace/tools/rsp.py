"""Minimal GDB Remote Serial Protocol client for QEMU's gdbstub (app zone, own code).

Enough for the spike: halt/continue, per-vCPU PC reads, single-step,
breakpoints, memory reads. QEMU exposes each vCPU as a gdb "thread"
(thread 1 = core 0 / PRO_CPU, thread 2 = core 1 / APP_CPU on ESP32).
"""

from __future__ import annotations

import socket
import time


class RSP:
    PC_REGNUM = 0  # Xtensa (esp32 core config): register 0 is pc

    def __init__(self, port: int, host: str = "127.0.0.1", timeout: float = 10.0):
        deadline = time.time() + timeout
        while True:
            try:
                self.s = socket.create_connection((host, port), timeout=timeout)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.05)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        self.ack = True
        self.packets = 0
        self.cmd("qSupported:multiprocess-;swbreak+;hwbreak+;vContSupported+")
        if self.cmd("QStartNoAckMode") == "OK":
            self.ack = False
        self.threads = self._threads()

    # --- framing -------------------------------------------------------------
    def _send(self, data: str) -> None:
        cs = sum(data.encode()) & 0xFF
        self.s.sendall(f"${data}#{cs:02x}".encode())
        self.packets += 1

    def _recv_packet(self) -> str:
        while True:
            start = self.buf.find(b"$")
            if start >= 0:
                end = self.buf.find(b"#", start)
                if end >= 0 and len(self.buf) >= end + 3:
                    payload = self.buf[start + 1:end]
                    self.buf = self.buf[end + 3:]
                    if self.ack:
                        self.s.sendall(b"+")
                    return payload.decode("latin-1")
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("gdbstub closed the connection")
            self.buf += chunk

    def cmd(self, data: str) -> str:
        self._send(data)
        return self._recv_packet()

    # --- helpers -------------------------------------------------------------
    def _threads(self) -> list[str]:
        out = []
        r = self.cmd("qfThreadInfo")
        while r.startswith("m"):
            out += r[1:].split(",")
            r = self.cmd("qsThreadInfo")
        return out

    def halt(self) -> str:
        self.s.sendall(b"\x03")
        return self._recv_packet()

    def cont(self) -> None:
        self._send("c")  # the stop reply arrives later (breakpoint / halt)

    def wait_stop(self) -> str:
        return self._recv_packet()

    def select(self, tid: str) -> None:
        self.cmd(f"Hg{tid}")

    def reg(self, num: int) -> int:
        r = self.cmd(f"p{num:x}")
        return int.from_bytes(bytes.fromhex(r), "little")

    def pc(self, tid: str | None = None) -> int:
        if tid is not None:
            self.select(tid)
        return self.reg(self.PC_REGNUM)

    def step(self, tid: str) -> str:
        return self.cmd(f"vCont;s:{tid}")

    def mem(self, addr: int, n: int) -> bytes:
        return bytes.fromhex(self.cmd(f"m{addr:x},{n:x}"))

    def u32(self, addr: int) -> int:
        return int.from_bytes(self.mem(addr, 4), "little")

    def bp(self, addr: int, hw: bool = False) -> str:
        return self.cmd(f"Z{1 if hw else 0},{addr:x},3")

    def unbp(self, addr: int, hw: bool = False) -> str:
        return self.cmd(f"z{1 if hw else 0},{addr:x},3")

    def close(self) -> None:
        try:
            self.s.close()
        except OSError:
            pass
