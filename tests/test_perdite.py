import contextlib
import io
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bwt_perla  # noqa: E402
import perdite  # noqa: E402
import storico  # noqa: E402

Q = timedelta(minutes=15)


def serie_giorni(giorni, notte=None, regen=None, extra=None):
    """Quarti d'ora di `giorni` giorni (96 al giorno) che finiscono alla mezzanotte
    dopo il primo giorno + giorni. `notte(giorno_index, ora_quarto)` -> litri."""
    inizio = datetime(2026, 9, 1)
    righe = []
    for g in range(giorni):
        for k in range(96):
            t = inizio + timedelta(days=g) + Q * k
            litri = 0
            if extra:
                litri = extra(g, k)
            if notte and 4 <= k < 20:
                litri = notte(g, k)
            e = bool(regen and regen(g, k))
            righe.append((t, litri, e))
    return righe


class NotteTest(unittest.TestCase):
    def test_notte_silenziosa_e_ok(self):
        ns = perdite.notti(serie_giorni(3))
        self.assertEqual(len(ns), 3)
        self.assertTrue(all(n["giudizio"] == "ok" and n["attivi"] == 0 and n["silenzio_q"] == 96 for n in ns))

    def test_perdita_continua_ogni_notte(self):
        ns = perdite.notti(serie_giorni(4, notte=lambda g, k: 1))
        self.assertTrue(all(n["giudizio"] == "continuo" and n["litri"] == 16 for n in ns))
        # un filo d'acqua che non si ferma mai spezza ogni silenzio, anche di giorno
        sempre = perdite.notti(serie_giorni(4, extra=lambda g, k: 1 if k % 3 == 0 else 0,
                                            notte=lambda g, k: 1 if k % 3 == 0 else 0))
        self.assertTrue(all(n["silenzio_q"] <= 2 for n in sempre))

    def test_perdita_a_meta_notte_e_sospetta(self):
        ns = perdite.notti(serie_giorni(2, notte=lambda g, k: 1 if k % 2 == 0 else 0))
        self.assertEqual({n["giudizio"] for n in ns}, {"sospetto"})

    def test_un_bagno_non_e_una_perdita(self):
        ns = perdite.notti(serie_giorni(2, notte=lambda g, k: 6 if k == 12 else 0))
        self.assertEqual({n["giudizio"] for n in ns}, {"ok"})

    def test_notte_con_rigenerazione_scartata(self):
        ns = perdite.notti(serie_giorni(2, notte=lambda g, k: 40 if k == 10 else 0,
                                        regen=lambda g, k: g == 0 and k == 10))
        self.assertEqual(ns[0]["scartata"], "rigenerazione o interruzione")
        self.assertIsNone(ns[0]["giudizio"])
        self.assertIsNone(ns[1]["scartata"])

    def test_giorno_incompleto_ignorato(self):
        self.assertEqual(perdite.notti(serie_giorni(2)[:-3]).__len__(), 1)

    def test_silenzio_massimo(self):
        self.assertEqual(perdite.silenzio_massimo([0, 0, 3, 0, 0, 0, 1]), 3)
        self.assertEqual(perdite.silenzio_massimo([]), 0)


class GiornoTest(unittest.TestCase):
    def lavastoviglie(self, g, k):
        # lavaggio 10:00-12:00 con consumo, poi 2 h di ciclo a secco e 1 h di asciugatura
        return {40: 4, 41: 0, 42: 12, 43: 0, 44: 0, 45: 9, 46: 6, 47: 0}.get(k, 0)

    def test_strisce_senza_consumo(self):
        quarti = [0] * 96
        for k in (40, 42, 45, 46):
            quarti[k] = 5
        self.assertEqual(perdite.silenzi_giorno(quarti), [(0, 40), (47, 96)])
        self.assertEqual(perdite.silenzi_giorno([0, 1, 0, 0, 0, 0, 2], minimo=4), [(2, 6)])

    def test_tabella_del_giorno(self):
        s = serie_giorni(1, extra=self.lavastoviglie)
        testo = "\n".join(perdite.righe_giorno(s, date(2026, 9, 1)))
        self.assertIn("01/09/2026", testo)
        self.assertIn("10   ", testo)
        self.assertIn("Totale: 31 L", testo)
        self.assertIn("00:00-10:00", testo)
        self.assertIn("11:45-24:00", testo)

    def test_giorno_mancante(self):
        self.assertIn("nessun quarto", perdite.righe_giorno([], date(2026, 9, 1))[0])


def giorni_csv(n, minimo_ultimi=None, regen_ogni=0):
    inizio = date(2026, 1, 1)
    righe = []
    for i in range(n):
        litri = 120 + (i % 5) * 30
        if i % 7 == 6:
            litri = 20                                  # un giorno quasi fermo a settimana
        if minimo_ultimi is not None and i >= n - 15:
            litri = max(litri, minimo_ultimi)           # perdita: il pavimento sale
        righe.append({"data": (inizio + timedelta(days=i)).isoformat(), "litri": litri,
                      "rigenerazioni": 1 if regen_ogni and i % regen_ogni == 0 else 0, "blackout": 0})
    return righe


class PavimentoTest(unittest.TestCase):
    def test_normale(self):
        p = perdite.pavimento(giorni_csv(200))
        self.assertFalse(p["record"])
        self.assertEqual(p["ultima"]["minimo"], 20)

    def test_pavimento_che_sale_e_un_record(self):
        p = perdite.pavimento(giorni_csv(200, minimo_ultimi=90))
        self.assertTrue(p["record"])
        self.assertGreater(p["ultima"]["minimo"], p["massimo"])

    def test_storico_corto(self):
        p = perdite.pavimento(giorni_csv(40))
        self.assertNotIn("record", p)
        testo = "\n".join(perdite.righe_analisi([], giorni_csv(40), None))
        self.assertIn("storico troppo corto", testo)

    def test_giorni_con_rigenerazione_non_contano(self):
        giorni = giorni_csv(200)
        for g in giorni:
            if g["litri"] == 20:
                g["rigenerazioni"] = 1
        self.assertGreater(perdite.pavimento(giorni)["ultima"]["minimo"], 20)

    def test_dal_esclude_i_giorni_precedenti(self):
        giorni = giorni_csv(200)
        p = perdite.pavimento(giorni, "2026-06-01")
        self.assertLess(p["finestre"], 12)

    def test_vuoto(self):
        self.assertEqual(perdite.pavimento([]), {})


class RapportoTest(unittest.TestCase):
    def test_tre_notti_anomale_avvisano(self):
        testo = "\n".join(perdite.righe_analisi(serie_giorni(5, notte=lambda g, k: 1), None, None))
        self.assertIn("SPERIMENTALE", testo)
        self.assertIn("ATTENZIONE: le ultime 3 notti", testo)

    def test_notti_normali_non_avvisano(self):
        testo = "\n".join(perdite.righe_analisi(serie_giorni(5), None, None))
        self.assertNotIn("ATTENZIONE", testo)
        self.assertIn("ok: 5", testo)

    def test_una_sola_notte_anomala_non_basta(self):
        testo = "\n".join(perdite.righe_analisi(
            serie_giorni(5, notte=lambda g, k: 1 if g == 4 else 0), None, None))
        self.assertNotIn("ATTENZIONE", testo)
        self.assertIn("una sola notte non basta", testo)

    def test_senza_quarti(self):
        self.assertIn("nessun giorno completo", "\n".join(perdite.righe_analisi([], None, None)))


class SerieTest(unittest.TestCase):
    def test_flag_di_rigenerazione_segue_il_quarto(self):
        inizio = datetime(2026, 9, 1, 0, 0)
        live = [{"inizio_approx": (inizio + Q * j).isoformat(timespec="minutes"), "idx": str(j),
                 "litri": 3 if j == 2 else 0, "rigenerazione": "1" if j == 2 else "0", "blackout": "0"}
                for j in range(4)]
        s = perdite.serie_con_esclusioni(live, [])
        self.assertEqual([e for _, _, e in s], [False, False, True, False])
        self.assertEqual([l for _, l, _ in s], [0, 0, 3, 0])


class ComandoTest(unittest.TestCase):
    def esegui(self, d, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bwt_perla.cmd_analisi_perdite(d, None, None, kw.get("da"), kw.get("giorno"), kw.get("notti", 14))
        return out.getvalue()

    def scrivi(self, d, leak):
        s = serie_giorni(6, notte=(lambda g, k: 1) if leak else None)
        righe = [{"indice": i, "inizio_approx": t.isoformat(timespec="minutes"), "litri": l,
                  "rigenerazione": int(e), "blackout": 0} for i, (t, l, e) in enumerate(s)]
        storico.scrivi_csv(Path(d) / "storico_quarti_20260907.csv", storico.QUARTI_COLONNE, righe)
        storico.scrivi_csv(Path(d) / "storico_giorni_20260907.csv", storico.GIORNI_COLONNE,
                           [{"indice": i, "inizio_slot": "", "data": g["data"], "litri": g["litri"],
                             "rigenerazioni": 0, "blackout": 0} for i, g in enumerate(giorni_csv(30))])

    def test_rapporto_con_perdita(self):
        with tempfile.TemporaryDirectory() as d:
            self.scrivi(d, True)
            testo = self.esegui(d, da="2026-01-01")
            self.assertIn("continuo", testo)
            self.assertIn("ATTENZIONE", testo)

    def test_rapporto_senza_perdita(self):
        with tempfile.TemporaryDirectory() as d:
            self.scrivi(d, False)
            self.assertNotIn("ATTENZIONE", self.esegui(d))

    def test_giorno(self):
        with tempfile.TemporaryDirectory() as d:
            self.scrivi(d, False)
            self.assertIn("02/09/2026", self.esegui(d, giorno="2026-09-02"))

    def test_giorno_non_valido(self):
        with tempfile.TemporaryDirectory() as d:
            self.scrivi(d, False)
            with self.assertRaises(SystemExit):
                self.esegui(d, giorno="domani")

    def test_senza_dati(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                self.esegui(d)


if __name__ == "__main__":
    unittest.main()
