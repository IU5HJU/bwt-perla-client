"""Indizi di perdita dai consumi (SPERIMENTALE).

L'apparecchio registra i litri a quarti d'ora (ultimi 30 giorni) e a giorni
(ultimi 5 anni, in passi da 10 L). Una perdita non e' un'ora: e' un filo
d'acqua che non si ferma mai, e quindi si vede dove la casa dovrebbe essere
ferma. Tre indizi, tutti con soglie dichiarate qui sotto:

* notte: tra le 01:00 e le 05:00 quanti quarti d'ora hanno consumo (di solito
  pochi o nessuno);
* silenzio: la striscia piu' lunga di quarti d'ora a zero nella giornata (con una
  perdita si accorcia, perche' ogni goccia la interrompe);
* pavimento: il consumo minimo di una finestra di 14 giorni, sui giorni
  senza rigenerazione (con una perdita sale e non torna piu' giu').

Sono indizi, non una diagnosi: a quarti d'ora e in litri interi una perdita
piccola (meno di ~1 L/h) si confonde con il rumore, e una notte con il
bagno, il frigorifero con dispenser o l'irrigazione automatica sembra una
perdita. Gli orari sono approssimati (vedi storico.etichetta_quarti): la
finestra notturna e' larga apposta.
"""
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import storico

NOTTE_DA, NOTTE_A = 1, 5                       # ore 01:00-05:00
QUARTI_NOTTE = (NOTTE_A - NOTTE_DA) * 4        # 16
SOGLIA_SOSPETTA = 8                            # quarti attivi su 16: metà della notte
SOGLIA_CONTINUA = 12                           # tre quarti della notte
FINESTRA_PAVIMENTO = 14                        # giorni
MIN_FINESTRE = 6                               # storia minima per giudicare il pavimento
QUARTI_GIORNO = 96


def serie_con_esclusioni(live: list[dict], storico_q: list[dict]) -> list[tuple[datetime, int, bool]]:
    """(inizio, litri, escluso): escluso = rigenerazione o interruzione di corrente
    in quel quarto d'ora. I litri di una rigenerazione non sono un uso della casa."""
    def flag(righe):
        return [{**r, "litri": 1 if int(r.get("rigenerazione") or 0) or int(r.get("blackout") or 0) else 0}
                for r in righe]
    litri = storico.unisci_quarti(live, storico_q)
    esclusi = dict(storico.unisci_quarti(flag(live), flag(storico_q)))
    return [(t, l, bool(esclusi.get(t, 0))) for t, l in litri]


def per_giorno(serie) -> dict[date, dict[datetime, tuple[int, bool]]]:
    out = defaultdict(dict)
    for t, litri, escluso in serie:
        out[t.date()][t] = (litri, escluso)
    return out


def giudizio_notte(attivi: int) -> str:
    if attivi >= SOGLIA_CONTINUA:
        return "continuo"
    if attivi >= SOGLIA_SOSPETTA:
        return "sospetto"
    return "ok"


def silenzio_massimo(quarti: list[int]) -> int:
    """Quarti d'ora consecutivi a zero piu' lunghi (quarti in ordine di tempo)."""
    migliore = corrente = 0
    for litri in quarti:
        corrente = corrente + 1 if litri == 0 else 0
        migliore = max(migliore, corrente)
    return migliore


def notti(serie) -> list[dict]:
    """Una riga per ogni giorno con tutti i 96 quarti: la notte 01-05 e il silenzio."""
    righe = []
    for g, quarti in sorted(per_giorno(serie).items()):
        if len(quarti) != QUARTI_GIORNO:
            continue
        ordine = sorted(quarti.items())
        finestra = [(l, e) for t, (l, e) in ordine if NOTTE_DA <= t.hour < NOTTE_A]
        riga = {"data": g.isoformat(), "silenzio_q": silenzio_massimo([l for _, (l, _e) in ordine]),
                "litri_giorno": sum(l for _, (l, _e) in ordine)}
        if len(finestra) != QUARTI_NOTTE:
            continue
        if any(e for _, e in finestra):
            riga.update(scartata="rigenerazione o interruzione", litri=None, attivi=None, giudizio=None)
        else:
            attivi = sum(1 for l, _ in finestra if l > 0)
            riga.update(scartata=None, litri=sum(l for l, _ in finestra), attivi=attivi,
                        giudizio=giudizio_notte(attivi))
        righe.append(riga)
    return righe


def pavimento(giorni: list[dict], dal: str | None = None) -> dict:
    """Minimo di consumo per finestre di 14 giorni (senza rigenerazione, senza
    interruzioni di corrente, escluso l'ultimo giorno che puo' essere parziale)."""
    giorni = sorted(giorni, key=lambda r: r["data"])[:-1]
    if dal:
        giorni = [r for r in giorni if r["data"] >= dal]
    validi = [r for r in giorni if not r["rigenerazioni"] and not r["blackout"]]
    # finestre contigue di FINESTRA_PAVIMENTO giorni di calendario, dalla fine
    if not giorni:
        return {}
    fine = date.fromisoformat(giorni[-1]["data"])
    per_data = {r["data"]: r["litri"] for r in validi}
    finestre = []
    for k in range(len(giorni) // FINESTRA_PAVIMENTO):
        a = fine - timedelta(days=FINESTRA_PAVIMENTO * (k + 1) - 1)
        chiavi = [(a + timedelta(days=i)).isoformat() for i in range(FINESTRA_PAVIMENTO)]
        valori = [per_data[c] for c in chiavi if c in per_data]
        if len(valori) >= FINESTRA_PAVIMENTO // 2:
            finestre.append({"dal": a.isoformat(), "minimo": min(valori),
                             "zeri": sum(1 for v in valori if v == 0)})
    if not finestre:
        return {}
    ultima, storia = finestre[0], finestre[1:]
    minimi = [f["minimo"] for f in storia]
    r = {"ultima": ultima, "finestre": len(storia)}
    if len(storia) >= MIN_FINESTRE:
        r.update(mediana=statistics.median(minimi), massimo=max(minimi),
                 record=ultima["minimo"] > max(minimi) and ultima["minimo"] > 0)
    return r


def _ora(quarto: int) -> str:
    return f"{quarto // 4:02d}:{quarto % 4 * 15:02d}"


def silenzi_giorno(quarti: list[int], minimo: int = 4) -> list[tuple[int, int]]:
    """Strisce di almeno `minimo` quarti a zero, come (primo, ultimo+1)."""
    out, inizio = [], None
    for j, litri in enumerate(quarti + [1]):
        if litri == 0 and inizio is None:
            inizio = j
        elif litri != 0 and inizio is not None:
            if j - inizio >= minimo:
                out.append((inizio, j))
            inizio = None
    return out


def righe_giorno(serie, giorno: date) -> list[str]:
    quarti = per_giorno(serie).get(giorno)
    if not quarti:
        return [f"{giorno:%d/%m/%Y}: nessun quarto d'ora disponibile."]
    ordine = sorted(quarti.items())
    per_q = {t.hour * 4 + t.minute // 15: (l, e) for t, (l, e) in ordine}
    righe = [f"Giorno {giorno:%d/%m/%Y} ({len(ordine)} quarti su {QUARTI_GIORNO}); litri per quarto d'ora"
             " (R = rigenerazione o interruzione di corrente):",
             "  ora    :00   :15   :30   :45     tot"]
    for h in range(24):
        celle, tot = [], 0
        for k in range(4):
            if h * 4 + k in per_q:
                l, e = per_q[h * 4 + k]
                tot += l
                celle.append(f"{l:>4}{'R' if e else ' '}")
            else:
                celle.append("   - ")
        righe.append(f"  {h:02d}   " + " ".join(celle) + f"   {tot:>5}")
    if len(ordine) == QUARTI_GIORNO:
        liste = [per_q[j][0] for j in range(QUARTI_GIORNO)]
        righe.append(f"Totale: {sum(liste)} L. Strisce di almeno 1 ora senza consumo:")
        for a, b in silenzi_giorno(liste):
            righe.append(f"  {_ora(a)}-{_ora(b) if b < QUARTI_GIORNO else '24:00'}  "
                         f"({(b - a) * 15 // 60} h {(b - a) * 15 % 60:02d} min)")
    return righe


def valutazione(serie, giorni: list[dict] | None, dal: str | None, ultime: int = 14) -> dict:
    """Esito per la pagina web (stesse regole di `righe_analisi`).

    stato: "allarme" = le ultime 3 notti valide sono tutte anomale;
    "attenzione" = l'ultima notte valida e' anomala, oppure il pavimento dei consumi
    e' il piu' alto mai registrato; "ok"; "nd" = nessuna notte valida."""
    ns = notti(serie) if serie else []
    valide = [n for n in ns if n["scartata"] is None]
    p = pavimento(giorni, dal) if giorni else {}
    motivi = []
    if not valide:
        stato = "nd"
    elif len(valide) >= 3 and all(n["giudizio"] != "ok" for n in valide[-3:]):
        stato = "allarme"
        motivi.append("notti")
    else:
        stato = "ok"
        if valide[-1]["giudizio"] != "ok":
            stato = "attenzione"
            motivi.append("ultima_notte")
    if p.get("record") and stato in ("ok", "attenzione"):
        stato = "attenzione"
        motivi.append("pavimento")
    elif p.get("record"):
        motivi.append("pavimento")
    ultima = valide[-1] if valide else None
    return {
        "stato": stato, "motivi": motivi,
        "notti": [{"data": n["data"], "attivi": n["attivi"], "litri": n["litri"],
                   "giudizio": n["giudizio"], "scartata": n["scartata"]} for n in ns[-ultime:]],
        "notti_valide": len(valide),
        "ultima_attivi": ultima["attivi"] if ultima else None,
        "soglie": {"quarti": QUARTI_NOTTE, "sospetta": SOGLIA_SOSPETTA, "continua": SOGLIA_CONTINUA},
        "pavimento": ({"minimo": p["ultima"]["minimo"], "dal": p["ultima"]["dal"], "zeri": p["ultima"]["zeri"],
                       "mediana": p.get("mediana"), "massimo": p.get("massimo"), "record": p.get("record")}
                      if p else None),
    }


def righe_analisi(serie, giorni: list[dict] | None, dal: str | None, ultime: int = 14) -> list[str]:
    righe = ["Analisi perdite — SPERIMENTALE: sono indizi, non una diagnosi.",
             f"Notte = quarti d'ora con consumo tra le {NOTTE_DA:02d}:00 e le {NOTTE_A:02d}:00 "
             f"(su {QUARTI_NOTTE}); sospetta da {SOGLIA_SOSPETTA}, continua da {SOGLIA_CONTINUA}."]
    ns = notti(serie) if serie else []
    if not ns:
        righe.append("Quarti d'ora: nessun giorno completo disponibile "
                     "(serve 'storico' o qualche giorno di registrazione).")
    else:
        valide = [n for n in ns if n["scartata"] is None]
        righe.append(f"\nNotti analizzate: {len(valide)} (scartate {len(ns) - len(valide)} con rigenerazione "
                     "o interruzione di corrente).")
        if valide:
            conteggio = defaultdict(int)
            for n in valide:
                conteggio[n["giudizio"]] += 1
            righe.append(f"  ok: {conteggio['ok']}, sospette: {conteggio['sospetto']}, "
                         f"continue: {conteggio['continuo']}; mediana quarti attivi: "
                         f"{statistics.median(n['attivi'] for n in valide):g}")
        righe.append(f"\nUltime {min(ultime, len(ns))} notti:")
        righe.append("  data         notte: litri  quarti   giudizio   silenzio piu' lungo  totale giorno")
        for n in ns[-ultime:]:
            d = date.fromisoformat(n["data"])
            s = n["silenzio_q"]
            silenzio = f"{s * 15 // 60} h {s * 15 % 60:02d} min"
            if n["scartata"]:
                righe.append(f"  {d:%d/%m/%Y}   (scartata: {n['scartata']})    "
                             f"{silenzio:>20}   {n['litri_giorno']:>6} L")
            else:
                righe.append(f"  {d:%d/%m/%Y}   {n['litri']:>11} {n['attivi']:>3}/{QUARTI_NOTTE}  "
                             f"{n['giudizio']:>9}   {silenzio:>18}   {n['litri_giorno']:>6} L")
        ultime3 = [n for n in valide[-3:]]
        if len(ultime3) == 3 and all(n["giudizio"] != "ok" for n in ultime3):
            righe.append("\nATTENZIONE: le ultime 3 notti valide hanno consumo notturno anomalo. "
                         "Controlla il contatore con tutti i rubinetti chiusi e gli elettrodomestici fermi: "
                         "se gira, c'e' una perdita (o un uso notturno che non ricordi).")
        elif valide and valide[-1]["giudizio"] != "ok":
            righe.append("\nL'ultima notte valida e' anomala, ma una sola notte non basta: "
                         "guarda se si ripete.")
    if giorni:
        p = pavimento(giorni, dal)
        righe.append(f"\nPavimento dei consumi (minimo su {FINESTRA_PAVIMENTO} giorni, senza rigenerazioni):")
        if not p:
            righe.append("  dati insufficienti.")
        else:
            u = p["ultima"]
            righe.append(f"  ultima finestra dal {date.fromisoformat(u['dal']):%d/%m/%Y}: minimo {u['minimo']} L, "
                         f"{u['zeri']} giorni a zero")
            if "mediana" in p:
                righe.append(f"  storico ({p['finestre']} finestre): mediana dei minimi {p['mediana']:g} L, "
                             f"massimo {p['massimo']} L")
                if p["record"]:
                    righe.append("  ATTENZIONE: il pavimento attuale e' il piu' alto mai registrato. "
                                 "Una perdita lo alza e non lo fa piu' scendere; ma lo alza anche chi in "
                                 "casa non e' mai stato assente. Confrontalo con le tue assenze.")
                else:
                    righe.append("  nella norma.")
            else:
                righe.append(f"  storico troppo corto per giudicare (servono {MIN_FINESTRE} finestre).")
    return righe
