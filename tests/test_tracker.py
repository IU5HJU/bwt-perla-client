import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker import Tracker  # noqa: E402


def bc(idx_q, idx_g=121):
    return {"idx_quarti": idx_q, "idx_giorni": idx_g, "sale_g": 0,
            "sale_per_rigenerazione_g": 1200, "capacita_g": 14000}


def q(litri, rigen=False, black=False):
    return {"litri": litri, "rigen": rigen, "blackout": black}


def giorni(n_rigen_per_giorno):
    return [{"litri": 200, "rigen": r, "blackout": False} for r in n_rigen_per_giorno]


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tr = Tracker(self.tmp.name)
        self.t0 = datetime(2026, 10, 28, 10, 7, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_primo_avvio_non_conta_il_passato(self):
        self.assertEqual(self.tr.quarti_da_leggere(2551), 0)
        r = self.tr.applica(self.t0, bc(2551), b"\x00" * 20, [q(50)], giorni([0]))
        self.assertEqual(r["litri_nuovi"], 0)
        self.assertEqual(self.tr.state["last_idx"], 2551)
        self.assertFalse((Path(self.tmp.name) / "quarti.csv").exists())

    def test_conteggi_e_fronti(self):
        self.tr.applica(self.t0, bc(100), b"\x00" * 20, [], [])
        self.assertEqual(self.tr.quarti_da_leggere(105), 5)
        quarti = [q(10), q(0, rigen=True), q(0, rigen=True), q(20), q(5, black=True)]
        r = self.tr.applica(self.t0, bc(105), b"\x00" * 20, quarti, giorni([0, 1]))
        self.assertEqual(r["litri_nuovi"], 35)
        self.assertEqual(r["rigenerazioni_nuove"], 1)   # un evento su 2 quarti
        self.assertEqual(r["blackout_nuovi"], 1)
        # una rigenerazione a cavallo di due letture non si conta due volte
        self.tr.applica(self.t0, bc(107), b"\x00" * 20,
                        [q(0, rigen=True), q(0, rigen=True)], [])
        self.tr.applica(self.t0, bc(108), b"\x00" * 20, [q(0, rigen=True)], [])
        self.assertEqual(self.tr.state["rigenerazioni_totali"], 2)

    def test_giro_del_buffer(self):
        self.tr.applica(self.t0, bc(2878), b"\x00" * 20, [], [])
        self.assertEqual(self.tr.quarti_da_leggere(3), 5)

    def test_stima_sale(self):
        self.tr.applica(self.t0, bc(100), b"\x00" * 20, [], [])
        self.tr.rabbocco(14, now=self.t0)
        quarti = [q(0, rigen=True), q(0), q(0, rigen=True), q(0)]
        r = self.tr.applica(self.t0, bc(104), b"\x00" * 20, quarti,
                            giorni([1, 0, 0, 1, 0, 0, 0]))
        self.assertEqual(r["rigenerazioni_totali"], 2)
        self.assertEqual(r["sale_stimato_g"], 14000 - 2 * 1200)
        self.assertEqual(r["sale_stimato_pct"], round(11600 * 100 / 14000))
        # 2 rigenerazioni su 7 giorni -> 11600 / (2/7 * 1200) = 33.8 giorni
        self.assertEqual(r["autonomia_giorni"], round(11600 / (2 / 7 * 1200)))

    def test_rabbocco_con_rigenerazioni_gia_usate(self):
        self.tr.applica(self.t0, bc(100), b"\x00" * 20, [], [])
        self.tr.rabbocco(14, rigen_gia_usate=3, now=self.t0)
        r = self.tr.applica(self.t0, bc(100), b"\x00" * 20, [], giorni([1]))
        self.assertEqual(r["sale_stimato_g"], 14000 - 3 * 1200)

    def test_senza_rabbocco_nessuna_stima(self):
        r = self.tr.applica(self.t0, bc(100), b"\x00" * 20, [], giorni([1]))
        self.assertEqual(r["sale_stimato_g"], "")

    def test_stato_persistente_e_csv(self):
        self.tr.applica(self.t0, bc(100), b"\x00" * 20, [], [])
        self.tr.applica(self.t0, bc(102), b"\x00" * 20, [q(7), q(8)], [])
        tr2 = Tracker(self.tmp.name)
        self.assertEqual(tr2.state["litri_totali"], 15)
        with (Path(self.tmp.name) / "quarti.csv").open() as f:
            righe = list(csv.DictReader(f))
        self.assertEqual([r["litri"] for r in righe], ["7", "8"])
        self.assertEqual(righe[-1]["inizio_approx"], "2026-10-28T09:45")
        self.assertEqual(tr2.ultima_riga()["idx_quarti"], "102")


if __name__ == "__main__":
    unittest.main()
