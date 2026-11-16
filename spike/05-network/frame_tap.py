"""Spike step 5: prove that QEMU can hand every Ethernet frame to a process we write.

App zone (host-side tool, AGPLv3 plan). Python standard library only.

QEMU's socket netdevs and net filters all frame packets the same way on a
stream socket: a 4-byte big-endian length, then the raw Ethernet frame
(no vnet header unless vnet_hdr_support=on).

Modes (QEMU connects to us as a TCP client, so start this first):

  mirror  PORT
      Read-only copy of every frame. QEMU side:
        -nic user,model=open_eth,id=n0
        -chardev socket,id=m0,host=127.0.0.1,port=PORT
        -object filter-mirror,id=fm0,netdev=n0,queue=all,outdev=m0
      slirp still does the NAT; we only watch (sequence-diagram use case).

  mitm  PORT_BASE  [--drop-tcp-port N]
      Full man-in-the-middle in front of slirp: four chardevs at PORT_BASE..+3
        +0 guest->net frames diverted to us     (filter-redirector queue=rx outdev)
        +1 guest->net frames we send back       (filter-redirector queue=rx indev)
        +2 net->guest frames diverted to us     (filter-redirector queue=tx outdev)
        +3 net->guest frames we send back       (filter-redirector queue=tx indev)
      We forward each frame (or drop it), so the app decides what reaches the
      network: the hook a Wi-Fi substitute or a port-mapping UI would need.

  stream  PORT
      -netdev stream,...,server=off,addr.port=PORT: we ARE the whole network.
      Nothing answers, so this only counts what the chip sends (DHCP etc.).

Every mode prints one line per frame and a summary on Ctrl+C / timeout.
"""
import argparse
import asyncio
import struct
import sys
import time
from collections import Counter

stats = Counter()
T0 = time.monotonic()


def describe(f: bytes) -> str:
    if len(f) < 14:
        return f"runt {len(f)}B"
    dst, src, et = f[0:6].hex(":"), f[6:12].hex(":"), struct.unpack("!H", f[12:14])[0]
    p = f[14:]
    if et == 0x0806 and len(p) >= 28:
        op = struct.unpack("!H", p[6:8])[0]
        return f"ARP {'who-has' if op == 1 else 'reply'} {'.'.join(map(str, p[24:28]))} (from {'.'.join(map(str, p[14:18]))})"
    if et == 0x86DD:
        return f"IPv6 next={p[6] if len(p) > 6 else '?'} {len(f)}B"
    if et != 0x0800 or len(p) < 20:
        return f"eth type=0x{et:04x} {src}->{dst} {len(f)}B"
    ihl = (p[0] & 0x0F) * 4
    proto = p[9]
    s_ip, d_ip = ".".join(map(str, p[12:16])), ".".join(map(str, p[16:20]))
    l4 = p[ihl:]
    if proto in (6, 17) and len(l4) >= 4:
        sp, dp = struct.unpack("!HH", l4[0:4])
        tag = ""
        if proto == 17 and 67 in (sp, dp):
            tag = " DHCP"
        elif 53 in (sp, dp):
            tag = " DNS"
        if proto == 6 and len(l4) >= 14:
            fl = l4[13]
            names = "".join(n for b, n in ((1, "F"), (2, "S"), (4, "R"), (8, "P"), (16, "A")) if fl & b)
            return f"TCP {s_ip}:{sp} -> {d_ip}:{dp} [{names}] {len(f)}B{tag}"
        return f"UDP {s_ip}:{sp} -> {d_ip}:{dp} {len(f)}B{tag}"
    return f"IPv4 proto={proto} {s_ip} -> {d_ip} {len(f)}B"


def tcp_ports(f: bytes):
    if len(f) < 34 or struct.unpack("!H", f[12:14])[0] != 0x0800 or f[23] != 6:
        return ()
    ihl = (f[14] & 0x0F) * 4
    return struct.unpack("!HH", f[14 + ihl:14 + ihl + 4])


async def read_frames(reader):
    while True:
        hdr = await reader.readexactly(4)
        (n,) = struct.unpack("!I", hdr)
        yield await reader.readexactly(n)


def log(direction, f, verdict="", quiet=False):
    stats[direction] += 1
    stats[direction + "_bytes"] += len(f)
    if not quiet:
        print(f"{time.monotonic() - T0:8.3f} {direction:9s} {describe(f)}{verdict}", flush=True)


async def serve_one(port):
    """Accept exactly one QEMU connection on 127.0.0.1:port."""
    fut = asyncio.get_running_loop().create_future()

    async def on_conn(r, w):
        if not fut.done():
            fut.set_result((r, w))

    server = await asyncio.start_server(on_conn, "127.0.0.1", port)
    return server, fut


async def run_simple(port, label, quiet):
    server, fut = await serve_one(port)
    print(f"listening on 127.0.0.1:{port} ({label})", flush=True)
    r, _ = await fut
    print("QEMU connected", flush=True)
    async for f in read_frames(r):
        log(label, f, quiet=quiet)


async def run_mitm(base, drop_port, quiet):
    pairs = [await serve_one(base + i) for i in range(4)]
    print(f"mitm listening on 127.0.0.1:{base}..{base + 3}", flush=True)
    conns = [await fut for _, fut in pairs]
    print("QEMU connected (4 chardevs)", flush=True)

    async def pump(src_idx, dst_idx, label):
        r = conns[src_idx][0]
        w = conns[dst_idx][1]
        async for f in read_frames(r):
            if drop_port and drop_port in tcp_ports(f):
                stats[label + "_dropped"] += 1
                log(label, f, "  ** DROPPED by app **", quiet)
                continue
            log(label, f, quiet=quiet)
            w.write(struct.pack("!I", len(f)) + f)
            await w.drain()

    await asyncio.gather(pump(0, 1, "chip->net"), pump(2, 3, "net->chip"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["mirror", "mitm", "stream"])
    ap.add_argument("port", type=int)
    ap.add_argument("--drop-tcp-port", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--quiet", action="store_true", help="summary only")
    a = ap.parse_args()
    if a.mode == "mitm":
        coro = run_mitm(a.port, a.drop_tcp_port, a.quiet)
    else:
        coro = run_simple(a.port, a.mode, a.quiet)

    async def bounded():
        try:
            await asyncio.wait_for(coro, a.seconds)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass

    try:
        asyncio.run(bounded())
    except KeyboardInterrupt:
        pass
    print("SUMMARY " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())), flush=True)


if __name__ == "__main__":
    sys.exit(main())
