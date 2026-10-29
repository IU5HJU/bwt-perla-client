"""Storico dell'apparecchio: calibrazione delle date e analisi, senza BLE.

L'apparecchio conserva i litri in due registri circolari: 30 giorni a quarti
d'ora (al litro) e fino a ~5 anni a giorni interi (a multipli di 10 litri). Il
giorno dell'apparecchio non comincia a mezzanotte ma a un'ora sua, e non ha un
orologio affidabile: per datare i giorni si confrontano gli ultimi 30 giorni nei
due registri. L'ora di cambio giorno e l'allineamento sono quelli per cui i
giorni interi coincidono meglio con la somma dei quarti d'ora.

Gli orari restano approssimati come quelli della registrazione normale (l'ultimo
quarto si assume finito all'ultimo multiplo di 15 minuti prima della lettura).

Licenza GPLv3, come il resto del progetto.
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

QUARTI_GIORNO = 96
# I litri dei giorni sono a multipli di 10: l'arrotondamento da' ~2,5 L di scarto
# medio anche con l'ipotesi giusta, con una dispersione di ~0,3 L. Ipotesi entro
# questa tolleranza dal minimo non sono distinguibili.
TOLLERANZA_SCARTO = 1.5
QUARTO = timedelta(minutes=15)
GIORNI_SETTIMANA = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
GIORNI_COLONNE = ["indice", "inizio_slot", "data", "litri", "rigenerazioni", "blackout"]
QUARTI_COLONNE = ["indice", "inizio_approx", "litri", "rigenerazione", "blackout"]


def calibra_rollover(giorni_litri: list[int], quarti_litri: list[int],
                     max_giorni: int = 29):
    """Cerca a che quarto d'ora si e' chiuso l'ultimo giorno dell'apparecchio.

    giorni_litri e quarti_litri sono in ordine cronologico; l'ultimo giorno e'
    l'ultimo chiuso. Per ogni ipotesi `e0` (numero di quarti che lo precedono,
    tra len-95 e len) si confronta ogni giorno con la somma dei 96 quarti della
    sua finestra e si tiene l'ipotesi con lo scarto medio minore.
    -> (e0, scarto_medio_litri, giorni_confrontati, scarto_mediano_delle_ipotesi,
    (e0_min, e0_max)) oppure None.
    Se piu' ipotesi sono indistinguibili (scarto entro TOLLERANZA_SCARTO dal
    minimo: cambio giorno in piena notte, quando non si usa acqua, o con soli
    consumi minimi) e0 e' il centro dell'intervallo continuo attorno alla
    migliore e l'ultimo valore ne da gli estremi: l'ora esatta non e'
    ricavabile, ma le date dei giorni non cambiano.
    Un scarto mediano molto piu' alto del minimo indica che l'ipotesi si
    distingue bene dalle altre; se sono vicini la calibrazione e' incerta (per
    esempio giorni quasi tutti a zero).
    """
    n = len(quarti_litri)
    if n < QUARTI_GIORNO * 2 or not giorni_litri:
        return None
    pref = [0]
    for q in quarti_litri:
        pref.append(pref[-1] + q)
    scarti = {}
    cnt = 0
    for e0 in range(n - QUARTI_GIORNO + 1, n + 1):
        err = c = 0
        for k in range(min(max_giorni, len(giorni_litri))):
            fine = e0 - QUARTI_GIORNO * k
            ini = fine - QUARTI_GIORNO
            if ini < 0:
                break
            err += abs((pref[fine] - pref[ini]) - giorni_litri[-1 - k])
            c += 1
        if c >= 3:
            scarti[e0] = err / c
            cnt = c
    if not scarti:
        return None
    migliore = min(scarti, key=lambda e: (scarti[e], e))
    minimo = scarti[migliore]

    def ok(e):
        return e in scarti and scarti[e] - minimo <= TOLLERANZA_SCARTO

    lo = hi = migliore
    while ok(lo - 1):
        lo -= 1
    while ok(hi + 1):
        hi += 1
    return ((lo + hi) // 2, minimo, cnt, statistics.median(scarti.values()), (lo, hi))


def etichetta_giorni(giorni: list[dict], quarti_n: int, e0: int,
                     letto_il: datetime) -> list[dict]:
    """Aggiunge a ogni giorno (cronologico) l'inizio dello slot e la data.

    L'ultimo giorno si e' chiuso e0 quarti dopo l'inizio dei quarti letti; ogni
    giorno dura 24 ore. La data e' quella dell'inizio dello slot (dove cade la
    sera: la doccia delle 22:30 sta nel giorno in cui e' stata fatta).
    """
    fine_quarti = letto_il.replace(minute=letto_il.minute - letto_il.minute % 15,
                                   second=0, microsecond=0)
    chiusura = fine_quarti - QUARTO * (quarti_n - e0)
    n = len(giorni)
    righe = []
    for j, g in enumerate(giorni):
        inizio = chiusura - timedelta(days=n - j)
        righe.append({**g, "inizio_slot": inizio.isoformat(timespec="minutes"),
                      "data": inizio.date().isoformat()})
    return righe


def etichetta_quarti(quarti: list[dict], letto_il: datetime) -> list[dict]:
    fine = letto_il.replace(minute=letto_il.minute - letto_il.minute % 15,
                            second=0, microsecond=0)
    n = len(quarti)
    return [{**q, "inizio_approx": (fine - QUARTO * (n - j)).isoformat(timespec="minutes")}
            for j, q in enumerate(quarti)]


def riepilogo(righe: list[dict]) -> dict:
    """Statistiche sui giorni etichettati (lista di dict con data e litri)."""
    if not righe:
        return {}
    per_gs = defaultdict(list)
    for r in righe:
        per_gs[datetime.fromisoformat(r["data"]).weekday()].append(r["litri"])
    litri = [r["litri"] for r in righe]
    rig = sum(r["rigenerazioni"] for r in righe)

    def media(v):
        return round(statistics.mean(v), 1) if v else None

    return {
        "giorni": len(righe), "da": righe[0]["data"], "a": righe[-1]["data"],
        "media_litri": media(litri),
        "mediana_litri": statistics.median(litri),
        "giorni_a_zero": sum(1 for v in litri if v == 0),
        "rigenerazioni": rig,
        "rigenerazioni_al_giorno": round(rig / len(righe), 3),
        "per_giorno_settimana": {GIORNI_SETTIMANA[i]: {"giorni": len(v), "media": media(v)}
                                 for i, v in sorted(per_gs.items())},
    }


def scrivi_csv(path: Path, colonne: list[str], righe: list[dict]) -> None:
    with Path(path).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonne, extrasaction="ignore")
        w.writeheader()
        w.writerows(righe)
