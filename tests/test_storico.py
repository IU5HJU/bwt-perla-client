import asyncio
import csv
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bwt_perla  # noqa: E402
import storico  # noqa: E402
from bwt_perla import Perla  # noqa: E402

MAC = "03:12:00:2E:00:59"


def frame(rango, parole):
    f = bytearray([rango & 255, rango >> 8])
    for w in parole + [0xFFFF] * (9 - len(parole)):
        f += bytes([w >> 8, w & 255])                  # voci dello storico: big-endian
    return bytes(f)


class CalibrazioneTest(unittest.TestCase):
    def serie(self, e0_vero, n=2880, giorni=29, seed=1):
        rnd = random.Random(seed)
        # profilo realistico: poco al mattino, doccia la sera, nulla di notte
        q = []
        for i in range(n):
            ora = (i % 96) / 4
            q.append(rnd.choice([0, 0, 1, 2]) if 5.5 <= ora < 7 else
                     rnd.randint(40, 70) if 22.25 <= ora < 22.75 else
                     rnd.choice([0, 0, 0, 3, 8]) if 7 <= ora < 22 else 0)
        d = [round(sum(q[e0_vero - 96 * (k + 1):e0_vero - 96 * k]) / 10) * 10
             for k in range(giorni)]
        return d[::-1], q                          # cronologico

    def test_ritrova_il_cambio_giorno(self):
        for e0 in (2880, 2880 - 37, 2880 - 95, 2880 - 12):
            d, q = self.serie(e0)
            ris = storico.calibra_rollover(d, q)
            e_min, e_max = ris[4]
            self.assertTrue(e_min <= e0 <= e_max, f"e0={e0} fuori da {ris[4]}")
            self.assertTrue(e_min <= ris[0] <= e_max)
            self.assertLess(ris[1], 5.1)                 # solo l'arrotondamento a 10 L
            self.assertLess(ris[1], 0.6 * ris[3])        # si distingue dalle altre ipotesi

    def test_intervallo_stretto_dopo_l_uso_del_mattino_largo_di_notte(self):
        d, q = self.serie(2880 - 61)                     # 05:45: attorno si usa poca acqua
        e_min, e_max = storico.calibra_rollover(d, q)[4]
        self.assertTrue(e_min <= 2880 - 61 <= e_max)
        self.assertLessEqual(e_max - e_min, 6)           # al piu' un'ora e mezza
        d, q = self.serie(2880 - 80)                     # 04:00: in mezzo alla notte vuota
        e_min, e_max = storico.calibra_rollover(d, q)[4]
        self.assertTrue(e_min <= 2880 - 80 <= e_max)
        self.assertGreater(e_max - e_min, 15)            # ore di notte: ora non ricavabile

    def test_dati_insufficienti(self):
        self.assertIsNone(storico.calibra_rollover([100, 200], [0] * 50))
        self.assertIsNone(storico.calibra_rollover([], [0] * 500))

    def test_giorni_tutti_a_zero_sono_incerti(self):
        ris = storico.calibra_rollover([0] * 29, [0] * 2880)
        self.assertEqual((ris[1], ris[3]), (0, 0))       # tutte le ipotesi uguali: nessuna migliore


class EtichetteTest(unittest.TestCase):
    def test_date_degli_slot(self):
        letto = datetime(2026, 10, 29, 21, 10, 30)         # ultimo quarto finito alle 21:00
        giorni = [{"litri": 100 * i, "rigenerazioni": 0, "blackout": 0} for i in range(3)]
        # l'ultimo giorno si e' chiuso 61 quarti prima delle 21:00, cioe' alle 05:45
        righe = storico.etichetta_giorni(giorni, 2880, 2880 - 61, letto)
        self.assertEqual([r["inizio_slot"] for r in righe],
                         ["2026-10-26T05:45", "2026-10-27T05:45", "2026-10-28T05:45"])
        self.assertEqual([r["data"] for r in righe], ["2026-10-26", "2026-10-27", "2026-10-28"])

    def test_quarti(self):
        righe = storico.etichetta_quarti([{"litri": 1}, {"litri": 2}], datetime(2026, 10, 29, 21, 10))
        self.assertEqual([r["inizio_approx"] for r in righe], ["2026-10-29T20:30", "2026-10-29T20:45"])

    def test_riepilogo(self):
        righe = []
        for i in range(14):                      # dal lunedi' 5 ottobre 2026
            d = datetime(2026, 10, 5) + timedelta(days=i)
            righe.append({"data": d.date().isoformat(),
                          "litri": 100 if d.weekday() < 5 else 300,
                          "rigenerazioni": 1 if i % 4 == 0 else 0})
        r = storico.riepilogo(righe)
        self.assertEqual((r["giorni"], r["da"], r["a"]), (14, "2026-10-05", "2026-10-18"))
        self.assertEqual(r["per_giorno_settimana"]["lun"], {"giorni": 2, "media": 100})
        self.assertEqual(r["per_giorno_settimana"]["sab"]["media"], 300)
        self.assertEqual(r["rigenerazioni"], 4)
        self.assertEqual(storico.riepilogo([]), {})


class LetturaCircolareTest(unittest.TestCase):
    """Un blocco che attraversa la fine del registro non deve infilare nei dati
    le voci in piu' dell'ultimo frame."""
    SIZE = 20
    DATI = list(range(100, 120))

    def finto(self, oltre):
        dati = self.DATI
        size = self.SIZE

        class Finto(Perla):
            def __init__(self):
                self.chiamate = 0

            async def _block(self, addr, n_entries):
                self.chiamate += 1
                pos = addr // 2
                out = []
                for r in range((n_entries * 2 + 17) // 18):
                    parole = [dati[p] if p < size else oltre
                              for p in range(pos + r * 9, pos + r * 9 + 9)]
                    out.append(frame(r, parole))
                return out
        return Finto()

    def test_attraversa_la_fine_con_memoria_spazzatura_o_vuota(self):
        for oltre in (9999, 0xFFFF, 0):
            res = asyncio.run(self.finto(oltre).history(0, self.SIZE, 5, self.SIZE, True))
            self.assertEqual([e["litri"] for e in res],
                             self.DATI[5:] + self.DATI[:5], f"oltre={oltre}")

    def test_blocchi_da_90_e_avanzamento(self):
        class Lungo(Perla):
            def __init__(self):
                pass

            async def _block(self, addr, n):
                return [frame(r, [1] * 9) for r in range((n * 2 + 17) // 18)]
        visti = []
        res = asyncio.run(Lungo().history(0, 2880, 500, 200, True, avanza=lambda a, b: visti.append((a, b))))
        self.assertEqual(len(res), 200)
        self.assertEqual(visti, [(90, 200), (180, 200), (200, 200)])

    def test_riprova_un_blocco_perso(self):
        class Instabile(Perla):
            def __init__(self):
                self.n = 0

            async def _block(self, addr, n):
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("blocco incompleto")
                return [frame(0, [7] * 9)]
        with mock.patch.object(asyncio, "sleep", new=mock.AsyncMock()):
            res = asyncio.run(Instabile().history(0, 100, 20, 5, True, tentativi=3))
        self.assertEqual(len(res), 5)
        with mock.patch.object(asyncio, "sleep", new=mock.AsyncMock()):
            with self.assertRaises(RuntimeError):
                asyncio.run(Instabile().history(0, 100, 20, 5, True, tentativi=1))


class ApparecchioFinto:
    """Simula la memoria dell'apparecchio e il protocollo di lettura a frame."""
    def __init__(self, quarti, giorni, idx_q, idx_g, loop_q=True):
        self.q, self.g, self.idx_q, self.idx_g, self.loop_q = quarti, giorni, idx_q, idx_g, loop_q
        self.cb = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def parola(self, w):
        if 0 <= w < 2880:
            x = self.q[w]
            return (x["litri"] & 0x3FF) | (0x800 if x["rigen"] else 0) | (0x400 if x["blackout"] else 0)
        if 3200 <= w < 3200 + 1825 and w - 3200 < len(self.g):
            x = self.g[w - 3200]
            return ((x["litri"] // 10) & 0x7FF) | ((x["rigen"] & 3) << 12) | (0x800 if x["blackout"] else 0)
        return 0xFFFF

    async def start_notify(self, uuid, cb):
        self.cb = cb

    async def read_gatt_char(self, uuid):
        if uuid == bwt_perla.U_AUTH:
            return b"\x00"
        b = bytearray(20)
        b[4:6] = self.idx_q.to_bytes(2, "little")
        b[6:8] = self.idx_g.to_bytes(2, "little")
        b[8:10] = (1200).to_bytes(2, "little")
        b[10:12] = (14).to_bytes(2, "little")
        b[12] = 0x02 if self.loop_q else 0x00
        b[13], b[14] = 1, 18
        return bytes(b)

    async def write_gatt_char(self, uuid, dati):
        addr = dati[1] | (dati[2] << 8)
        nbytes = dati[3] | (dati[4] << 8)
        w0 = addr // 2
        for r in range((nbytes + 17) // 18):
            self.cb(None, bytearray(frame(r, [self.parola(w0 + r * 9 + i) for i in range(9)])))


class ImportazioneTest(unittest.TestCase):
    def test_dall_apparecchio_ai_csv(self):
        rnd = random.Random(5)
        adesso = datetime(2026, 10, 29, 21, 10)          # ultimo quarto finito alle 21:00
        N = 2880
        e0_vero = N - 61                                  # cambio giorno 61 quarti prima (05:45)
        idx_q, idx_g, ng = 700, 120, 120                  # il registro dei quarti ha gia' fatto il giro
        # quarti in ordine cronologico, poi messi nel registro circolare
        cron = []
        for i in range(N):
            ora = (i % 96) / 4
            litri = (rnd.randint(40, 70) if 22.25 <= ora < 22.75 else
                     rnd.choice([0, 1, 2]) if 5.5 <= ora < 7 else
                     rnd.choice([0, 0, 4, 9]) if 7 <= ora < 22 else 0)
            cron.append({"litri": litri, "rigen": (i % 360) == 10, "blackout": False})
        reg_q = [None] * N
        for j, x in enumerate(cron):
            reg_q[(idx_q - N + j) % N] = x
        d_ultimi = [round(sum(x["litri"] for x in cron[e0_vero - 96 * (k + 1):e0_vero - 96 * k]) / 10) * 10
                    for k in range(29)][::-1]
        giorni = [{"litri": rnd.choice([100, 150, 200, 250]), "rigen": rnd.choice([0, 0, 1]), "blackout": False}
                  for _ in range(ng - 29)]
        giorni += [{"litri": v, "rigen": 0, "blackout": False} for v in d_ultimi]
        giorni += [{"litri": 0, "rigen": 0, "blackout": False}] * 0

        # ng giorni cronologici: nel registro occupano le posizioni 0..ng-1 (idx_g = ng)
        dispositivo = ApparecchioFinto(reg_q, giorni, idx_q, idx_g)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(bwt_perla, "BleakClient", lambda m, timeout=0: dispositivo):
                asyncio.run(bwt_perla.cmd_storico(MAC, d, 1825, adesso=adesso))
            with open(Path(d) / "storico_giorni_20261029.csv") as f:
                gg = list(csv.DictReader(f))
            with open(Path(d) / "storico_quarti_20261029.csv") as f:
                qq = list(csv.DictReader(f))

        # quarti: tutti letti, nell'ordine giusto e con gli stessi litri
        self.assertEqual(len(qq), N)
        self.assertEqual([int(r["litri"]) for r in qq], [x["litri"] for x in cron])
        self.assertEqual(qq[-1]["inizio_approx"], "2026-10-29T20:45")
        # giorni: tutti letti; il cambio giorno ritrovato (05:45) data gli slot
        self.assertEqual(len(gg), ng)
        self.assertEqual([int(r["litri"]) for r in gg], [x["litri"] for x in giorni])
        self.assertEqual(gg[-1]["inizio_slot"], "2026-10-28T05:45")
        self.assertEqual(gg[-2]["inizio_slot"], "2026-10-27T05:45")
        self.assertEqual(gg[0]["data"], (datetime(2026, 10, 28) - timedelta(days=ng - 1)).date().isoformat())


if __name__ == "__main__":
    unittest.main()
