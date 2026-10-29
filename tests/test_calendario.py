import contextlib
import io
import math
import random
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bwt_perla  # noqa: E402
import calendario as cal  # noqa: E402
import storico  # noqa: E402
from config import carica_config  # noqa: E402


class PasquaEFestiviTest(unittest.TestCase):
    def test_pasqua_anni_noti(self):
        attese = {2000: date(2000, 4, 23), 2019: date(2019, 4, 21), 2023: date(2023, 4, 9),
                  2024: date(2024, 3, 31), 2025: date(2025, 4, 20), 2026: date(2026, 4, 5)}
        for anno, d in attese.items():
            self.assertEqual(cal.pasqua(anno), d, anno)

    def test_festivi_nazionali_e_pasquetta(self):
        f = cal.Calendario().festivi(2026)
        self.assertEqual(len(f), 12)                           # 10 fisse + Pasqua + Pasquetta
        self.assertEqual(f[date(2026, 4, 6)], "Pasquetta")
        self.assertEqual(f[date(2026, 6, 2)], "Festa della Repubblica")
        self.assertNotIn(date(2026, 12, 7), f)

    def test_patrono(self):
        c = cal.Calendario("12-07")
        self.assertEqual(c.festivo(date(2026, 12, 7)), "Patrono")
        self.assertIsNone(cal.Calendario().festivo(date(2026, 12, 7)))
        self.assertIsNone(cal.Calendario("02-29").festivo(date(2025, 2, 28)))    # non bisestile: niente
        self.assertEqual(cal.Calendario("02-29").festivo(date(2024, 2, 29)), "Patrono")

    def test_parse_patrono(self):
        self.assertEqual(cal.parse_patrono("12-07"), (12, 7))
        self.assertIsNone(cal.parse_patrono(None))
        for brutto in ("13-45", "boh", "12/07", "12-7-1"):
            with self.assertRaises(ValueError, msg=brutto):
                cal.parse_patrono(brutto)


class CategorieTest(unittest.TestCase):
    def test_date_che_contano(self):
        c = cal.Calendario()
        casi = {
            date(2026, 4, 6): "festivo infrasettimanale",        # Pasquetta, lunedi'
            date(2026, 4, 25): "sabato",                        # Liberazione di sabato: resta sabato
            date(2026, 6, 2): "festivo infrasettimanale",       # Repubblica, martedi'
            date(2026, 6, 1): "ponte probabile",                # lunedi' tra la domenica e la Repubblica
            date(2026, 6, 3): "feriale",
            date(2025, 5, 1): "festivo infrasettimanale",       # giovedi'
            date(2025, 5, 2): "ponte probabile",                # venerdi' tra il 1 maggio e il sabato
            date(2025, 12, 26): "festivo infrasettimanale",     # Santo Stefano, venerdi'
            date(2025, 12, 29): "feriale",
            date(2026, 10, 28): "feriale",                      # mercoledi'
            date(2026, 10, 31): "sabato",
            date(2026, 11, 1): "domenica",                      # Ognissanti di domenica
        }
        for d, atteso in casi.items():
            self.assertEqual(c.categoria(d), atteso, d.isoformat())

    def test_ogni_giorno_ha_una_categoria_valida(self):
        c = cal.Calendario("12-07")
        d = date(2023, 1, 1)
        while d <= date(2026, 12, 31):
            self.assertIn(c.categoria(d), cal.CATEGORIE)
            d += timedelta(days=1)


class StatisticheTest(unittest.TestCase):
    def test_auc(self):
        self.assertAlmostEqual(cal.auc([2, 3], [1, 2]), 0.875)
        self.assertEqual(cal.auc([1, 2, 3], [1, 2, 3]), 0.5)
        self.assertEqual(cal.auc([10, 11], [1, 2]), 1.0)
        self.assertEqual(cal.auc([1, 2], [10, 11]), 0.0)
        self.assertTrue(math.isnan(cal.auc([], [1])))

    def test_soglia(self):
        soglia, bil, acc = cal.soglia_migliore([10, 12, 14], [1, 2, 3])
        self.assertEqual((soglia, bil, acc), (10, 1.0, 1.0))
        self.assertIsNone(cal.soglia_migliore([], [1]))
        t, bil, _ = cal.soglia_migliore([5, 6, 7, 8], [5, 6, 7, 8])
        self.assertAlmostEqual(bil, 0.5, delta=0.26)


def storia(separata=True, anni=3, seed=3):
    """Giorni sintetici dal 22/03/2023: lavorativi ~150 L, non lavorativi ~230 L (o uguali)."""
    rnd = random.Random(seed)
    c = cal.Calendario()
    out, d = [], date(2023, 3, 22)
    while d <= date(2023, 3, 22) + timedelta(days=365 * anni):
        nl = c.non_lavorativo(d)
        base = (230 if nl and separata else 150)
        litri = 0 if rnd.random() < 0.05 else max(10, round(rnd.gauss(base, 20) / 10) * 10)
        out.append({"data": d.isoformat(), "litri": litri, "rigenerazioni": 0})
        d += timedelta(days=1)
    return out


class CorrelazioneTest(unittest.TestCase):
    def test_con_separazione_netta(self):
        r = cal.correlazione(storia(True))
        self.assertGreater(r["auc"], 0.95)
        self.assertGreater(r["soglia"]["accuratezza"], 0.9)
        cat = r["categorie"]
        self.assertGreater(cat["domenica"]["media"], cat["feriale"]["media"] + 60)
        self.assertGreater(cat["festivo infrasettimanale"]["media"], cat["feriale"]["media"] + 60)
        self.assertGreater(cat["ponte probabile"]["giorni"], 0)
        self.assertGreater(sum(v["a_zero"] for v in cat.values()), 20)          # le assenze sono contate a parte
        self.assertEqual(sum(v["giorni"] for v in cat.values()), r["giorni"])

    def test_senza_separazione_l_auc_resta_vicino_a_un_mezzo(self):
        r = cal.correlazione(storia(False, anni=4))
        self.assertLess(abs(r["auc"] - 0.5), 0.1)
        self.assertLess(r["soglia"]["accuratezza_bilanciata"], 0.65)

    def test_dal_esclude_i_giorni_precedenti(self):
        giorni = storia(True) + [{"data": "2023-01-10", "litri": 0, "rigenerazioni": 0}]
        giorni.sort(key=lambda g: g["data"])
        self.assertEqual(cal.correlazione(giorni, "2023-03-22")["giorni"], len(giorni) - 1)

    def test_giorni_dalla_parte_sbagliata(self):
        giorni = storia(True)
        for g in giorni:
            if g["data"] == "2024-05-15":
                g["litri"] = 400                   # un mercoledi' con consumo da festivo
            if g["data"] == "2024-05-19":
                g["litri"] = 30                    # una domenica con consumo da feriale
        r = cal.correlazione(giorni)
        self.assertIn(("2024-05-15", 400), r["feriali_da_festivo"])
        self.assertIn(("2024-05-19", 30), r["festivi_da_feriale"])

    def test_pochi_dati_e_nessun_dato(self):
        r = cal.correlazione([{"data": "2026-10-28", "litri": 100, "rigenerazioni": 0}])
        self.assertIsNone(r["auc"])
        self.assertIn("Troppo pochi", cal.righe_correlazione(r)[-1])
        self.assertEqual(cal.righe_correlazione(cal.correlazione([])), ["Nessun giorno da analizzare."])

    def test_testo_in_formato_europeo(self):
        giorni = storia(True)
        for g in giorni:
            if g["data"] == "2024-05-15":
                g["litri"] = 400
        testo = "\n".join(cal.righe_correlazione(cal.correlazione(giorni)))
        self.assertIn("15/05/2024 400 L", testo)
        self.assertIn("ponte probabile", testo)
        self.assertNotRegex(testo, r"\b\d{4}-\d{2}-\d{2}\b")


class ComandoTest(unittest.TestCase):
    def esegui(self, d, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bwt_perla.cmd_analisi_calendario(d, kw.get("file"), kw.get("da"), kw.get("patrono"))
        return out.getvalue()

    def righe_csv(self, d):
        giorni = [{"indice": i, "inizio_slot": f"{g['data']}T02:45", "data": g["data"], "litri": g["litri"],
                   "rigenerazioni": 0, "blackout": 0} for i, g in enumerate(storia(True))]
        storico.scrivi_csv(Path(d) / "storico_giorni_20261029.csv", storico.GIORNI_COLONNE, giorni)

    def test_da_file_con_data_e_patrono(self):
        with tempfile.TemporaryDirectory() as d:
            self.righe_csv(d)
            testo = self.esegui(d, da="2023-03-22", patrono="12-07")
            self.assertEqual(carica_config(d)["messa_in_servizio"], "2023-03-22")
            for atteso in ("patrono: 12-07", "feriale", "AUC", "accuratezza"):
                self.assertIn(atteso, testo)
            self.assertNotIn("Suggerimento", testo)

    def test_senza_data_suggerisce(self):
        with tempfile.TemporaryDirectory() as d:
            self.righe_csv(d)
            self.assertIn("--da AAAA-MM-GG", self.esegui(d))

    def test_errori(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                self.esegui(d)                                  # nessun file
            self.righe_csv(d)
            with self.assertRaises(SystemExit):
                self.esegui(d, patrono="13-45")


if __name__ == "__main__":
    unittest.main()
