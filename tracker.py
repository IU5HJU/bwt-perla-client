"""Registrazione su CSV e stima del sale, senza dipendenze da BLE.

L'addolcitore del modello in uso riporta sempre 0 g di sale (come la vecchia
app): il residuo e' quindi stimato come
    kg dopo il rabbocco - rigenerazioni dal rabbocco * g per rigenerazione.

Ogni rabbocco e' una misura reale: tra due rabbocchi si conoscono le
rigenerazioni avvenute e i kg rimessi, da cui si ricavano il consumo reale per
rigenerazione e il livello di "riserva" (quando si vede l'acqua di salamoia
sul fondo e si rabbocca). Il manuale dice che la rigenerazione e' proporzionale
alla resina esaurita: il sale consumato segue i litri addolciti, non il numero
di rigenerazioni, quindi il consumo si misura in grammi per litro (con il
valore per rigenerazione come ripiego). I rabbocchi stanno in `rabbocchi.json`, un file a
parte: il servizio di registrazione riscrive `state.json` a ogni ciclo e
cancellerebbe le modifiche fatte da un altro processo (CLI o pagina web).

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
RABBOCCHI_JSON = "rabbocchi.json"

LIVELLI = ("abbondante", "riserva", "sconosciuto")
# Frazione del serbatoio, finche' non c'e' una misura. Ricavata dall'abitudine
# dichiarata (rabbocco ogni ~40 giorni) e dal regime attuale di ~0,24
# rigenerazioni al giorno da 1200 g l'una (~290 g al giorno): in 40 giorni restano
# ~2,5 kg, cioe' il 18%. Il primo rabbocco "in riserva" misurato la sostituisce.
RISERVA_PROVVISORIA = 0.18

# Uso previsto (rigenerazioni e litri al giorno): media sugli ultimi GIORNI_USO
# giorni dello storico dell'apparecchio, riletta al piu' una volta al giorno. Una
# media su 30 giorni oscilla di ~6 giorni per una sola rigenerazione in piu' o in
# meno; su un anno e' stabile e non risente di assenze lontane nel tempo.
GIORNI_USO = 365
USO_VALIDITA_H = 20
USO_RETRY_H = 1
GIORNI_AVVISO = 7            # preavviso prima di arrivare in riserva
MIN_RIGEN_CICLO = 3          # rigenerazioni minime per fidarsi di un ciclo
MIN_LITRI_CICLO = 300        # litri minimi per misurare i grammi per litro
G_PER_LITRO_PLAUSIBILE = (0.05, 10.0)
FATTORE_PLAUSIBILE = (0.3, 3.0)   # consumo misurato / nominale ammesso
KG_MAX = 200

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
        "bc": None,     # ultimi parametri letti dall'apparecchio
    }


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _scrivi_righe(path: Path, colonne: list[str], righe: list[dict]) -> None:
    nuovo = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonne)
        if nuovo:
            w.writeheader()
        w.writerows(righe)


def _leggi_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _scrivi_json(path: Path, dati) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, indent=2))
    tmp.replace(path)


def carica_rabbocchi(data_dir) -> list[dict]:
    """Rabbocchi in ordine cronologico. Importa quello registrato dalle
    versioni precedenti (chiave `rabbocco` di state.json), se c'e'."""
    d = Path(data_dir).expanduser()
    p = d / RABBOCCHI_JSON
    if p.exists():
        return _leggi_json(p, [])
    vecchio = _leggi_json(d / STATE_JSON, {}).get("rabbocco")
    if not vecchio:
        return []
    lista = [{
        "data": vecchio["data"], "kg_dopo": vecchio["kg"], "kg_aggiunti": None,
        "livello_prima": "sconosciuto",
        "rigenerazioni_totali": vecchio["rigenerazioni_al_rabbocco"],
    }]
    d.mkdir(parents=True, exist_ok=True)
    _scrivi_json(p, lista)
    return lista


def registra_rabbocco(data_dir, kg_dopo=None, kg_aggiunti=None,
                      livello_prima="sconosciuto", rigen_gia_usate=0,
                      now=None) -> dict:
    """Aggiunge un rabbocco. Legge lo stato da disco (non da una copia in
    memoria), cosi' funziona anche mentre il servizio di registrazione gira.

    kg_dopo: kg nel serbatoio dopo il rabbocco (None = pieno, cioe' la
    capacita' letta dall'apparecchio). kg_aggiunti: kg versati, serve alla
    calibrazione. rigen_gia_usate: rigenerazioni gia' avvenute dal rabbocco
    reale, se registrato in ritardo."""
    d = Path(data_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    stato = _leggi_json(d / STATE_JSON, {})
    if kg_dopo is None:
        capa = (stato.get("bc") or {}).get("capacita_g")
        if not capa:
            raise ValueError("capacita' sconosciuta: indica i kg nel serbatoio")
        kg_dopo = capa / 1000
    if not 0 < kg_dopo <= KG_MAX:
        raise ValueError("kg nel serbatoio non validi")
    if kg_aggiunti is not None:
        if not 0 <= kg_aggiunti <= KG_MAX:
            raise ValueError("kg aggiunti non validi")
        if kg_aggiunti > kg_dopo:
            raise ValueError("i kg aggiunti superano quelli nel serbatoio")
    if livello_prima not in LIVELLI:
        raise ValueError("livello prima del rabbocco non valido")
    if rigen_gia_usate < 0:
        raise ValueError("rigenerazioni gia' usate non valide")
    now = now or datetime.now()
    rec = {
        "data": now.isoformat(timespec="seconds"),
        "kg_dopo": round(float(kg_dopo), 2),
        "kg_aggiunti": None if kg_aggiunti is None else round(float(kg_aggiunti), 2),
        "livello_prima": livello_prima,
        "rigenerazioni_totali": stato.get("rigenerazioni_totali", 0) - rigen_gia_usate,
        # litri contati dal servizio fino a questo rabbocco; sconosciuti se
        # il rabbocco e' registrato in ritardo
        "litri_totali": (stato.get("litri_totali", 0) if rigen_gia_usate == 0 else None),
    }
    lista = carica_rabbocchi(d)
    lista.append(rec)
    _scrivi_json(d / RABBOCCHI_JSON, lista)
    return rec


def annulla_ultimo_rabbocco(data_dir) -> dict:
    d = Path(data_dir).expanduser()
    lista = carica_rabbocchi(d)
    if not lista:
        raise ValueError("nessun rabbocco da annullare")
    tolto = lista.pop()
    _scrivi_json(d / RABBOCCHI_JSON, lista)
    return tolto


def cicli_rabbocco(rabbocchi: list[dict]) -> list[dict]:
    """Un ciclo per ogni coppia di rabbocchi consecutivi in cui il secondo ha
    i kg aggiunti: consumato = livello dopo il primo - livello prima del
    secondo, dove livello prima = kg_dopo - kg_aggiunti."""
    cicli = []
    for a, b in zip(rabbocchi, rabbocchi[1:]):
        agg = b.get("kg_aggiunti")
        if agg is None:
            continue
        residuo = b["kg_dopo"] - agg
        la, lb = a.get("litri_totali"), b.get("litri_totali")
        cicli.append({
            "litri": lb - la if la is not None and lb is not None else None,
            "rigenerazioni": b["rigenerazioni_totali"] - a["rigenerazioni_totali"],
            "consumato_kg": a["kg_dopo"] - residuo,
            "residuo_kg": residuo,
            "livello_prima": b.get("livello_prima"),
        })
    return cicli


def analisi_sale(rabbocchi, rigen_totali, g_nominale, capa_g, rate,
                 litri_totali=None, litri_giorno=None):
    """Stato del sale. None se non c'e' nessun rabbocco.

    - consumo: se ci sono cicli con i litri noti si usano i grammi per litro
      (la rigenerazione e' proporzionale alla resina esaurita); altrimenti i
      grammi per rigenerazione;
    - g per rigenerazione: misurato sui cicli plausibili (almeno
      MIN_RIGEN_CICLO rigenerazioni, entro FATTORE_PLAUSIBILE del valore
      dichiarato) o, in mancanza, quello dichiarato dall'apparecchio;
    - riserva: media dei residui ai rabbocchi fatti "in riserva", o una
      frazione provvisoria del serbatoio;
    - stato: riserva / avviso (a meno di GIORNI_AVVISO dalla riserva) / ok.
    """
    if not rabbocchi:
        return None
    ultimo = rabbocchi[-1]
    cicli = cicli_rabbocco(rabbocchi)

    def litri_ok(c):
        """Falso se i litri sono noti e il consumo per litro e' assurdo: il dato
        (kg digitati male) e' sospetto anche per il ripiego a rigenerazioni."""
        if c["litri"] is None or c["litri"] < MIN_LITRI_CICLO:
            return True
        return (G_PER_LITRO_PLAUSIBILE[0]
                <= c["consumato_kg"] * 1000 / c["litri"] <= G_PER_LITRO_PLAUSIBILE[1])

    def plausibile(c):
        if c["rigenerazioni"] < MIN_RIGEN_CICLO or c["consumato_kg"] <= 0:
            return False
        if not litri_ok(c):
            return False
        if not g_nominale:
            return True
        rapporto = c["consumato_kg"] * 1000 / c["rigenerazioni"] / g_nominale
        return FATTORE_PLAUSIBILE[0] <= rapporto <= FATTORE_PLAUSIBILE[1]

    validi = [c for c in cicli if plausibile(c)]

    def plausibile_litri(c):
        if c["litri"] is None or c["litri"] < MIN_LITRI_CICLO or c["consumato_kg"] <= 0:
            return False
        return (G_PER_LITRO_PLAUSIBILE[0]
                <= c["consumato_kg"] * 1000 / c["litri"] <= G_PER_LITRO_PLAUSIBILE[1])

    validi_l = [c for c in cicli if plausibile_litri(c)]
    litri_dal = None
    if ultimo.get("litri_totali") is not None and litri_totali is not None:
        litri_dal = litri_totali - ultimo["litri_totali"]
    g_litro = None
    if validi_l and litri_dal is not None:
        modello = "litri"
        g_litro = sum(c["consumato_kg"] for c in validi_l) * 1000 / sum(c["litri"] for c in validi_l)
        g_calibrato = True
        validi = validi_l
        consumo_g = litri_dal * g_litro
        uso_giorno_g = litri_giorno * g_litro if litri_giorno else None
        g_rig = (consumo_g / (rigen_totali - ultimo["rigenerazioni_totali"])
                 if rigen_totali > ultimo["rigenerazioni_totali"] else g_nominale)
    else:
        modello = "rigenerazioni"
        g_calibrato = bool(validi)
        g_rig = (sum(c["consumato_kg"] for c in validi) * 1000
                 / sum(c["rigenerazioni"] for c in validi)) if validi else g_nominale
        consumo_g = (rigen_totali - ultimo["rigenerazioni_totali"]) * g_rig
        uso_giorno_g = rate * g_rig if rate and g_rig else None

    rig_dal = rigen_totali - ultimo["rigenerazioni_totali"]
    residuo_g = max(0.0, ultimo["kg_dopo"] * 1000 - consumo_g)

    riserve = [c["residuo_kg"] for c in cicli
               if c["livello_prima"] == "riserva" and c["residuo_kg"] >= 0]
    riserva_calibrata = bool(riserve)
    riserva_g = (sum(riserve) / len(riserve) * 1000 if riserve
                 else RISERVA_PROVVISORIA * ultimo["kg_dopo"] * 1000)

    giorni_riserva = ((residuo_g - riserva_g) / uso_giorno_g
                      if uso_giorno_g else None)
    if giorni_riserva is not None:
        giorni_riserva = max(0, round(giorni_riserva))
    giorni_esaurimento = round(residuo_g / uso_giorno_g) if uso_giorno_g else None

    if residuo_g <= riserva_g:
        stato = "riserva"
    elif giorni_riserva is not None and giorni_riserva <= GIORNI_AVVISO:
        stato = "avviso"
    else:
        stato = "ok"
    # 100% = il livello a cui hai riempito, non la capacita' dichiarata
    # dall'apparecchio (che puo' differire dal serbatoio reale)
    capa = ultimo["kg_dopo"] * 1000
    return {
        "stato": stato,
        "residuo_g": int(residuo_g),
        "pct": min(100, round(residuo_g * 100 / capa)) if capa else None,
        "riserva_g": int(riserva_g),
        "riserva_calibrata": riserva_calibrata,
        "g_per_rigenerazione": round(g_rig),
        "g_calibrato": g_calibrato,
        "rigenerazioni_dal_rabbocco": rig_dal,
        "giorni_a_riserva": giorni_riserva,
        "giorni_a_esaurimento": giorni_esaurimento,
        "rabbocco_data": ultimo["data"],
        "rabbocco_kg": ultimo["kg_dopo"],
        "modello": modello,
        "g_per_litro": None if g_litro is None else round(g_litro, 3),
        "cicli_misurati": len(validi),
        "cicli_scartati": len(cicli) - len(validi),
    }


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

    def dispositivo(self, mac: str) -> bool:
        """Associa lo stato a un addolcitore. Se cambia, l'indice dei quarti
        d'ora dell'altro apparecchio non vale piu': si riparte da adesso, senza
        contare il passato. -> True se e' cambiato."""
        precedente = self.state.get("mac")
        if precedente == mac:
            return False
        self.state["mac"] = mac
        cambiato = precedente is not None
        if cambiato:
            self.state.update(last_idx=None, prev_rege=False, prev_black=False)
        self._salva()
        return cambiato

    # ── rabbocco ────────────────────────────────────────────────────

    def rabbocco(self, kg_dopo=None, rigen_gia_usate: int = 0, now=None,
                 kg_aggiunti=None, livello_prima="sconosciuto") -> dict:
        return registra_rabbocco(self.dir, kg_dopo, kg_aggiunti, livello_prima,
                                 rigen_gia_usate, now)

    # ── uso a lungo periodo ─────────────────────────────────────────

    def uso_da_aggiornare(self, now: datetime) -> bool:
        """True se conviene rileggere lo storico lungo: l'ultimo aggiornamento e'
        vecchio e un eventuale tentativo fallito non e' troppo recente."""
        def quando(chiave, sotto=None):
            v = (self.state.get(chiave) or {}).get("aggiornato") if sotto else self.state.get(chiave)
            try:
                return datetime.fromisoformat(v)
            except (TypeError, ValueError):
                return None
        agg, ten = quando("uso", True), quando("uso_tentato")
        if agg and now - agg < timedelta(hours=USO_VALIDITA_H):
            return False
        return not (ten and now - ten < timedelta(hours=USO_RETRY_H))

    def segna_tentativo_uso(self, now: datetime) -> None:
        self.state["uso_tentato"] = now.isoformat(timespec="seconds")
        self._salva()

    def imposta_uso(self, giorni: list[dict], now: datetime) -> None:
        """Rigenerazioni e litri al giorno medi sui `giorni` letti (anche quelli
        a zero: le assenze fanno parte dell'uso reale)."""
        if not giorni:
            return
        n = len(giorni)
        self.state["uso"] = {
            "rigenerazioni_al_giorno": sum(g["rigen"] for g in giorni) / n,
            "litri_al_giorno": sum(g["litri"] for g in giorni) / n,
            "giorni": n, "aggiornato": now.isoformat(timespec="seconds"),
        }
        self._salva()

    def _uso(self, rate_30g):
        """-> (rigenerazioni/giorno, litri/giorno, giorni della media)."""
        u = self.state.get("uso")
        if u:
            return u["rigenerazioni_al_giorno"], u["litri_al_giorno"], u["giorni"]
        return rate_30g, self.state.get("litri_giorno"), 30

    def analisi(self, rate=None):
        bc = self.state.get("bc")
        if not bc:
            return None
        if rate is None:
            u = self.ultima_riga()
            rate = _num(u["rigenerazioni_al_giorno_30g"]) if u else None
        rig_g, litri_g, base = self._uso(rate)
        a = analisi_sale(carica_rabbocchi(self.dir),
                         self.state["rigenerazioni_totali"],
                         bc["sale_per_rigenerazione_g"], bc["capacita_g"], rig_g,
                         self.state["litri_totali"], litri_g)
        if a:
            sd = self.state.get("stato_sale") or {}
            a["stato_da"] = sd.get("da") if sd.get("stato") == a["stato"] else None
            a["rigenerazioni_al_giorno"] = None if rig_g is None else round(rig_g, 3)
            a["uso_base_giorni"] = base
        return a

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
        s["bc"] = {"capacita_g": bc["capacita_g"],
                   "sale_per_rigenerazione_g": bc["sale_per_rigenerazione_g"]}

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

        # frequenza media delle rigenerazioni sui giorni letti (30): e' la colonna
        # del CSV e il ripiego se lo storico lungo non e' ancora disponibile
        rate = (sum(g["rigen"] for g in giorni) / len(giorni)) if giorni else None
        if giorni:     # litri al giorno secondo lo storico dell'apparecchio
            s["litri_giorno"] = sum(g["litri"] for g in giorni) / len(giorni)
        rig_g, litri_g, _ = self._uso(rate)
        a = analisi_sale(carica_rabbocchi(self.dir), s["rigenerazioni_totali"],
                         bc["sale_per_rigenerazione_g"], bc["capacita_g"], rig_g,
                         s["litri_totali"], litri_g)
        sale_g = pct = autonomia = None
        if a:
            sale_g, pct, autonomia = a["residuo_g"], a["pct"], a["giorni_a_esaurimento"]
        # momento in cui lo stato del sale e' cambiato (ok / avviso / riserva)
        nuovo = a["stato"] if a else None
        if nuovo != (s.get("stato_sale") or {}).get("stato"):
            s["stato_sale"] = {"stato": nuovo, "da": now.isoformat(timespec="seconds")}

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

    def ultima_riga(self) -> dict | None:
        p = self.dir / CICLI_CSV
        if not p.exists():
            return None
        with p.open(newline="") as f:
            righe = list(csv.DictReader(f))
        return righe[-1] if righe else None
