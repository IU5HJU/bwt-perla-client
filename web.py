#!/usr/bin/env python3
"""Pagina web locale per monitorare l'addolcitore (solo libreria standard).

  python web.py                      # http://IP-DEL-RASPBERRY:8080
  python web.py --port 8080 --data-dir ~/bwt-data

Legge i CSV scritti da `bwt_perla.py log`: sola lettura, nessuna
autenticazione. Da usare solo in rete locale, mai esposta a internet.

Licenza GPLv3, come il resto del progetto.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tracker import CICLI_CSV, QUARTI_CSV, Tracker

INDEX = Path(__file__).with_name("web") / "index.html"
MAX_PUNTI_SALE = 500
GIORNI_MOSTRATI = 30
ORE_MOSTRATE = 48


def _leggi(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _num(v, tipo=float):
    try:
        return tipo(v)
    except (TypeError, ValueError):
        return None


def riepilogo(data_dir, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    tr = Tracker(data_dir)
    cicli = _leggi(tr.dir / CICLI_CSV)
    quarti = _leggi(tr.dir / QUARTI_CSV)

    ultima = dict(cicli[-1]) if cicli else None
    eta_min = None
    if ultima:
        eta_min = round((now - datetime.fromisoformat(ultima["timestamp"]))
                        .total_seconds() / 60)

    # serie del sale stimato, ridotta a MAX_PUNTI_SALE punti
    sale = [{"t": r["timestamp"], "g": _num(r["sale_stimato_g"], int),
             "pct": _num(r["sale_stimato_pct"], int)}
            for r in cicli if r["sale_stimato_g"] != ""]
    if len(sale) > MAX_PUNTI_SALE:
        passo = len(sale) / MAX_PUNTI_SALE
        sale = [sale[int(i * passo)] for i in range(MAX_PUNTI_SALE)] + [sale[-1]]

    litri_g: dict[str, int] = defaultdict(int)
    litri_h: dict[str, int] = defaultdict(int)
    for q in quarti:
        litri_g[q["inizio_approx"][:10]] += int(q["litri"])
        litri_h[q["inizio_approx"][:13]] += int(q["litri"])
    rigen_g: dict[str, int] = defaultdict(int)
    black_g: dict[str, int] = defaultdict(int)
    for r in cicli:
        rigen_g[r["timestamp"][:10]] += int(r["rigenerazioni_nuove"])
        black_g[r["timestamp"][:10]] += int(r["blackout_nuovi"])

    oggi = now.date()
    giorni = []
    for i in range(GIORNI_MOSTRATI - 1, -1, -1):
        g = (oggi - timedelta(days=i)).isoformat()
        giorni.append({"giorno": g, "litri": litri_g.get(g, 0),
                       "rigenerazioni": rigen_g.get(g, 0),
                       "blackout": black_g.get(g, 0)})
    # i giorni prima della prima registrazione non hanno dati: si tolgono
    primo = min(litri_g.keys() | rigen_g.keys(), default=None)
    giorni = [g for g in giorni if primo and g["giorno"] >= primo]

    ore = []
    inizio_ore = now.replace(minute=0, second=0, microsecond=0)
    for i in range(ORE_MOSTRATE - 1, -1, -1):
        h = (inizio_ore - timedelta(hours=i)).isoformat(timespec="hours")
        ore.append({"ora": h, "litri": litri_h.get(h, 0)})

    complete = [g["litri"] for g in giorni if g["giorno"] != oggi.isoformat()][-7:]
    return {
        "ora": now.isoformat(timespec="seconds"),
        "eta_ultima_lettura_min": eta_min,
        "ultima": ultima,
        "rabbocco": tr.state["rabbocco"],
        "totali": {"litri": tr.state["litri_totali"],
                   "rigenerazioni": tr.state["rigenerazioni_totali"],
                   "blackout": tr.state["blackout_totali"]},
        "media_litri_giorno_7g": round(sum(complete) / len(complete)) if complete else None,
        "sale": sale, "giorni": giorni, "ore": ore,
    }


class Handler(BaseHTTPRequestHandler):
    data_dir = "~/bwt-data"

    def _invia(self, codice, tipo, corpo: bytes):
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._invia(200, "text/html; charset=utf-8", INDEX.read_bytes())
        elif path == "/api/riepilogo":
            corpo = json.dumps(riepilogo(self.data_dir)).encode()
            self._invia(200, "application/json", corpo)
        elif path == "/healthz":
            self._invia(200, "text/plain", b"ok")
        else:
            self._invia(404, "text/plain", b"non trovato")

    def log_message(self, fmt, *args):   # niente rumore nel journal
        pass


def main():
    ap = argparse.ArgumentParser(description="Pagina web BWT Perla")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data-dir", default="~/bwt-data")
    a = ap.parse_args()
    Handler.data_dir = a.data_dir
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"In ascolto su http://{a.host}:{a.port}  (dati: {a.data_dir})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
