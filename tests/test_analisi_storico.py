import contextlib
import io
import random
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bwt_perla  # noqa: E402
import storico  # noqa: E402
from config import carica_config  # noqa: E402

INIZIO, SERVIZIO, FINE = date(2021, 10, 30), date(2023, 3, 22), date(2026, 10, 28)


def giorni_sintetici():
    """1825 giorni: a zero prima della messa in servizio, poi feriali ~170 L e
    fine settimana ~225 L, con qualche assenza e una rigenerazione ogni ~4,5 giorni."""
    rnd = random.Random(7)
    righe, d = [], INIZIO
    while d <= FINE:
        in_servizio = d >= SERVIZIO
        litri = 0
        if in_servizio and rnd.random() > 0.10:                      # ~10% assenze
            litri = rnd.choice([150, 170, 190] if d.weekday() < 5 else [210, 230, 240])
        righe.append({"indice": len(righe), "inizio_slot": f"{d}T05:00", "data": d.isoformat(),
                      "litri": litri, "rigenerazioni": 1 if in_servizio and rnd.random() < 0.22 else 0,
                      "blackout": 0})
        d += timedelta(days=1)
    return righe


def quarti_sintetici(giorni=30):
    """Ultimi 30 giorni a quarti d'ora: feriali = 6:00 poco + doccia 22:30-23:00;
    fine settimana = usi distribuiti di giorno."""
    fine = datetime(2026, 10, 29, 0, 0)               # giorni interi, fino al 28/10 compreso
    t = fine - timedelta(days=giorni)
    righe = []
    while t < fine:
        h = t.hour + t.minute / 60
        if t.weekday() < 5:
            litri = (3 if 6 <= h < 6.5 else 0) + (50 if 22.5 <= h < 23 else 0)
        else:
            litri = 6 if 9 <= h < 21 and t.minute == 0 else 0
        righe.append({"indice": len(righe), "inizio_approx": t.isoformat(timespec="minutes"),
                      "litri": litri, "rigenerazione": 0, "blackout": 0})
        t += timedelta(minutes=15)
    return righe


class RiepilogoTest(unittest.TestCase):
    def test_esclude_i_giorni_prima_della_messa_in_servizio(self):
        r = storico.riepilogo(giorni_sintetici(), "2023-03-22")
        self.assertEqual((r["giorni_esclusi"], r["giorni"]), (508, 1317))
        self.assertEqual((r["da"], r["a"]), ("2023-03-22", "2026-10-28"))
        self.assertLess(r["giorni_a_zero"], 200)                      # solo le assenze
        self.assertGreater(r["media_giorni_con_consumo"], r["media_litri"])
        self.assertEqual(sorted(r["per_anno"]), [2023, 2024, 2025, 2026])
        self.assertEqual(sum(a["giorni"] for a in r["per_anno"].values()), 1317)
        self.assertEqual(r["per_anno"][2023]["giorni"], 285)          # 22 marzo - 31 dicembre
        self.assertEqual(sum(m["giorni"] for m in r["per_mese_dell_anno"].values()), 1317)

    def test_senza_data_i_zeri_iniziali_falsano_la_media(self):
        con = storico.riepilogo(giorni_sintetici(), "2023-03-22")
        senza = storico.riepilogo(giorni_sintetici())
        self.assertEqual(senza["giorni"], 1825)
        self.assertLess(senza["media_litri"], con["media_litri"] * 0.8)

    def test_fine_settimana_piu_alto_dei_feriali(self):
        gs = storico.riepilogo(giorni_sintetici(), "2023-03-22")["per_giorno_settimana"]
        feriali = sum(gs[n]["media"] for n in ("lun", "mar", "mer", "gio", "ven")) / 5
        weekend = (gs["sab"]["media"] + gs["dom"]["media"]) / 2
        self.assertGreater(weekend, feriali * 1.2)

    def test_il_picco_del_fine_settimana_sta_su_sab_dom_in_ogni_anno(self):
        r = storico.riepilogo(giorni_sintetici(), "2023-03-22")
        for anno, v in r["per_anno_giorno_settimana"].items():
            primi = sorted(v, key=v.get, reverse=True)[:2]
            self.assertEqual(set(primi), {"sab", "dom"}, anno)

    def test_giorni_con_piu_rigenerazioni_e_blackout(self):
        righe = giorni_sintetici()
        for r in righe:
            if r["data"] == "2023-05-31":
                r.update(rigenerazioni=3, litri=490, blackout=1)
        r = storico.riepilogo(righe, "2023-03-22")
        self.assertEqual(r["giorni_con_piu_rigenerazioni"],
                         [{"data": "2023-05-31", "litri": 490, "rigenerazioni": 3}])
        self.assertEqual(r["giorni_blackout"], 1)
        testo = "\n".join(storico.righe_riepilogo(r))
        self.assertIn("2023-05-31 (3 rigen., 490 L)", testo)
        self.assertIn("anno per anno", testo)

    def test_tutto_prima_della_messa_in_servizio(self):
        self.assertEqual(storico.riepilogo(giorni_sintetici(), "2030-01-01"), {"giorni_esclusi": 1825})
        self.assertEqual(storico.riepilogo([], "2023-03-22"), {})


class ProfiloOrarioTest(unittest.TestCase):
    def test_la_routine_si_vede(self):
        p = storico.profilo_orario(quarti_sintetici())
        self.assertGreater(p["giorni"]["feriali"], 15)
        self.assertGreater(p["giorni"]["weekend"], 6)
        f, w = p["feriali"], p["weekend"]
        self.assertEqual(f[6], 6.0)                    # poco alle 6: due quarti da 3 L
        self.assertEqual(f[22], 100.0)                 # la doccia 22:30-23:00: due quarti da 50 L
        self.assertEqual(f[12], 0.0)                   # giorno vuoto
        self.assertEqual(w[12], 6.0)                   # il weekend si usa di giorno
        self.assertEqual(w[22], 0.0)

    def test_giorni_incompleti_non_contano(self):
        q = quarti_sintetici()
        self.assertEqual(q[0]["inizio_approx"][:10], "2026-09-29")      # un martedi'
        # 100 quarti = 1 giorno intero (96) + 4 quarti del successivo: conta solo il primo
        self.assertEqual(storico.profilo_orario(q[:100])["giorni"], {"feriali": 1, "weekend": 0})
        self.assertEqual(storico.profilo_orario(q[:50])["giorni"], {"feriali": 0, "weekend": 0})
        self.assertEqual(storico.profilo_orario([])["giorni"], {"feriali": 0, "weekend": 0})


class CommandoTest(unittest.TestCase):
    def test_analisi_da_file_salva_la_messa_in_servizio(self):
        with tempfile.TemporaryDirectory() as d:
            storico.scrivi_csv(Path(d) / "storico_giorni_20261029.csv", storico.GIORNI_COLONNE, giorni_sintetici())
            storico.scrivi_csv(Path(d) / "storico_quarti_20261029.csv", storico.QUARTI_COLONNE, quarti_sintetici())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bwt_perla.cmd_analisi_storico(d, None, None, "2023-03-22")
            testo = out.getvalue()
            self.assertEqual(carica_config(d)["messa_in_servizio"], "2023-03-22")
            for atteso in ("Esclusi 508 giorni", "1317 giorni in servizio", "Per anno", "Per mese dell'anno",
                           "Profilo orario", "22   "):
                self.assertIn(atteso, testo)
            # la seconda volta la data si legge dalla configurazione
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bwt_perla.cmd_analisi_storico(d, None, None, None)
            self.assertIn("Esclusi 508 giorni", out.getvalue())
            self.assertNotIn("Messa in servizio salvata", out.getvalue())

    def test_senza_data_suggerisce_di_indicarla(self):
        with tempfile.TemporaryDirectory() as d:
            storico.scrivi_csv(Path(d) / "storico_giorni_20261029.csv", storico.GIORNI_COLONNE, giorni_sintetici())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                bwt_perla.cmd_analisi_storico(d, None, None, None)
            self.assertIn("--da AAAA-MM-GG", out.getvalue())

    def test_data_non_valida_e_file_mancanti(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                bwt_perla.cmd_analisi_storico(d, None, None, None)           # nessun file
            storico.scrivi_csv(Path(d) / "storico_giorni_20261029.csv", storico.GIORNI_COLONNE, giorni_sintetici())
            with self.assertRaises(SystemExit):
                bwt_perla.cmd_analisi_storico(d, None, None, "22/03/2023")   # formato sbagliato


if __name__ == "__main__":
    unittest.main()
