"""Spike step 5: host-side network bench against net_probe running in QEMU.

App zone (host-side tool). Python standard library only.
Assumes QEMU forwards host ports to the chip, e.g.
  -nic user,model=open_eth,hostfwd=tcp:127.0.0.1:18070-:3333,hostfwd=tcp:127.0.0.1:18072-:3334,hostfwd=tcp:127.0.0.1:18073-:3335

  echo   (18070 -> 3333): N round trips of a small message -> RTT min/median/p95/max
  upload (18072 -> 3334): send BYTES to the chip's sink  -> MB/s host->chip
  download (18073 -> 3335): read the chip's 4 MiB source -> MB/s chip->host
"""
import argparse
import socket
import statistics
import time


def connect(host, port, retries=60):
    for _ in range(retries):
        try:
            s = socket.create_connection((host, port), timeout=10)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return s
        except OSError:
            time.sleep(1)
    raise SystemExit(f"cannot connect to {host}:{port}")


def echo(host, port, n, size):
    s = connect(host, port)
    msg = b"p" * size
    rtts = []
    for _ in range(n):
        t = time.perf_counter()
        s.sendall(msg)
        got = 0
        while got < size:
            chunk = s.recv(size - got)
            if not chunk:
                raise SystemExit("echo: connection closed")
            got += len(chunk)
        rtts.append((time.perf_counter() - t) * 1000)
    s.close()
    rtts.sort()
    print(f"ECHO n={n} size={size}B rtt_ms min={rtts[0]:.2f} median={statistics.median(rtts):.2f} "
          f"p95={rtts[int(0.95 * (n - 1))]:.2f} max={rtts[-1]:.2f}", flush=True)


def upload(host, port, nbytes):
    s = connect(host, port)
    buf = b"u" * 65536
    t = time.perf_counter()
    sent = 0
    while sent < nbytes:
        sent += s.send(buf[: min(len(buf), nbytes - sent)])
    s.shutdown(socket.SHUT_WR)
    s.recv(1)  # wait for the chip to close: everything was read
    dt = time.perf_counter() - t
    s.close()
    print(f"UPLOAD bytes={sent} s={dt:.2f} MBps={sent / dt / 1e6:.3f}", flush=True)


def download(host, port):
    s = connect(host, port)
    t = time.perf_counter()
    got = 0
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        got += len(chunk)
    dt = time.perf_counter() - t
    s.close()
    print(f"DOWNLOAD bytes={got} s={dt:.2f} MBps={got / dt / 1e6:.3f}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--echo-port", type=int, default=18070)
    ap.add_argument("--sink-port", type=int, default=18072)
    ap.add_argument("--source-port", type=int, default=18073)
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--size", type=int, default=32)
    ap.add_argument("--upload-bytes", type=int, default=4 * 1024 * 1024)
    ap.add_argument("--only", choices=["echo", "upload", "download"])
    a = ap.parse_args()
    if a.only in (None, "echo"):
        echo(a.host, a.echo_port, a.n, a.size)
    if a.only in (None, "upload"):
        upload(a.host, a.sink_port, a.upload_bytes)
    if a.only in (None, "download"):
        download(a.host, a.source_port)


if __name__ == "__main__":
    main()
