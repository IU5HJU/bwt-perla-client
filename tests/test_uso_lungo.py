import asyncio
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bwt_perla  # noqa: E402
from config import salva_config  # noqa: E402
from test_storico import ApparecchioFinto  # noqa: E402
from tracker import (GIORNI_USO, RISERVA_PROVVISORIA, Tracker,  # noqa: E402
                     registra_rabbocco)

MAC = "03:12:00:2E:00:59"
ORA = datetime(2026, 10, 29, 12, 0)


def giorni(n, ogni=4, litri=200):
    """n giorni cronologici: una rigenerazione ogni `ogni` giorni."""
    return [{"litri": litri, "rigen": 1 if j % ogni == 0 else 0, "blackout": False}
            for j in range(n)]


def bc(idx=10):
    return {"idx_quarti": idx, "idx_giorni": 1, "sale_g": 0,
            "sale_per_rigenerazione_g": 1200, "capacita_g": 14000}


class ParametriTest(unittest.TestCase):
    def test_valori(self):
        self.assertEqual(RISERVA_PROVVISORIA, 0.18)
        self.assertEqual(GIORNI_USO, 365)


class UsoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tr = Tracker(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_imposta_uso_include_i_giorni_a_zero(self):
        g = giorni(10, ogni=5, litri=100) + [{"litri": 0, "rigen": 0, "blackout": False}] * 10
        self.tr.imposta_uso(g, ORA)
        u = self.tr.state["uso"]
        self.assertEqual((u["giorni"], u["rigenerazioni_al_giorno"], u["litri_al_giorno"]),
                         (20, 2 / 20, 50.0))
        self.tr.imposta_uso([], ORA)                       # nessun dato: non cambia nulla
        self.assertEqual(self.tr.state["uso"]["giorni"], 20)
        self.assertEqual(Tracker(self.tmp.name).state["uso"]["giorni"], 20)    # persistito

    def test_quando_rileggere(self):
        self.assertTrue(self.tr.uso_da_aggiornare(ORA))                        # mai letto
        self.tr.segna_tentativo_uso(ORA)
        self.assertFalse(self.tr.uso_da_aggiornare(ORA + timedelta(minutes=30)))   # appena provato
        self.assertTrue(self.tr.uso_da_aggiornare(ORA + timedelta(hours=1, minutes=1)))
        self.tr.imposta_uso(giorni(30), ORA)
        self.assertFalse(self.tr.uso_da_aggiornare(ORA + timedelta(hours=5)))      # fresco
        self.assertTrue(self.tr.uso_da_aggiornare(ORA + timedelta(hours=21)))      # vecchio
        self.tr.segna_tentativo_uso(ORA + timedelta(hours=21))
        self.assertFalse(self.tr.uso_da_aggiornare(ORA + timedelta(hours=21, minutes=10)))

    def test_l_uso_lungo_vince_sui_30_giorni(self):
        self.tr.applica(ORA, bc(), b"\0" * 20, [], giorni(30, ogni=2))         # 30 giorni: 0,5/giorno
        registra_rabbocco(self.tmp.name, 14, now=ORA)
        r = self.tr.applica(ORA, bc(), b"\0" * 20, [], giorni(30, ogni=2))
        self.assertEqual(r["rigenerazioni_al_giorno_30g"], 0.5)               # la colonna resta a 30 giorni
        a30 = self.tr.analisi()
        self.assertEqual((a30["uso_base_giorni"], a30["rigenerazioni_al_giorno"]), (30, 0.5))
        self.tr.imposta_uso(giorni(360, ogni=4), ORA)                          # 0,25/giorno su un anno
        a = self.tr.analisi()
        self.assertEqual((a["uso_base_giorni"], a["rigenerazioni_al_giorno"]), (360, 0.25))
        self.assertGreater(a["giorni_a_riserva"], a30["giorni_a_riserva"])     # meno rigenerazioni: piu' giorni
        # e anche la riga di CSV scritta dopo usa l'anno, non i 30 giorni
        r2 = self.tr.applica(ORA, bc(), b"\0" * 20, [], giorni(30, ogni=2))
        self.assertEqual(r2["autonomia_giorni"], round(a["residuo_g"] / (0.25 * a["g_per_rigenerazione"])))


class GiorniPerUsoTest(unittest.TestCase):
    def b(self, ciclico=True, idx=121):
        return {"buffer_giorni_ciclico": ciclico, "idx_giorni": idx}

    def test_casi(self):
        with tempfile.TemporaryDirectory() as d:
            oggi = date(2026, 10, 29)
            self.assertEqual(bwt_perla.giorni_per_uso(self.b(), d, oggi), 365)
            self.assertEqual(bwt_perla.giorni_per_uso(self.b(False, 120), d, oggi), 120)   # registro non pieno
            salva_config(d, messa_in_servizio="2026-09-09")
            self.assertEqual(bwt_perla.giorni_per_uso(self.b(), d, oggi), 50)              # non oltre la messa in servizio
            salva_config(d, messa_in_servizio="2023-03-22")
            self.assertEqual(bwt_perla.giorni_per_uso(self.b(), d, oggi), 365)
            salva_config(d, messa_in_servizio="2030-01-01")
            self.assertEqual(bwt_perla.giorni_per_uso(self.b(), d, oggi), 0)
            salva_config(d, messa_in_servizio="boh")
            self.assertEqual(bwt_perla.giorni_per_uso(self.b(), d, oggi), 365)             # data illeggibile: ignorata


class CicloTest(unittest.TestCase):
    def dispositivo(self, n_giorni=120):
        quarti = [{"litri": 0, "rigen": False, "blackout": False}] * 2880
        return ApparecchioFinto(quarti, giorni(n_giorni), idx_q=100, idx_g=n_giorni)

    def esegui(self, tr, dispositivo, spia):
        original = bwt_perla.Perla.history

        async def con_spia(self_, base, size, idx, n, quarti, tentativi=1, avanza=None):
            spia.append(n)
            if spia.errore and n > 30:
                raise RuntimeError("blocco incompleto")
            return await original(self_, base, size, idx, n, quarti, tentativi, avanza)

        with mock.patch.object(bwt_perla, "BleakClient", lambda m, timeout=0: dispositivo), \
                mock.patch.object(bwt_perla.Perla, "history", con_spia):
            return asyncio.run(bwt_perla.ciclo(MAC, tr))

    class Spia(list):
        errore = False

    def test_rilegge_l_anno_una_volta_al_giorno_rispettando_la_messa_in_servizio(self):
        with tempfile.TemporaryDirectory() as d:
            salva_config(d, messa_in_servizio=(date.today() - timedelta(days=50)).isoformat())
            tr, spia = Tracker(d), self.Spia()
            dev = self.dispositivo()
            self.esegui(tr, dev, spia)
            self.assertEqual(spia, [30, 50])                    # 30 giorni di sempre + 50 per l'uso
            ultimi50 = giorni(120)[-50:]
            self.assertEqual(tr.state["uso"]["giorni"], 50)
            self.assertAlmostEqual(tr.state["uso"]["rigenerazioni_al_giorno"],
                                   sum(g["rigen"] for g in ultimi50) / 50)
            self.assertEqual(tr.state["uso"]["litri_al_giorno"], 200)
            spia.clear()
            self.esegui(tr, dev, spia)                          # stesso giorno: niente storico lungo
            self.assertEqual(spia, [30])

    def test_se_lo_storico_lungo_fallisce_il_ciclo_riesce_lo_stesso(self):
        with tempfile.TemporaryDirectory() as d:
            tr, spia = Tracker(d), self.Spia()
            spia.errore = True
            riga = self.esegui(tr, self.dispositivo(), spia)
            self.assertIn("timestamp", riga)                    # la lettura normale e' stata registrata
            self.assertIsNone(tr.state.get("uso"))
            self.assertIn("uso_tentato", tr.state)
            spia.clear()
            self.esegui(tr, self.dispositivo(), spia)           # entro un'ora: non riprova
            self.assertEqual(spia, [30])


if __name__ == "__main__":
    unittest.main()
