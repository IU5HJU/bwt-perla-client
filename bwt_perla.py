#!/usr/bin/env python3
"""Client BLE standalone per addolcitore BWT AQA Perla.

Uso:
  pip install bleak
  python bwt_perla.py scan
  python bwt_perla.py read [MAC]        # stato + ultimi consumi
  python bwt_perla.py dump [MAC]        # trame grezze, per analisi

Il protocollo e' ricostruito dal progetto GPL-3.0 Micka41/bwt-aqa-perla-ble:
questo file ne eredita la licenza (GPLv3).
"""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

U_SERVICE = "d973f2e0-b19e-11e2-9e96-0800200c9a66"
U_NOTIFY = "d973f2e1-b19e-11e2-9e96-0800200c9a66"     # storico (notify)
U_WRITE = "d973f2e2-b19e-11e2-9e96-0800200c9a66"      # comandi
U_BROADCAST = "d973f2e3-b19e-11e2-9e96-0800200c9a66"  # sale + indici
U_AUTH = "d973f2e4-b19e-11e2-9e96-0800200c9a66"       # lettura = "auth"

ADDR_QUARTI, ADDR_GIORNI = 0, 6400
MAX_QUARTI, MAX_GIORNI = 2880, 1825
PER_NOTIFICA = 9   # parole da 16 bit per trama


def w_le(b, o):
    return b[o] | (b[o + 1] << 8)


def decode_broadcast(b: bytes) -> dict:
    if len(b) < 15:
        raise ValueError(f"BROADCAST troppo corto: {len(b)} byte")
    sale = w_le(b, 0) + w_le(b, 2) * 65536
    if b[13] >= 2:          # firmware V2.x: valore 4x troppo alto
        sale //= 4
    capa = w_le(b, 10) * 1000
    pct = 0
    if capa > 0:
        pct = (sale * 100) // capa
        pct = 0 if pct > 50000 else max(0, min(100, pct))
    return {
        "sale_g": sale, "capacita_g": capa, "sale_pct": pct,
        "idx_quarti": w_le(b, 4), "idx_giorni": w_le(b, 6),
        "sale_per_rigenerazione_g": w_le(b, 8),
        "allarme_sale": bool(b[12] & 1),
        "buffer_quarti_ciclico": bool(b[12] & 2),
        "buffer_giorni_ciclico": bool(b[12] & 4),
        "firmware": f"A22X V{b[13]}.{b[14]}",
    }


def decode_frame(f: bytes, quarti: bool):
    """Trama da 20 byte: 2 byte di rango + 9 parole little-endian."""
    if len(f) < 20:
        return []
    out = []
    for i in range(9):
        w = w_le(f, 2 + 2 * i)
        if w > 32767:       # 0xFFFF = cella non scritta
            break
        if quarti:
            out.append({"litri": w & 0x3FF, "rigen": bool(w & 0x800),
                        "blackout": bool(w & 0x400)})
        else:
            out.append({"litri": (w & 0x7FF) * 10, "rigen": (w >> 12) & 3,
                        "blackout": bool(w & 0x800)})
    return out


def read_cmd(addr: int, nbytes: int, inter_ms: int = 20) -> bytes:
    return bytes([2, addr & 255, addr >> 8, nbytes & 255, nbytes >> 8,
                  inter_ms & 255, inter_ms >> 8])


class Perla:
    def __init__(self, client: BleakClient):
        self.c = client
        self.frames: list[bytes] = []
        self.ev = asyncio.Event()

    def _on(self, _, data: bytearray):
        self.frames.append(bytes(data))
        self.ev.set()

    async def open(self):
        await self.c.start_notify(U_NOTIFY, self._on)
        await self.c.read_gatt_char(U_AUTH)

    async def broadcast(self) -> bytes:
        return bytes(await self.c.read_gatt_char(U_BROADCAST))

    async def _block(self, addr: int, n_entries: int):
        nbytes = n_entries * 2
        n_frames = (nbytes + 17) // 18
        self.frames.clear()
        await self.c.write_gatt_char(U_WRITE, read_cmd(addr, nbytes))
        good: list[bytes] = []
        while len(good) < n_frames:
            self.ev.clear()
            for f in self.frames:   # il rango (2 byte) riparte da 0 a ogni READ
                if len(f) >= 2 and (f[1] * 256 + f[0]) == len(good):
                    good.append(f)
                    if len(good) == n_frames:
                        break
            if len(good) >= n_frames:
                break
            try:
                await asyncio.wait_for(self.ev.wait(), 5.0)
            except asyncio.TimeoutError:
                raise RuntimeError(f"blocco incompleto {len(good)}/{n_frames}")
        return good

    async def history(self, base: int, size: int, idx: int, n: int, quarti: bool):
        """Ultime n voci prima di idx (buffer circolare); ordine cronologico."""
        n = min(n, size)
        start = (idx - n) % size
        res, pos, left = [], start, n
        while left > 0:
            chunk = min(left, 90, size - pos)   # non oltrepassare la fine
            for f in await self._block(base + 2 * pos, chunk):
                res += decode_frame(f, quarti)
            pos = (pos + chunk) % size
            left -= chunk
        return res[:n]


async def find(mac: str | None) -> str:
    if mac:
        return mac
    print("Scansione (10 s)...")
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    for addr, (d, adv) in found.items():
        if U_SERVICE in [u.lower() for u in adv.service_uuids]:
            print(f"Trovato: {addr}  rssi={adv.rssi}")
            return addr
    raise SystemExit("Addolcitore non trovato: chiudi l'app BWT / avvicinati.")


async def cmd_scan():
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    for addr, (d, adv) in sorted(found.items(), key=lambda x: -x[1][1].rssi):
        tag = "  <== BWT" if U_SERVICE in [u.lower() for u in adv.service_uuids] else ""
        print(f"{addr}  {adv.rssi:4} dBm  {d.name or '-'}{tag}")


async def cmd_read(mac, dump=False):
    addr = await find(mac)
    async with BleakClient(addr, timeout=30) as cl:
        p = Perla(cl)
        await p.open()
        raw = await p.broadcast()
        print("BROADCAST raw:", raw.hex(" "))
        b = decode_broadcast(raw)
        for k, v in b.items():
            print(f"  {k}: {v}")
        if dump:
            for f in await p._block(ADDR_QUARTI + 2 * ((b["idx_quarti"] - 18) % MAX_QUARTI), 18):
                print("frame:", f.hex(" "))
            return
        q = await p.history(ADDR_QUARTI, MAX_QUARTI, b["idx_quarti"], 96, True)
        print(f"\nUltimi {len(q)} quarti d'ora (~24h): {sum(e['litri'] for e in q)} L,"
              f" rigenerazioni (fronti): "
              f"{sum(1 for i, e in enumerate(q) if e['rigen'] and (i == 0 or not q[i-1]['rigen']))}")
        d = await p.history(ADDR_GIORNI, MAX_GIORNI, b["idx_giorni"], 14, False)
        print("\nUltimi giorni (dal piu' vecchio), litri:")
        print("  ", [e["litri"] for e in d])


def selftest():
    # trama sintetica: rango 0, parole 100, 0x0800|5, 0xFFFF
    f = bytes([0, 0, 100, 0, 5, 0x08, 0xFF, 0xFF]) + bytes(12)
    e = decode_frame(f, True)
    assert e[0]["litri"] == 100 and e[1]["rigen"] and len(e) == 2, e
    bc = bytes([0xE8, 0x03, 0, 0, 1, 0, 2, 0, 50, 0, 10, 0, 0, 1, 5])
    assert decode_broadcast(bc)["sale_pct"] == 10, decode_broadcast(bc)
    print("selftest ok")


if __name__ == "__main__":
    a = sys.argv[1:] or ["scan"]
    if a[0] == "scan":
        asyncio.run(cmd_scan())
    elif a[0] in ("read", "dump"):
        asyncio.run(cmd_read(a[1] if len(a) > 1 else None, a[0] == "dump"))
    elif a[0] == "selftest":
        selftest()
    else:
        print(__doc__)
