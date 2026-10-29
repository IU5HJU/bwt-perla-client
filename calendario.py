"""Calendario italiano (feriali, sabati, domeniche, festivi, ponti) e correlazione
con i consumi giornalieri dell'addolcitore. Solo libreria standard, senza internet.

Le festivita' nazionali sono regole fisse piu' la Pasquetta (lunedi' dopo Pasqua,
con la formula gregoriana). Il patrono della propria citta' non e' noto: si puo'
indicare con `patrono="MM-GG"`.

Licenza GPLv3, come il resto del progetto.
"""
from __future__ import annotations

import bisect
import statistics
from datetime import date, timedelta

FISSE = ((1, 1, "Capodanno"), (1, 6, "Epifania"), (4, 25, "Liberazione"),
         (5, 1, "Festa del lavoro"), (6, 2, "Festa della Repubblica"),
         (8, 15, "Ferragosto"), (11, 1, "Ognissanti"), (12, 8, "Immacolata"),
         (12, 25, "Natale"), (12, 26, "Santo Stefano"))
CATEGORIE = ("feriale", "ponte probabile", "festivo infrasettimanale", "sabato", "domenica")
NON_LAVORATIVE = ("festivo infrasettimanale", "sabato", "domenica")


def pasqua(anno: int) -> date:
    """Domenica di Pasqua (algoritmo gregoriano anonimo)."""
    a, b, c = anno % 19, anno // 100, anno % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mese = (h + l - 7 * m + 114) // 31
    giorno = (h + l - 7 * m + 114) % 31 + 1
    return date(anno, mese, giorno)


def parse_patrono(patrono: str | None):
    """"MM-GG" -> (mese, giorno); ValueError se non e' una data valida."""
    if not patrono:
        return None
    try:
        m, g = (int(x) for x in patrono.split("-"))
        date(2024, m, g)                              # anno bisestile: accetta anche 29-02
    except (ValueError, TypeError):
        raise ValueError("il patrono va indicato come MM-GG (per esempio 12-07)")
    return m, g


class Calendario:
    def __init__(self, patrono: str | None = None):
        self.patrono = parse_patrono(patrono)
        self._cache: dict[int, dict[date, str]] = {}

    def festivi(self, anno: int) -> dict[date, str]:
        if anno not in self._cache:
            f = {date(anno, m, g): nome for m, g, nome in FISSE}
            p = pasqua(anno)
            f[p] = "Pasqua"
            f[p + timedelta(days=1)] = "Pasquetta"
            if self.patrono:
                try:
                    f[date(anno, *self.patrono)] = "Patrono"
                except ValueError:                    # 29 febbraio negli anni non bisestili
                    pass
            self._cache[anno] = f
        return self._cache[anno]

    def festivo(self, d: date) -> str | None:
        return self.festivi(d.year).get(d)

    def non_lavorativo(self, d: date) -> bool:
        return d.weekday() >= 5 or self.festivo(d) is not None

    def categoria(self, d: date) -> str:
        if d.weekday() == 5:
            return "sabato"
        if d.weekday() == 6:
            return "domenica"
        if self.festivo(d):
            return "festivo infrasettimanale"
        # un feriale incastrato tra due giorni non lavorativi e' un possibile ponte
        if self.non_lavorativo(d - timedelta(days=1)) and self.non_lavorativo(d + timedelta(days=1)):
            return "ponte probabile"
        return "feriale"


def auc(alti: list[float], bassi: list[float]) -> float:
    """Probabilita' che un valore di `alti` superi uno di `bassi` (a pari merito
    mezzo punto): 0,5 = nessuna separazione, 1 = separazione perfetta."""
    if not alti or not bassi:
        return float("nan")
    b = sorted(bassi)
    tot = 0.0
    for x in alti:
        lo, hi = bisect.bisect_left(b, x), bisect.bisect_right(b, x)
        tot += lo + 0.5 * (hi - lo)
    return tot / (len(alti) * len(b))


def soglia_migliore(non_lav: list[float], lav: list[float]):
    """Soglia sui litri (non lavorativo se litri >= soglia) con la migliore
    accuratezza bilanciata. -> (soglia, accuratezza_bilanciata, accuratezza)."""
    if not non_lav or not lav:
        return None
    migliore = None
    for t in sorted(set(non_lav) | set(lav)):
        tpr = sum(1 for v in non_lav if v >= t) / len(non_lav)
        tnr = sum(1 for v in lav if v < t) / len(lav)
        bil = (tpr + tnr) / 2
        if migliore is None or bil > migliore[1]:
            acc = (sum(1 for v in non_lav if v >= t) + sum(1 for v in lav if v < t)) / (len(non_lav) + len(lav))
            migliore = (t, bil, acc)
    return migliore


def correlazione(giorni: list[dict], dal: str | None = None, patrono: str | None = None) -> dict:
    """Confronta i consumi con il calendario. `giorni`: dict con data AAAA-MM-GG e litri.

    I giorni a zero (assenze) sono contati a parte e non entrano nel confronto: un
    giorno senza consumo non dice se era lavorativo."""
    cal = Calendario(patrono)
    if dal:
        giorni = [g for g in giorni if g["data"] >= dal]
    per_cat: dict[str, list[int]] = {c: [] for c in CATEGORIE}
    zeri = {c: 0 for c in CATEGORIE}
    righe = []
    for g in giorni:
        d = date.fromisoformat(g["data"])
        c = cal.categoria(d)
        righe.append((d, c, g["litri"]))
        if g["litri"] == 0:
            zeri[c] += 1
        else:
            per_cat[c].append(g["litri"])

    def stat(v):
        return {"media": round(statistics.mean(v), 1), "mediana": statistics.median(v),
                "dev_std": round(statistics.pstdev(v), 1)} if v else {"media": None, "mediana": None, "dev_std": None}

    categorie = {c: {"giorni": len(per_cat[c]) + zeri[c], "con_consumo": len(per_cat[c]),
                     "a_zero": zeri[c], **stat(per_cat[c])} for c in CATEGORIE}
    lav = per_cat["feriale"]
    non_lav = [v for c in NON_LAVORATIVE for v in per_cat[c]]
    sep = soglia_migliore(non_lav, lav)
    # giorni "dalla parte sbagliata": feriali con consumi da festivo e viceversa
    esempi_fer, esempi_nl = [], []
    if len(non_lav) > 10 and len(lav) > 10:
        p90_nl = statistics.quantiles(non_lav, n=10)[8]      # sopra il 90% dei non lavorativi
        p10_lav = statistics.quantiles(lav, n=10)[0]         # sotto il 10% dei feriali
        esempi_fer = sorted(((v, d) for d, c, v in righe if c == "feriale" and v >= p90_nl),
                            reverse=True)[:6]
        esempi_nl = sorted((v, d) for d, c, v in righe
                           if c in NON_LAVORATIVE and 0 < v <= p10_lav)[:6]
    return {
        "giorni": len(righe), "da": giorni[0]["data"] if righe else None,
        "a": giorni[-1]["data"] if righe else None,
        "categorie": categorie,
        "auc": None if not (lav and non_lav) else round(auc(non_lav, lav), 3),
        "soglia": None if not sep else {"litri": sep[0], "accuratezza_bilanciata": round(sep[1], 3),
                                        "accuratezza": round(sep[2], 3)},
        "n_lavorativi": len(lav), "n_non_lavorativi": len(non_lav),
        "feriali_da_festivo": [(d.isoformat(), v) for v, d in esempi_fer],
        "festivi_da_feriale": [(d.isoformat(), v) for v, d in esempi_nl],
    }


def righe_correlazione(r: dict) -> list[str]:
    def it(iso):
        return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"

    if not r.get("giorni"):
        return ["Nessun giorno da analizzare."]
    out = [f"Calendario italiano su {r['giorni']} giorni, dal {it(r['da'])} al {it(r['a'])}.",
           f"  {'categoria':<26}{'giorni':>7}{'a zero':>8}{'media L':>9}{'mediana':>9}{'dev.std':>9}"]
    for c, v in r["categorie"].items():
        if v["giorni"]:
            m = "-" if v["media"] is None else v["media"]
            md = "-" if v["mediana"] is None else v["mediana"]
            ds = "-" if v["dev_std"] is None else v["dev_std"]
            out.append(f"  {c:<26}{v['giorni']:>7}{v['a_zero']:>8}{m:>9}{md:>9}{ds:>9}")
    if r["auc"] is None:
        out.append("Troppo pochi giorni per confrontare lavorativi e non lavorativi.")
        return out
    out.append(f"Separazione lavorativi / non lavorativi ({r['n_lavorativi']} contro "
               f"{r['n_non_lavorativi']} giorni con consumo):")
    out.append(f"  AUC {r['auc']:.2f} (0,50 = nessuna, 1,00 = perfetta); migliore soglia {r['soglia']['litri']} L: "
               f"accuratezza {100 * r['soglia']['accuratezza']:.0f}% "
               f"(bilanciata {100 * r['soglia']['accuratezza_bilanciata']:.0f}%).")
    if r["feriali_da_festivo"]:
        out.append("Feriali con consumo da festivo (a casa? ferie? smart working?): " + "; ".join(
            f"{it(d)} {v} L" for d, v in r["feriali_da_festivo"]))
    if r["festivi_da_feriale"]:
        out.append("Non lavorativi con consumo da feriale (fuori casa?): " + "; ".join(
            f"{it(d)} {v} L" for d, v in r["festivi_da_feriale"]))
    return out
