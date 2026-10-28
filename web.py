#!/usr/bin/env python3
"""Pagina web locale per monitorare l'addolcitore (solo libreria standard).

  python web.py                      # http://IP-DEL-RASPBERRY:8765
  python web.py --port 8765 --data-dir ~/bwt-data

Legge i CSV scritti da `bwt_perla.py log`. Le uniche scritture sono la
registrazione e l'annullamento di un rabbocco di sale (pulsante nella pagina).
Nessuna autenticazione, salvo un PIN facoltativo (`--pin`): da usare solo in
rete locale, mai esposta a internet.

Licenza GPLv3, come il resto del progetto.
"""
from __future__ import annotations

import argparse
import csv
import hmac
import json
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import carica_config, salva_config, valida_mac
from tracker import (CICLI_CSV, QUARTI_CSV, Tracker, annulla_ultimo_rabbocco,
                     carica_rabbocchi, registra_rabbocco)

WEB = Path(__file__).with_name("web")
INDEX = WEB / "index.html"
# File statici ammessi (elenco chiuso: niente percorsi presi dalla richiesta)
STATICI = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icons/icon.svg": ("icons/icon.svg", "image/svg+xml"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
    "/icons/maskable-512.png": ("icons/maskable-512.png", "image/png"),
    "/icons/apple-touch-icon.png": ("icons/apple-touch-icon.png", "image/png"),
    "/icons/favicon-32.png": ("icons/favicon-32.png", "image/png"),
    "/favicon.ico": ("icons/favicon-32.png", "image/png"),
}
MAX_PUNTI_SALE = 500
GIORNI_MOSTRATI = 30
ORE_MOSTRATE = 48
MAX_BODY = 4096
RABBOCCHI_MOSTRATI = 20
MSG_NO_BLUETOOTH = ("Bluetooth non disponibile sul server: controlla che il servizio "
                    "bluetooth sia attivo e l'adattatore acceso (hciconfig).")


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


def riepilogo(data_dir, now: datetime | None = None,
              pin_richiesto: bool = False, ble_disponibile: bool = False) -> dict:
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
        "analisi_sale": tr.analisi(_num(ultima["rigenerazioni_al_giorno_30g"])
                                   if ultima else None),
        "rabbocchi": carica_rabbocchi(tr.dir)[-RABBOCCHI_MOSTRATI:],
        "capacita_kg": (tr.state["bc"]["capacita_g"] / 1000) if tr.state.get("bc") else None,
        "pin_richiesto": pin_richiesto,
        "ble_disponibile": ble_disponibile,
        "config": carica_config(tr.dir),
        "totali": {"litri": tr.state["litri_totali"],
                   "rigenerazioni": tr.state["rigenerazioni_totali"],
                   "blackout": tr.state["blackout_totali"]},
        "media_litri_giorno_7g": round(sum(complete) / len(complete)) if complete else None,
        "sale": sale, "giorni": giorni, "ore": ore,
    }


def parse_rabbocco(payload: dict) -> dict:
    """Valida il JSON del modulo -> argomenti di registra_rabbocco."""
    if not isinstance(payload, dict):
        raise ValueError("richiesta non valida")

    def numero(chiave):
        v = payload.get(chiave)
        if v in (None, ""):
            return None
        try:
            return float(str(v).replace(",", "."))
        except ValueError:
            raise ValueError(f"valore non numerico: {chiave}")

    pieno = payload.get("pieno", True) is not False
    kg_dopo = None if pieno else numero("kg_dopo")
    if not pieno and kg_dopo is None:
        raise ValueError("indica i kg nel serbatoio dopo il rabbocco")
    return {"kg_dopo": kg_dopo, "kg_aggiunti": numero("kg_aggiunti"),
            "livello_prima": payload.get("livello_prima") or "sconosciuto"}


class _ErroreBle(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    data_dir = "~/bwt-data"
    pin: str | None = None
    ble = None      # PonteBle quando il server gira con `bwt_perla.py serve`

    def _invia(self, codice, tipo, corpo: bytes, cache="no-store"):
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._invia(200, "text/html; charset=utf-8", INDEX.read_bytes())
        elif path == "/api/riepilogo":
            corpo = json.dumps(riepilogo(self.data_dir, pin_richiesto=bool(self.pin),
                                         ble_disponibile=self.ble is not None)).encode()
            self._invia(200, "application/json", corpo)
        elif path == "/healthz":
            self._invia(200, "text/plain", b"ok")
        elif path in STATICI:
            nome, tipo = STATICI[path]
            self._invia(200, tipo, (WEB / nome).read_bytes(), "public, max-age=86400")
        else:
            self._invia(404, "text/plain", b"non trovato")

    def _json(self, codice, oggetto):
        self._invia(codice, "application/json", json.dumps(oggetto).encode())

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/rabbocco", "/api/rabbocco/annulla", "/api/scan",
                        "/api/verifica", "/api/config"):
            return self._json(404, {"errore": "non trovato"})
        # difese minime contro richieste da altre pagine web: tipo JSON
        # (un modulo di un altro sito non puo' inviarlo senza permesso CORS)
        # e Origin uguale all'host a cui ci si e' collegati
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._json(415, {"errore": "serve application/json"})
        origine = self.headers.get("Origin")
        if origine and urlparse(origine).netloc != self.headers.get("Host"):
            return self._json(403, {"errore": "origine non consentita"})
        try:
            lung = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            lung = -1
        if not 0 < lung <= MAX_BODY:
            return self._json(400, {"errore": "corpo non valido"})
        try:
            payload = json.loads(self.rfile.read(lung))
            if self.pin and not (isinstance(payload, dict) and hmac.compare_digest(
                    str(payload.get("pin", "")).encode(), self.pin.encode())):
                return self._json(403, {"errore": "PIN errato"})
            if not isinstance(payload, dict):
                raise ValueError("richiesta non valida")
            if path in ("/api/scan", "/api/verifica") and self.ble is None:
                return self._json(501, {"errore": "scansione disponibile solo con "
                                                  "«bwt_perla.py serve»"})
            if path == "/api/scan":
                return self._json(200, {"dispositivi": self._ble(self.ble.scan)})
            if path == "/api/verifica":
                mac = valida_mac(payload.get("mac"))
                return self._json(200, {"dispositivo": self._ble(self.ble.verifica, mac)})
            if path == "/api/config":
                mac = valida_mac(payload.get("mac"))
                cfg = (self.ble.salva_mac(mac) if self.ble is not None
                       else salva_config(self.data_dir, mac=mac))
                return self._json(200, {"ok": True, "config": cfg})
            if path.endswith("/annulla"):
                return self._json(200, {"ok": True,
                                        "annullato": annulla_ultimo_rabbocco(self.data_dir)})
            rec = registra_rabbocco(self.data_dir, **parse_rabbocco(payload))
            return self._json(200, {"ok": True, "rabbocco": rec})
        except (ValueError, TypeError) as e:
            return self._json(400, {"errore": str(e)})
        except _ErroreBle as e:
            return self._json(502, {"errore": str(e)})

    @staticmethod
    def _ble(funzione, *argomenti):
        """Chiama il ponte Bluetooth: qualunque errore diventa un 502 leggibile."""
        try:
            return funzione(*argomenti)
        except FileNotFoundError as e:    # nessun servizio/adattatore Bluetooth
            raise _ErroreBle(MSG_NO_BLUETOOTH) from e
        except Exception as e:
            raise _ErroreBle(str(e) or e.__class__.__name__) from e

    def log_message(self, fmt, *args):   # niente rumore nel journal
        pass


def serve(host: str, port: int, data_dir: str, pin: str | None) -> None:
    Handler.data_dir = data_dir
    Handler.pin = pin or None
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"In ascolto su http://{host}:{port}  (dati: {data_dir})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description="Pagina web BWT Perla")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data-dir", default="~/bwt-data")
    ap.add_argument("--pin", default=None,
                    help="PIN richiesto per registrare/annullare un rabbocco")
    a = ap.parse_args()
    serve(a.host, a.port, a.data_dir, a.pin)


if __name__ == "__main__":
    main()
