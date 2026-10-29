#!/usr/bin/env python3
"""Client BLE standalone per addolcitore BWT AQA Perla.

Uso:
  pip install bleak
  python bwt_perla.py scan
  python bwt_perla.py read [MAC]        # stato + ultimi consumi
  python bwt_perla.py dump [MAC]        # trame grezze, per analisi
  python bwt_perla.py log [MAC]         # lettura periodica -> CSV in ~/bwt-data
  python bwt_perla.py salt-refill KG    # registra un rabbocco (vedi --help)
  python bwt_perla.py status            # ultima riga registrata
  python bwt_perla.py serve             # registrazione + pagina web in un solo processo
  python bwt_perla.py storico [MAC]     # legge la memoria dell'apparecchio -> CSV (ferma prima il servizio)
  python bwt_perla.py analisi-storico   # rilegge i CSV senza Bluetooth (--da AAAA-MM-GG: messa in servizio)

Il protocollo e' ricostruito dal progetto GPL-3.0 Micka41/bwt-aqa-perla-ble:
questo file ne eredita la licenza (GPLv3).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

from config import carica_config, salva_config, valida_mac
import storico
from tracker import GIORNI_USO, Tracker

U_SERVICE = "d973f2e0-b19e-11e2-9e96-0800200c9a66"
U_NOTIFY = "d973f2e1-b19e-11e2-9e96-0800200c9a66"     # storico (notify)
U_WRITE = "d973f2e2-b19e-11e2-9e96-0800200c9a66"      # comandi
U_BROADCAST = "d973f2e3-b19e-11e2-9e96-0800200c9a66"  # sale + indici
U_AUTH = "d973f2e4-b19e-11e2-9e96-0800200c9a66"       # lettura = "auth"

# L'apparecchio si annuncia come iBeacon: dato del costruttore (ID Apple 76)
# = 02 15 + UUID, che qui e' la scritta "BestWaterTechno" + un byte zero.
# Verificato sull'apparecchio in uso; il servizio D973F2E0 non compare nella
# pubblicita', solo dopo la connessione.
RECO_BWT = b"BestWaterTechno"

# Firmware su cui il client e' stato provato; altri potrebbero comportarsi
# diversamente (per esempio i V2.x riportano il sale moltiplicato per 4).
FIRMWARE_TESTATI = ("A22X V1.18",)

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


def w_be(b, o):
    return (b[o] << 8) | b[o + 1]


def decode_frame(f: bytes, quarti: bool):
    """Trama da 20 byte: rango (2 byte LE) + 9 parole di storico big-endian."""
    if len(f) < 20:
        return []
    out = []
    for i in range(9):
        w = w_be(f, 2 + 2 * i)
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

    async def _blocco_con_tentativi(self, addr: int, n: int, tentativi: int):
        for t in range(1, tentativi + 1):
            try:
                return await self._block(addr, n)
            except RuntimeError:
                if t == tentativi:
                    raise
                await asyncio.sleep(1.0)

    async def history(self, base: int, size: int, idx: int, n: int, quarti: bool,
                      tentativi: int = 1, avanza=None):
        """Ultime n voci prima di idx (buffer circolare); ordine cronologico.

        Ogni blocco si taglia alla sua lunghezza: i frame portano 9 voci, e se un
        blocco non e' multiplo di 9 (succede quando la lettura attraversa la fine
        del registro) le voci in piu' vanno scartate, altrimenti si infilerebbero
        tra i dati e sfalserebbero quelli seguenti.
        `tentativi` > 1 riprova un blocco perso; `avanza(letti, totale)` e'
        chiamata dopo ogni blocco (letture lunghe)."""
        n = min(n, size)
        start = (idx - n) % size
        res, pos, left = [], start, n
        while left > 0:
            chunk = min(left, 90, size - pos)   # non oltrepassare la fine
            voci = []
            for f in await self._blocco_con_tentativi(base + 2 * pos, chunk, tentativi):
                voci += decode_frame(f, quarti)
            res += voci[:chunk]
            pos = (pos + chunk) % size
            left -= chunk
            if avanza:
                avanza(n - left, n)
        return res[:n]


def is_bwt(adv) -> bool:
    """True se la pubblicita' e' quella di un addolcitore BWT."""
    if U_SERVICE in [u.lower() for u in adv.service_uuids]:
        return True
    return any(RECO_BWT in bytes(v) for v in adv.manufacturer_data.values())


async def scansiona(timeout: float = 8.0) -> list[dict]:
    """Dispositivi BLE nelle vicinanze, gli addolcitori BWT per primi."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = [{"mac": a.upper(), "nome": d.name or adv.local_name or "",
            "rssi": adv.rssi, "bwt": is_bwt(adv)} for a, (d, adv) in found.items()]
    return sorted(out, key=lambda x: (not x["bwt"], -x["rssi"]))


async def verifica(mac: str) -> dict:
    """Si collega in sola lettura e legge il BROADCAST: serve a controllare che
    sia davvero un addolcitore BWT e a mostrarne i dati prima di usarlo."""
    mac = valida_mac(mac)
    try:
        async with BleakClient(mac, timeout=30) as cl:
            p = Perla(cl)
            await p.open()
            raw = await p.broadcast()
    except FileNotFoundError as e:
        raise RuntimeError("Bluetooth non disponibile sul server: controlla che il "
                           "servizio bluetooth sia attivo e l'adattatore acceso "
                           "(hciconfig).") from e
    except Exception as e:
        raise RuntimeError(f"connessione o lettura non riuscita: {e}") from e
    try:
        b = decode_broadcast(raw)
    except ValueError as e:
        raise RuntimeError(f"risposta non riconosciuta: {e}") from e
    return {"mac": mac, "firmware": b["firmware"],
            "capacita_kg": b["capacita_g"] / 1000,
            "sale_per_rigenerazione_g": b["sale_per_rigenerazione_g"],
            "allarme_sale": b["allarme_sale"],
            "testato": b["firmware"] in FIRMWARE_TESTATI}


async def find(mac: str | None) -> str:
    """MAC dato, oppure il primo addolcitore BWT trovato. LookupError se manca."""
    if mac:
        return mac
    print("Scansione (8 s)...")
    for d in await scansiona():
        if d["bwt"]:
            print(f"Trovato: {d['mac']}  rssi={d['rssi']}")
            return d["mac"]
    raise LookupError("Addolcitore non trovato: chiudi l'app BWT / avvicinati.")


async def cmd_scan():
    for d in await scansiona(10):
        tag = "  <== BWT" if d["bwt"] else ""
        print(f"{d['mac']}  {d['rssi']:4} dBm  {d['nome'] or '-'}{tag}")


async def cmd_read(mac, dump=False):
    try:
        addr = await find(mac)
    except LookupError as e:
        raise SystemExit(str(e))
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


def giorni_per_uso(b: dict, data_dir, oggi) -> int:
    """Quanti giorni leggere per l'uso a lungo periodo: GIORNI_USO, ma non oltre
    quelli scritti nel registro ne' oltre la messa in servizio (i giorni prima
    sono a zero perche' l'apparecchio non era installato)."""
    n = GIORNI_USO
    if not b["buffer_giorni_ciclico"]:
        n = min(n, b["idx_giorni"])
    ms = carica_config(data_dir).get("messa_in_servizio")
    if ms:
        try:
            n = min(n, (oggi - datetime.strptime(ms, "%Y-%m-%d").date()).days)
        except ValueError:
            pass
    return max(n, 0)


async def ciclo(mac: str, tr: Tracker) -> dict:
    """Una lettura: BROADCAST, quarti nuovi, ultimi 30 giorni -> CSV. Al piu' una
    volta al giorno rilegge anche l'ultimo anno di giorni, per la frequenza di
    rigenerazione usata nelle previsioni; se quella lettura fallisce si va avanti
    con i 30 giorni."""
    adesso = datetime.now()
    lungo = []
    async with BleakClient(mac, timeout=30) as cl:
        p = Perla(cl)
        await p.open()
        raw = await p.broadcast()
        b = decode_broadcast(raw)
        n = tr.quarti_da_leggere(b["idx_quarti"])
        q = await p.history(ADDR_QUARTI, MAX_QUARTI, b["idx_quarti"], n, True) if n else []
        d = await p.history(ADDR_GIORNI, MAX_GIORNI, b["idx_giorni"], 30, False)
        n_uso = giorni_per_uso(b, tr.dir, adesso.date())
        if n_uso >= 30 and tr.uso_da_aggiornare(adesso):
            tr.segna_tentativo_uso(adesso)
            try:
                lungo = await p.history(ADDR_GIORNI, MAX_GIORNI, b["idx_giorni"], n_uso,
                                        False, tentativi=3)
            except Exception as e:
                print(f"{adesso:%H:%M:%S}  storico lungo non letto ({e!r}): uso i 30 giorni",
                      flush=True)
    tr.imposta_uso(lungo, adesso)
    return tr.applica(adesso, b, raw, q, d)


async def ciclo_con_tentativi(mac, tr, tentativi=3, pausa=30, fn=None):
    """La radio dell'apparecchio ha poca portata: una lettura fallita si
    riprova dopo `pausa` secondi invece di aspettare il ciclo successivo.
    I dati si registrano solo a lettura riuscita (vedi `ciclo`), quindi un
    nuovo tentativo non conta nulla due volte."""
    fn = fn or ciclo
    for n in range(1, tentativi + 1):
        try:
            return await fn(mac, tr)
        except Exception as e:
            if n == tentativi:
                raise
            print(f"{datetime.now():%H:%M:%S}  tentativo {n}/{tentativi} fallito: "
                  f"{e!r}; riprovo tra {pausa} s", flush=True)
            await asyncio.sleep(pausa)


async def _attendi(evento: asyncio.Event, secondi: float) -> None:
    """Pausa interrompibile: la configurazione dalla pagina web sveglia il ciclo."""
    try:
        await asyncio.wait_for(evento.wait(), secondi)
    except asyncio.TimeoutError:
        pass
    evento.clear()


async def logger(mac_cli, data_dir, interval, once=False, lock=None,
                 evento=None, scansione_automatica=True):
    """Ciclo di registrazione. L'addolcitore e' quello dato a riga di comando,
    altrimenti quello in config.json (riletto a ogni ciclo, quindi cambia dalla
    pagina web senza riavvii), altrimenti, se consentito, il primo BWT trovato."""
    tr = Tracker(data_dir)
    lock = lock or asyncio.Lock()
    evento = evento or asyncio.Event()
    mac_auto, avvisato = None, False
    print(f"Registro in {tr.dir} ogni {interval} s (Ctrl+C per fermare)", flush=True)

    async def ciclo_in_esclusiva(m, t):      # un solo uso dell'antenna alla volta
        async with lock:
            return await ciclo(m, t)

    while True:
        mac = mac_cli or carica_config(data_dir).get("mac") or mac_auto
        if not mac and scansione_automatica:
            try:
                async with lock:
                    mac = mac_auto = await find(None)
            except LookupError as e:
                print(e, flush=True)
        if not mac:
            if not avvisato:
                print("Nessun addolcitore configurato: aprire la pagina web e "
                      "usare Impostazioni > Cerca addolcitore.", flush=True)
                avvisato = True
            await _attendi(evento, 30 if not once else 0)
            if once:
                raise SystemExit(1)
            continue
        avvisato = False
        try:
            if tr.dispositivo(mac):
                print(f"Addolcitore cambiato ({mac}): riparto da adesso.", flush=True)
            r = await ciclo_con_tentativi(mac, tr, fn=ciclo_in_esclusiva)
            print(f"{r['timestamp']}  +{r['litri_nuovi']} L  tot {r['litri_totali']} L  "
                  f"rigen {r['rigenerazioni_totali']}  sale stimato "
                  f"{r['sale_stimato_g'] or '-'} g  autonomia {r['autonomia_giorni'] or '-'} gg",
                  flush=True)
        except Exception as e:   # segnale debole: si riprova al ciclo dopo
            print(f"{datetime.now():%H:%M:%S}  lettura fallita: {e!r}", flush=True)
            if once:
                raise SystemExit(1)
        if once:
            return
        await _attendi(evento, interval)


async def cmd_log(mac, data_dir, interval, once):
    await logger(mac, data_dir, interval, once)


class PonteBle:
    """Ponte tra la pagina web (thread HTTP) e il ciclo asyncio che possiede
    l'antenna: scansione e verifica passano dallo stesso blocco della
    registrazione, cosi' non si pestano i piedi."""

    def __init__(self, loop, lock, evento, data_dir):
        self.loop, self.lock, self.evento, self.data_dir = loop, lock, evento, data_dir

    def _esegui(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def scan(self) -> list[dict]:
        async def _s():
            async with self.lock:
                return await scansiona()
        return self._esegui(_s(), 60)

    def verifica(self, mac: str) -> dict:
        async def _v():
            async with self.lock:
                return await verifica(mac)
        return self._esegui(_v(), 90)

    def salva_mac(self, mac: str) -> dict:
        cfg = salva_config(self.data_dir, mac=mac)
        self.loop.call_soon_threadsafe(self.evento.set)   # riparte subito
        return cfg


def cmd_serve(mac, data_dir, interval, host, port, pin):
    """Registrazione e pagina web in un solo processo (un solo servizio)."""
    import web
    loop = asyncio.new_event_loop()
    lock, evento = asyncio.Lock(), asyncio.Event()
    threading.Thread(target=loop.run_forever, daemon=True, name="ble").start()
    asyncio.run_coroutine_threadsafe(
        logger(mac, data_dir, interval, False, lock, evento, scansione_automatica=False),
        loop)
    web.Handler.ble = PonteBle(loop, lock, evento, data_dir)
    web.serve(host, port, data_dir, pin)


async def _leggi_storico(mac: str, giorni: int):
    """Legge i due registri; si riprova l'intera sessione se la connessione cade."""
    ultimo_errore = None
    for tentativo in range(1, 4):
        try:
            async with BleakClient(mac, timeout=30) as cl:
                p = Perla(cl)
                await p.open()
                b = decode_broadcast(await p.broadcast())
                nq = MAX_QUARTI if b["buffer_quarti_ciclico"] else b["idx_quarti"]
                ng = min(giorni, MAX_GIORNI if b["buffer_giorni_ciclico"] else b["idx_giorni"])

                def avanza(nome):
                    return lambda letti, tot: print(f"  {nome}: {letti}/{tot}", end="\r", flush=True)

                print(f"Quarti d'ora ({nq}):")
                q = await p.history(ADDR_QUARTI, MAX_QUARTI, b["idx_quarti"], nq, True,
                                    4, avanza("quarti"))
                print(f"\nGiorni ({ng}):")
                g = await p.history(ADDR_GIORNI, MAX_GIORNI, b["idx_giorni"], ng, False,
                                    4, avanza("giorni"))
                print()
                return b, q, g
        except Exception as e:
            ultimo_errore = e
            print(f"\nLettura interrotta ({e!r}); tentativo {tentativo}/3", flush=True)
            await asyncio.sleep(5)
    raise RuntimeError(f"lettura dello storico non riuscita: {ultimo_errore!r}")


async def cmd_storico(mac, data_dir, giorni, adesso=None, da=None):
    addr = mac or carica_config(data_dir).get("mac")
    if not addr:
        try:
            addr = await find(None)
        except LookupError as e:
            raise SystemExit(str(e))
    print(f"Lettura della memoria di {addr}: puo' richiedere qualche minuto.\n"
          "Se il servizio di registrazione e' attivo fermalo prima "
          "(sudo systemctl stop bwt-perla): usano la stessa antenna.\n")
    letto_il = adesso or datetime.now()
    b, quarti, giorni_v = await _leggi_storico(addr, giorni)
    if len(quarti) < MAX_QUARTI and b["buffer_quarti_ciclico"]:
        print(f"Attenzione: letti {len(quarti)} quarti su {MAX_QUARTI}.")

    ql = [x["litri"] for x in quarti]
    gl = [x["litri"] for x in giorni_v]
    cal = storico.calibra_rollover(gl, ql)
    if cal is None:
        e0 = len(quarti)
        print("Calibrazione non possibile (pochi dati): date approssimate all'ora di lettura.")
    else:
        e0, scarto, n_conf, mediano, (e_min, e_max) = cal
        fine = letto_il.replace(minute=letto_il.minute - letto_il.minute % 15,
                                second=0, microsecond=0)

        def ora(e):
            return fine - storico.QUARTO * (len(quarti) - e)

        sicura = scarto < 0.6 * mediano
        quando = (f"verso le {ora(e0):%H:%M}" if e_min == e_max else
                  f"tra le {ora(e_min):%H:%M} e le {ora(e_max):%H:%M} (di notte non si usa acqua: "
                  "l'ora esatta non si ricava, ma le date dei giorni non cambiano)")
        print(f"Cambio giorno dell'apparecchio {quando}; orari approssimati; "
              f"scarto medio {scarto:.1f} L su {n_conf} giorni "
              f"({'calibrazione affidabile' if sicura else 'CALIBRAZIONE INCERTA'}).")

    n = len(giorni_v)
    righe_g = [{"indice": (b["idx_giorni"] - n + j) % MAX_GIORNI, "litri": g["litri"],
                "rigenerazioni": g["rigen"], "blackout": int(g["blackout"])}
               for j, g in enumerate(giorni_v)]
    righe_g = storico.etichetta_giorni(righe_g, len(quarti), e0, letto_il)
    nq = len(quarti)
    righe_q = [{"indice": (b["idx_quarti"] - nq + j) % MAX_QUARTI, "litri": x["litri"],
                "rigenerazione": int(x["rigen"]), "blackout": int(x["blackout"])}
               for j, x in enumerate(quarti)]
    righe_q = storico.etichetta_quarti(righe_q, letto_il)

    cartella = Tracker(data_dir).dir
    sigla = letto_il.strftime("%Y%m%d")
    pg, pq = cartella / f"storico_giorni_{sigla}.csv", cartella / f"storico_quarti_{sigla}.csv"
    storico.scrivi_csv(pg, storico.GIORNI_COLONNE, righe_g)
    storico.scrivi_csv(pq, storico.QUARTI_COLONNE, righe_q)

    da = _messa_in_servizio(data_dir, da)
    print("\n" + "\n".join(storico.righe_riepilogo(storico.riepilogo(righe_g, da),
                                                   storico.profilo_orario(righe_q))))
    print(f"\nScritti:\n  {pg}\n  {pq}")


def _messa_in_servizio(data_dir, da):
    """Data di messa in servizio: da riga di comando (e allora si salva in
    config.json) oppure quella gia' salvata. I giorni precedenti sono a zero
    perche' l'apparecchio non era installato."""
    if da:
        try:
            datetime.strptime(da, "%Y-%m-%d")
        except ValueError:
            raise SystemExit("--da vuole una data nel formato AAAA-MM-GG")
        if carica_config(data_dir).get("messa_in_servizio") != da:
            salva_config(data_dir, messa_in_servizio=da)
            print(f"Messa in servizio salvata in config.json: {da}")
        return da
    return carica_config(data_dir).get("messa_in_servizio")


def cmd_analisi_storico(data_dir, file_giorni, file_quarti, da):
    """Rilegge i CSV di `storico` senza Bluetooth."""
    cartella = Tracker(data_dir).dir

    def ultimo(prefisso):
        trovati = sorted(cartella.glob(prefisso + "_*.csv"))
        if not trovati:
            raise SystemExit(f"Nessun file {prefisso}_*.csv in {cartella}: lancia prima 'storico'.")
        return trovati[-1]

    pg = Path(file_giorni) if file_giorni else ultimo("storico_giorni")
    pq = Path(file_quarti) if file_quarti else None
    if pq is None:
        try:
            pq = ultimo("storico_quarti")
        except SystemExit:
            pq = None
    print(f"File: {pg.name}" + (f", {pq.name}" if pq else ""))
    da = _messa_in_servizio(data_dir, da)
    if da is None:
        print("Suggerimento: indica la data di messa in servizio con --da AAAA-MM-GG; "
              "senza, i giorni precedenti (a zero) falsano le medie.")
    profilo = storico.profilo_orario(storico.carica_quarti(pq)) if pq else None
    print("\n".join(storico.righe_riepilogo(
        storico.riepilogo(storico.carica_giorni(pg), da), profilo)))


def cmd_salt_refill(kg_dopo, aggiunti, livello, used_regens, data_dir):
    try:
        r = Tracker(data_dir).rabbocco(kg_dopo, used_regens,
                                       kg_aggiunti=aggiunti, livello_prima=livello)
    except ValueError as e:
        raise SystemExit(f"Rabbocco non registrato: {e}")
    agg = "?" if r["kg_aggiunti"] is None else r["kg_aggiunti"]
    print(f"Rabbocco registrato il {r['data']}: {r['kg_dopo']} kg nel serbatoio, "
          f"{agg} kg aggiunti, livello prima: {r['livello_prima']}")
    if aggiunti is None:
        print("Suggerimento: indica --aggiunti per calibrare il consumo reale.")


def cmd_status(data_dir):
    tr = Tracker(data_dir)
    r = tr.ultima_riga()
    if not r:
        raise SystemExit("Nessun dato: lancia prima 'log'.")
    for k, v in r.items():
        if k != "broadcast_hex":
            print(f"  {k}: {v}")
    a = tr.analisi()
    if not a:
        print("  sale: nessun rabbocco registrato (usa salt-refill)")
        return
    da = f" dal {a['stato_da'][:10]}" if a.get("stato_da") else ""
    print(f"  sale: stato {a['stato']}{da}, residuo stimato {a['residuo_g'] / 1000:.1f} kg "
          f"({a['pct']}%), riserva a {a['riserva_g'] / 1000:.1f} kg "
          f"({'misurata' if a['riserva_calibrata'] else 'provvisoria'})")
    if a["modello"] == "litri":
        print(f"  consumo: {a['g_per_litro']} g per litro (misurato su {a['cicli_misurati']} cicli)")
    else:
        print(f"  consumo: {a['g_per_rigenerazione']} g per rigenerazione "
              f"({'misurato su ' + str(a['cicli_misurati']) + ' cicli' if a['g_calibrato'] else 'valore dichiarato dall apparecchio'})")
    print(f"  giorni alla riserva: {a['giorni_a_riserva']}, "
          f"all'esaurimento teorico: {a['giorni_a_esaurimento']}")


def selftest():
    # trama sintetica: rango 0, parole (big-endian) 100, 0x0800|5, 0xFFFF
    f = bytes([0, 0, 0, 100, 0x08, 5, 0xFF, 0xFF]) + bytes(12)
    e = decode_frame(f, True)
    assert e[0]["litri"] == 100 and e[1]["rigen"] and len(e) == 2, e
    bc = bytes([0xE8, 0x03, 0, 0, 1, 0, 2, 0, 50, 0, 10, 0, 0, 1, 5])
    assert decode_broadcast(bc)["sale_pct"] == 10, decode_broadcast(bc)
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description="Client BLE BWT AQA Perla")
    ap.add_argument("--data-dir", default="~/bwt-data")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("scan")
    sub.add_parser("selftest")
    sub.add_parser("status")
    for name in ("read", "dump"):
        sub.add_parser(name).add_argument("mac", nargs="?")
    lg = sub.add_parser("log")
    lg.add_argument("mac", nargs="?")
    lg.add_argument("--interval", type=int, default=900, help="secondi (def. 900)")
    lg.add_argument("--once", action="store_true", help="una sola lettura")
    sv = sub.add_parser("serve", help="registrazione + pagina web")
    sv.add_argument("mac", nargs="?", help="se omesso: quello di config.json")
    sv.add_argument("--interval", type=int, default=900)
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--pin", default=None, help="PIN per le azioni che scrivono")
    st = sub.add_parser("storico", help="legge la memoria dell'apparecchio -> CSV")
    st.add_argument("mac", nargs="?", help="se omesso: quello di config.json")
    st.add_argument("--giorni", type=int, default=MAX_GIORNI,
                    help=f"quanti giorni leggere (max {MAX_GIORNI})")
    st.add_argument("--da", help="messa in servizio AAAA-MM-GG: esclude i giorni precedenti")
    an = sub.add_parser("analisi-storico", help="rilegge i CSV di storico, senza Bluetooth")
    an.add_argument("--giorni-file", help="storico_giorni_*.csv (predefinito: l'ultimo)")
    an.add_argument("--quarti-file", help="storico_quarti_*.csv (predefinito: l'ultimo)")
    an.add_argument("--da", help="messa in servizio AAAA-MM-GG (si salva in config.json)")
    rf = sub.add_parser("salt-refill")
    rf.add_argument("kg", type=float,
                    help="kg nel serbatoio DOPO il rabbocco (es. 14 se pieno)")
    rf.add_argument("--aggiunti", type=float, default=None,
                    help="kg di sale versati: serve a calibrare i consumi reali")
    rf.add_argument("--livello", choices=("abbondante", "riserva", "sconosciuto"),
                    default="sconosciuto",
                    help="livello prima del rabbocco; 'riserva' = si vedeva "
                         "l'acqua di salamoia sul fondo")
    rf.add_argument("--used-regens", type=int, default=0,
                    help="rigenerazioni gia' avvenute dal rabbocco reale")
    a = ap.parse_args()

    if a.cmd in (None, "scan"):
        asyncio.run(cmd_scan())
    elif a.cmd in ("read", "dump"):
        asyncio.run(cmd_read(a.mac, a.cmd == "dump"))
    elif a.cmd == "log":
        try:
            asyncio.run(cmd_log(a.mac, a.data_dir, a.interval, a.once))
        except KeyboardInterrupt:
            pass
    elif a.cmd == "serve":
        try:
            cmd_serve(a.mac, a.data_dir, a.interval, a.host, a.port, a.pin)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "storico":
        asyncio.run(cmd_storico(a.mac, a.data_dir, a.giorni, da=a.da))
    elif a.cmd == "analisi-storico":
        cmd_analisi_storico(a.data_dir, a.giorni_file, a.quarti_file, a.da)
    elif a.cmd == "salt-refill":
        cmd_salt_refill(a.kg, a.aggiunti, a.livello, a.used_regens, a.data_dir)
    elif a.cmd == "status":
        cmd_status(a.data_dir)
    elif a.cmd == "selftest":
        selftest()


if __name__ == "__main__":
    main()
