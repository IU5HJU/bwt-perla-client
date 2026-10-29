import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import storico  # noqa: E402
import web  # noqa: E402
from tracker import CICLI_COLONNE, CICLI_CSV  # noqa: E402

OGGI = date(2026, 10, 29)


def con_intervalli(intervalli, ultima=date(2026, 10, 27)):
    """per_giorno con una rigenerazione a distanza data l'una dall'altra, a ritroso
    dall'ultima."""
    d, out = ultima, {ultima.isoformat(): 1}
    for gap in intervalli:
        d -= timedelta(days=gap)
        out[d.isoformat()] = 1
    return out


class StatisticheTest(unittest.TestCase):
    def test_regolare_ogni_4_giorni(self):
        s = storico.statistiche_rigenerazioni(con_intervalli([4] * 40), OGGI)
        self.assertEqual((s["ultima"], s["giorni_dall_ultima"]), ("2026-10-27", 2))
        self.assertEqual(s["intervallo"], {"mediano": 4, "p25": 4, "p75": 4, "campione": 40})
        self.assertEqual((s["prossima_da"], s["prossima_a"]), ("2026-10-31", "2026-10-31"))

    def test_irregolare_da_una_fascia(self):
        s = storico.statistiche_rigenerazioni(con_intervalli([3, 4, 5, 4] * 10), OGGI)
        i = s["intervallo"]
        self.assertEqual((i["mediano"], i["p25"], i["p75"]), (4, 4, 5))
        self.assertEqual((s["prossima_da"], s["prossima_a"]), ("2026-10-31", "2026-11-01"))

    def test_ultime_30_giorni_contano_anche_piu_rigenerazioni_nello_stesso_giorno(self):
        per = {"2026-10-27": 1, "2026-10-20": 3, "2026-09-30": 1,          # 30 giorni fa: dentro (oggi-29)
               "2026-09-29": 1, "2026-10-30": 1}                            # fuori: troppo vecchio / futuro
        s = storico.statistiche_rigenerazioni(per, OGGI)
        self.assertEqual(s["ultime_30g"], 5)

    def test_pochi_dati_ripiega_sulla_frequenza(self):
        s = storico.statistiche_rigenerazioni(con_intervalli([4, 4]), OGGI, rate=0.25)
        self.assertEqual(s["intervallo"], {"mediano": 4.0, "p25": 3, "p75": 5, "campione": 0})
        self.assertEqual((s["prossima_da"], s["prossima_a"]), ("2026-10-30", "2026-11-01"))
        senza = storico.statistiche_rigenerazioni(con_intervalli([4, 4]), OGGI)
        self.assertIsNone(senza["intervallo"])
        self.assertIsNone(senza["prossima_da"])

    def test_nessuna_rigenerazione(self):
        s = storico.statistiche_rigenerazioni({"2026-10-28": 0}, OGGI, rate=0.25)
        self.assertEqual((s["ultime_30g"], s["ultima"], s["giorni_dall_ultima"], s["prossima_da"]),
                         (0, None, None, None))
        self.assertEqual(storico.statistiche_rigenerazioni({}, OGGI)["ultima"], None)

    def test_fuori_finestra_e_futuro_ignorati(self):
        per = {"2024-01-01": 1, "2026-10-30": 1}
        self.assertIsNone(storico.statistiche_rigenerazioni(per, OGGI)["ultima"])

    def test_rigenerazione_di_oggi(self):
        s = storico.statistiche_rigenerazioni({"2026-10-29": 1, "2026-10-25": 1}, OGGI)
        self.assertEqual(s["giorni_dall_ultima"], 0)


class PaginaTest(unittest.TestCase):
    def riga_cicli(self, ts, rig):
        r = {c: "" for c in CICLI_COLONNE}
        r.update(timestamp=ts, idx_quarti=1, idx_giorni=1, broadcast_hex="00", sale_dispositivo_g=0,
                 litri_nuovi=0, litri_totali=0, rigenerazioni_nuove=rig, rigenerazioni_totali=0,
                 blackout_nuovi=0, rigenerazioni_al_giorno_30g=0.25)
        return r

    def test_senza_storico_usa_la_registrazione(self):
        with tempfile.TemporaryDirectory() as d:
            storico.scrivi_csv(Path(d) / CICLI_CSV, CICLI_COLONNE,
                               [self.riga_cicli("2026-10-28T20:00:00", 0),
                                self.riga_cicli("2026-10-29T03:15:00", 1)])
            r = web.riepilogo(d, datetime(2026, 10, 29, 8, 0))["rigenerazioni"]
        self.assertEqual((r["ultima"], r["giorni_dall_ultima"], r["ultime_30g"]), ("2026-10-29", 0, 1))
        self.assertIsNone(r["intervallo"])                       # un solo evento: nessuna fascia
        self.assertEqual(r["litri_7g"], 0)

    def test_con_lo_storico_e_la_registrazione_insieme(self):
        giorni = []
        d0 = date(2026, 10, 28) - timedelta(days=60)
        for k in range(61):
            g = d0 + timedelta(days=k)
            giorni.append({"indice": k, "inizio_slot": f"{g}T02:45", "data": g.isoformat(), "litri": 190,
                           "rigenerazioni": 1 if (60 - k) % 4 == 1 else 0, "blackout": 0})   # 27/10, 23/10...
        with tempfile.TemporaryDirectory() as d:
            storico.scrivi_csv(Path(d) / "storico_giorni_20261029.csv", storico.GIORNI_COLONNE, giorni)
            storico.scrivi_csv(Path(d) / "storico_quarti_20261029.csv", storico.QUARTI_COLONNE, [])
            storico.scrivi_csv(Path(d) / CICLI_CSV, CICLI_COLONNE, [self.riga_cicli("2026-10-29T03:15:00", 0)])
            r = web.riepilogo(d, datetime(2026, 10, 29, 8, 0))["rigenerazioni"]
        self.assertEqual(r["ultima"], "2026-10-27")
        self.assertEqual(r["giorni_dall_ultima"], 2)
        self.assertEqual(r["intervallo"]["mediano"], 4)
        self.assertEqual(r["ultime_30g"], 7)
        self.assertEqual((r["prossima_da"], r["prossima_a"]), ("2026-10-31", "2026-10-31"))


if __name__ == "__main__":
    unittest.main()
