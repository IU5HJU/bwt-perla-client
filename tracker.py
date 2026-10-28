"""Registrazione su CSV e stima del sale, senza dipendenze da BLE.

L'addolcitore del modello in uso riporta sempre 0 g di sale (come la vecchia
app): il residuo e' quindi stimato come
    sale messo - rigenerazioni dal rabbocco * g per rigenerazione.

Licenza GPLv3, come il resto del progetto.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

QUARTI_SIZE = 2880
QUARTO = timedelta(minutes=15)

CICLI_CSV = "cicli.csv"
QUARTI_CSV = "quarti.csv"
STATE_JSON = "state.json"

CICLI_COLONNE = [
    "timestamp", "idx_quarti", "idx_giorni", "broadcast_hex",
    "sale_dispositivo_g", "litri_nuovi", "litri_totali",
    "rigenerazioni_nuove", "rigenerazioni_totali", "blackout_nuovi",
    "sale_stimato_g", "sale_stimato_pct", "rigenerazioni_al_giorno_30g",
    "autonomia_giorni",
]
QUARTI_COLONNE = ["inizio_approx", "idx", "litri", "rigenerazione", "blackout"]


def _stato_iniziale() -> dict:
    return {
        "last_idx": None, "prev_rege": False, "prev_black": False,
        "litri_totali": 0, "rigenerazioni_totali": 0, "blackout_totali": 0,
        "rabbocco": None,
    }


def _scrivi_righe(path: Path, colonne: list[str], righe: list[dict]) -> None:
    nuovo = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonne)
        if nuovo:
            w.writeheader()
        w.writerows(righe)


class Tracker:
    def __init__(self, data_dir):
        self.dir = Path(data_dir).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / STATE_JSON
        self.state = _stato_iniziale()
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text()))

    def _salva(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.state_path)

    # ── rabbocco ────────────────────────────────────────────────────

    def rabbocco(self, kg: float, rigen_gia_usate: int = 0, now=None) -> dict:
        """Registra un rabbocco. `rigen_gia_usate`: rigenerazioni gia'
        avvenute dal rabbocco reale, se questo e' stato fatto prima."""
        now = now or datetime.now()
        self.state["rabbocco"] = {
            "data": now.isoformat(timespec="seconds"),
            "kg": kg,
            "rigenerazioni_al_rabbocco":
                self.state["rigenerazioni_totali"] - rigen_gia_usate,
        }
        self._salva()
        return self.state["rabbocco"]

    # ── ciclo di lettura ────────────────────────────────────────────

    def quarti_da_leggere(self, idx_quarti: int) -> int:
        """Quante voci nuove ci sono dall'ultima lettura (0 al primo avvio:
        si parte da adesso, senza ricostruire il passato)."""
        last = self.state["last_idx"]
        return 0 if last is None else (idx_quarti - last) % QUARTI_SIZE

    def applica(self, now: datetime, bc: dict, raw: bytes,
                quarti: list[dict], giorni: list[dict]) -> dict:
        s = self.state
        if s["last_idx"] is None:
            quarti = []     # primo avvio: nessun conteggio retroattivo
        litri = rigen = black = 0
        prev_r, prev_b = s["prev_rege"], s["prev_black"]
        for q in quarti:
            litri += q["litri"]
            rigen += int(q["rigen"] and not prev_r)
            black += int(q["blackout"] and not prev_b)
            prev_r, prev_b = q["rigen"], q["blackout"]
        s["prev_rege"], s["prev_black"] = prev_r, prev_b
        s["litri_totali"] += litri
        s["rigenerazioni_totali"] += rigen
        s["blackout_totali"] += black
        s["last_idx"] = bc["idx_quarti"]

        # Inizio approssimato dei quarti: l'orologio dell'apparecchio non e'
        # noto, quindi si assume che l'ultimo quarto sia finito all'ultimo
        # multiplo di 15 minuti.
        fine = now.replace(minute=now.minute - now.minute % 15,
                           second=0, microsecond=0)
        n = len(quarti)
        righe_q = []
        for j, q in enumerate(quarti):
            inizio = fine - QUARTO * (n - j)
            righe_q.append({
                "inizio_approx": inizio.isoformat(timespec="minutes"),
                "idx": (bc["idx_quarti"] - n + j) % QUARTI_SIZE,
                "litri": q["litri"], "rigenerazione": int(q["rigen"]),
                "blackout": int(q["blackout"]),
            })
        if righe_q:
            _scrivi_righe(self.dir / QUARTI_CSV, QUARTI_COLONNE, righe_q)

        # frequenza media delle rigenerazioni sui giorni letti
        rate = (sum(g["rigen"] for g in giorni) / len(giorni)) if giorni else None
        sale_g, pct, autonomia = self.stima_sale(bc, rate)

        riga = {
            "timestamp": now.isoformat(timespec="seconds"),
            "idx_quarti": bc["idx_quarti"], "idx_giorni": bc["idx_giorni"],
            "broadcast_hex": raw.hex(" "),
            "sale_dispositivo_g": bc["sale_g"],
            "litri_nuovi": litri, "litri_totali": s["litri_totali"],
            "rigenerazioni_nuove": rigen,
            "rigenerazioni_totali": s["rigenerazioni_totali"],
            "blackout_nuovi": black,
            "sale_stimato_g": "" if sale_g is None else sale_g,
            "sale_stimato_pct": "" if pct is None else pct,
            "rigenerazioni_al_giorno_30g": "" if rate is None else round(rate, 3),
            "autonomia_giorni": "" if autonomia is None else autonomia,
        }
        _scrivi_righe(self.dir / CICLI_CSV, CICLI_COLONNE, [riga])
        self._salva()
        return riga

    def stima_sale(self, bc: dict, rate: float | None):
        """-> (grammi, percentuale, giorni di autonomia); None se nessun rabbocco."""
        r = self.state["rabbocco"]
        if not r:
            return None, None, None
        usate = self.state["rigenerazioni_totali"] - r["rigenerazioni_al_rabbocco"]
        g_rege = bc["sale_per_rigenerazione_g"]
        sale = max(0, int(r["kg"] * 1000 - usate * g_rege))
        capa = bc["capacita_g"] or int(r["kg"] * 1000)
        pct = min(100, round(sale * 100 / capa)) if capa else None
        autonomia = None
        if rate and g_rege:
            autonomia = round(sale / (rate * g_rege))
        return sale, pct, autonomia

    def ultima_riga(self) -> dict | None:
        p = self.dir / CICLI_CSV
        if not p.exists():
            return None
        with p.open(newline="") as f:
            righe = list(csv.DictReader(f))
        return righe[-1] if righe else None
