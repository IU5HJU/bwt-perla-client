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


def riepilogo(righe: list[dict], dal: str | None = None) -> dict:
    """Statistiche sui giorni etichettati (dict con data AAAA-MM-GG, litri...).

    `dal`: data di messa in servizio. I giorni precedenti non hanno dati reali
    (l'apparecchio non era installato: sono a zero perche' non c'era nulla da
    registrare) e vengono esclusi, ma contati in `giorni_esclusi`."""
    esclusi = 0
    if dal:
        esclusi = sum(1 for r in righe if r["data"] < dal)
        righe = [r for r in righe if r["data"] >= dal]
    if not righe:
        return {"giorni_esclusi": esclusi} if esclusi else {}

    def media(v):
        return round(statistics.mean(v), 1) if v else None

    per_gs, per_mese, per_anno = defaultdict(list), defaultdict(list), defaultdict(list)
    per_anno_gs = defaultdict(lambda: defaultdict(list))
    for r in righe:
        d = datetime.fromisoformat(r["data"])
        per_gs[d.weekday()].append(r["litri"])
        per_mese[d.month].append(r["litri"])
        per_anno[d.year].append(r)
        per_anno_gs[d.year][d.weekday()].append(r["litri"])
    litri = [r["litri"] for r in righe]
    con_consumo = [v for v in litri if v > 0]
    rig = sum(r["rigenerazioni"] for r in righe)
    return {
        "giorni": len(righe), "giorni_esclusi": esclusi,
        "da": righe[0]["data"], "a": righe[-1]["data"],
        "media_litri": media(litri),
        "mediana_litri": statistics.median(litri),
        "media_giorni_con_consumo": media(con_consumo),
        "giorni_a_zero": len(litri) - len(con_consumo),
        "rigenerazioni": rig,
        "rigenerazioni_al_giorno": round(rig / len(righe), 3),
        "giorni_per_rigenerazione": round(len(righe) / rig, 1) if rig else None,
        "per_giorno_settimana": {GIORNI_SETTIMANA[i]: {"giorni": len(v), "media": media(v)}
                                 for i, v in sorted(per_gs.items())},
        "per_mese_dell_anno": {m: {"giorni": len(v), "media": media(v)}
                               for m, v in sorted(per_mese.items())},
        # se la datazione dei giorni fosse sfalsata, il picco del fine settimana
        # non starebbe su sab/dom in tutti gli anni: serve a controllarla
        "per_anno_giorno_settimana": {a: {GIORNI_SETTIMANA[i]: media(v) for i, v in sorted(g.items())}
                                      for a, g in sorted(per_anno_gs.items())},
        "giorni_con_piu_rigenerazioni": [{"data": r["data"], "litri": r["litri"],
                                          "rigenerazioni": r["rigenerazioni"]}
                                         for r in righe if r["rigenerazioni"] >= 2],
        "giorni_blackout": sum(1 for r in righe if r.get("blackout")),
        "per_anno": {a: {"giorni": len(v), "media": media([r["litri"] for r in v]),
                         "giorni_a_zero": sum(1 for r in v if r["litri"] == 0),
                         "rigenerazioni": sum(r["rigenerazioni"] for r in v)}
                     for a, v in sorted(per_anno.items())},
    }


def profilo_orario(quarti: list[dict]) -> dict:
    """Litri medi per ora del giorno, feriali (lun-ven) e fine settimana, sui
    soli giorni con tutti i 96 quarti. Gli orari sono approssimati (vedi sopra)."""
    ore = defaultdict(lambda: [0.0] * 24)
    conteggio = defaultdict(int)
    for q in quarti:
        t = datetime.fromisoformat(q["inizio_approx"])
        ore[t.date()][t.hour] += q["litri"]
        conteggio[t.date()] += 1
    gruppi = {"feriali": [], "weekend": []}
    for d in sorted(ore):
        if conteggio[d] == QUARTI_GIORNO:
            gruppi["feriali" if d.weekday() < 5 else "weekend"].append(ore[d])
    out = {"giorni": {k: len(v) for k, v in gruppi.items()}}
    for k, v in gruppi.items():
        out[k] = [round(statistics.mean(c[h] for c in v), 1) if v else None for h in range(24)]
    return out


def carica_giorni(path) -> list[dict]:
    with Path(path).open(newline="") as f:
        return [{**r, "litri": int(r["litri"]), "rigenerazioni": int(r["rigenerazioni"]),
                 "blackout": int(r["blackout"])} for r in csv.DictReader(f)]


def carica_quarti(path) -> list[dict]:
    with Path(path).open(newline="") as f:
        return [{**r, "litri": int(r["litri"])} for r in csv.DictReader(f)]


def righe_riepilogo(r: dict, profilo: dict | None = None) -> list[str]:
    """Testo del riepilogo, una riga per elemento."""
    if not r:
        return ["Nessun giorno da analizzare."]
    out = []
    if r["giorni_esclusi"]:
        out.append(f"Esclusi {r['giorni_esclusi']} giorni precedenti alla messa in servizio.")
    out.append(f"{r['giorni']} giorni in servizio, dal {r['da']} al {r['a']}: media "
               f"{r['media_litri']} L/giorno (mediana {r['mediana_litri']}), "
               f"{r['media_giorni_con_consumo']} L sui soli giorni con consumo; "
               f"{r['giorni_a_zero']} giorni a zero.")
    if r["rigenerazioni"]:
        out.append(f"{r['rigenerazioni']} rigenerazioni: {r['rigenerazioni_al_giorno']} al giorno, "
                   f"una ogni {r['giorni_per_rigenerazione']} giorni.")
    out.append("Media per giorno della settimana:")
    out += [f"  {n}: {v['media']} L ({v['giorni']} giorni)" for n, v in r["per_giorno_settimana"].items()]
    out.append("Per anno (giorni, media L/giorno, giorni a zero, rigenerazioni):")
    out += [f"  {a}: {v['giorni']:4d} gg  {v['media']:6} L  {v['giorni_a_zero']:3d} a zero  "
            f"{v['rigenerazioni']:3d} rigen." for a, v in r["per_anno"].items()]
    out.append("Media per giorno della settimana, anno per anno (il picco del fine settimana "
               "deve stare su sab/dom in tutti gli anni, altrimenti le date sono sfalsate):")
    out.append("        " + "".join(f"{n:>7}" for n in GIORNI_SETTIMANA))
    for a, v in r["per_anno_giorno_settimana"].items():
        out.append(f"  {a}  " + "".join(f"{v.get(n, 0):7.0f}" for n in GIORNI_SETTIMANA))
    if r["giorni_con_piu_rigenerazioni"]:
        out.append("Giorni con 2 o piu' rigenerazioni (interventi, prove): " + "; ".join(
            f"{g['data']} ({g['rigenerazioni']} rigen., {g['litri']} L)"
            for g in r["giorni_con_piu_rigenerazioni"]))
    out.append(f"Giorni con interruzione di corrente: {r['giorni_blackout']}.")
    mesi = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")
    out.append("Per mese dell'anno (media L/giorno su tutti gli anni):")
    out.append("  " + "  ".join(f"{mesi[m - 1]} {v['media']:.0f}" for m, v in r["per_mese_dell_anno"].items()))
    if profilo and (profilo["giorni"]["feriali"] or profilo["giorni"]["weekend"]):
        g = profilo["giorni"]
        out.append(f"Profilo orario, litri medi per ora ({g['feriali']} giorni feriali, "
                   f"{g['weekend']} di fine settimana; orari approssimati):")
        out.append("  ora   feriali  weekend")
        for h in range(24):
            f, w = profilo["feriali"][h], profilo["weekend"][h]
            barra = "#" * round((f or 0) / 2)
            out.append(f"  {h:02d}   {'-' if f is None else f:>7}  {'-' if w is None else w:>7}  {barra}")
    return out


def scrivi_csv(path: Path, colonne: list[str], righe: list[dict]) -> None:
    with Path(path).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonne, extrasaction="ignore")
        w.writeheader()
        w.writerows(righe)
