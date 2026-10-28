import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker import Tracker  # noqa: E402
from web import riepilogo  # noqa: E402


def bc(i):
    return {"idx_quarti": i, "idx_giorni": 1, "sale_g": 0,
            "sale_per_rigenerazione_g": 1200, "capacita_g": 14000}


class WebTest(unittest.TestCase):
    def test_senza_dati(self):
        with tempfile.TemporaryDirectory() as d:
            r = riepilogo(d, datetime(2026, 10, 28, 12, 0))
        self.assertIsNone(r["ultima"])
        self.assertEqual(r["giorni"], [])
        self.assertEqual(len(r["ore"]), 168)

    def test_riepilogo_con_dati(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Tracker(d)
            t0 = datetime(2026, 10, 28, 10, 0)
            tr.applica(t0, bc(10), b"\x00" * 20, [], [])
            tr.rabbocco(14, now=t0)
            t1 = datetime(2026, 10, 28, 11, 0)
            q = [{"litri": 10, "rigen": False, "blackout": False}] * 3 + \
                [{"litri": 0, "rigen": True, "blackout": False}]
            tr.applica(t1, bc(14), b"\x00" * 20, q,
                       [{"litri": 1, "rigen": 1, "blackout": False}] * 4)
            r = riepilogo(d, datetime(2026, 10, 28, 11, 10))
        self.assertEqual(r["eta_ultima_lettura_min"], 10)
        self.assertEqual(r["giorni"][-1]["litri"], 30)
        self.assertEqual(r["giorni"][-1]["rigenerazioni"], 1)
        self.assertEqual(sum(o["litri"] for o in r["ore"]), 30)
        self.assertEqual(r["ultima"]["sale_stimato_g"], "12800")
        self.assertEqual(r["totali"]["rigenerazioni"], 1)


if __name__ == "__main__":
    unittest.main()
