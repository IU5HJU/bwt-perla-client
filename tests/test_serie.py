import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import storico  # noqa: E402
import web  # noqa: E402
from tracker import QUARTI_COLONNE, QUARTI_CSV  # noqa: E402

QUARTO = timedelta(minutes=15)
N = 2880
IMPORT = datetime(2026, 10, 29, 7, 24)            # momento dell'importazione dello storico
IDX_FINE = 2607                                    # indice del registro dei quarti alla lettura


def litri_quarto(t: datetime) -> int:
    """Profilo sintetico: 100 L ogni giorno, in 4 quarti della sera."""
    return 25 if t.hour == 20 and t.minute < 60 else 0


def storico_quarti(shift=timedelta(0)):
    """30 giorni di quarti dello storico: ogni riga con l'indice assoluto del registro
    e l'orario approssimato assegnato all'importazione."""
    fine = IMPORT.replace(minute=IMPORT.minute - IMPORT.minute % 15, second=0, microsecond=0)
    righe = []
    for j in range(N):
        t = fine - QUARTO * (N - j)
        righe.append({"indice": (IDX_FINE - N + j) % N, "inizio_approx": t.isoformat(timespec="minutes"),
                      "litri": litri_quarto(t), "rigenerazione": 0, "blackout": 0})
    return righe


def live_da(righe_storico, dal_indice_pos, shift):
    """Quarti 'dal vivo': gli stessi quarti dell'apparecchio (stessi indici) dall'indice
    dato in poi, con l'orario sfalsato di `shift` rispetto allo storico."""
    return [{"inizio_approx": (datetime.fromisoformat(r["inizio_approx"]) + shift).isoformat(timespec="minutes"),
             "idx": r["indice"], "litri": r["litri"], "rigenerazione": 0, "blackout": 0}
            for r in righe_storico[dal_indice_pos:]]


class UnioneTest(unittest.TestCase):
    def test_aggancio_per_indice_senza_buchi_ne_duplicati(self):
        h = storico_quarti()
        live = live_da(h, N - 50, timedelta(minutes=15))        # ultimi 50 quarti, orari +15 min
        unione = storico.unisci_quarti(live, h)
        self.assertEqual(len(unione), N)                         # nessun quarto doppio o perso
        tempi = [t for t, _ in unione]
        self.assertEqual(tempi, sorted(tempi))
        self.assertTrue(all(b - a == QUARTO for a, b in zip(tempi, tempi[1:])), "orari non consecutivi")
        # gli orari dal vivo non cambiano e quelli piu' vecchi li continuano a ritroso
        self.assertEqual(tempi[-1], datetime.fromisoformat(live[-1]["inizio_approx"]))
        self.assertEqual(tempi[0], datetime.fromisoformat(live[0]["inizio_approx"]) - QUARTO * (N - 50))

    def test_senza_storico_o_senza_aggancio(self):
        h = storico_quarti()
        live = live_da(h, N - 5, timedelta(0))
        self.assertEqual(len(storico.unisci_quarti(live, [])), 5)
        self.assertEqual(len(storico.unisci_quarti([], h)), N)                 # solo storico: orari suoi
        # storico parziale (i primi 100 quarti): nessun indice dal vivo vi compare,
        # quindi niente aggancio, e ognuno tiene i propri orari
        parziale = h[:100]
        self.assertEqual(len(storico.unisci_quarti(live, parziale)), 105)
        # con un aggancio e lo stesso orario il dato dal vivo prende il posto
        self.assertEqual(len(storico.unisci_quarti(live, h)), N)

    def test_il_dato_dal_vivo_vince(self):
        h = storico_quarti()
        live = live_da(h, N - 5, timedelta(0))
        for r in live:
            r["litri"] = 999
        unione = dict(storico.unisci_quarti(live, h))
        self.assertTrue(all(unione[datetime.fromisoformat(r["inizio_approx"])] == 999 for r in live))


class SerieTest(unittest.TestCase):
    def test_giorni_completi_dai_quarti_e_giorno_incompleto_dallo_storico_giornaliero(self):
        h = storico_quarti()
        live = live_da(h, N - 50, timedelta(0))
        # il primo giorno e' a meta': lo storico giornaliero ha il valore intero (130 L)
        primo = datetime.fromisoformat(h[0]["inizio_approx"]).date().isoformat()
        giorni_s = [{"data": primo, "litri": "130", "rigenerazioni": "2"}]
        s = storico.serie_litri(live, h, giorni_s)
        self.assertEqual(s["giorni"][primo], {"litri": 130, "rigenerazioni": 2})
        centrale = (datetime.fromisoformat(h[0]["inizio_approx"]) + timedelta(days=5)).date().isoformat()
        self.assertEqual(s["giorni"][centrale], {"litri": 100, "rigenerazioni": None})
        self.assertEqual(sum(s["ore"].values()), sum(r["litri"] for r in h))

    def test_senza_storico_giornaliero_si_usa_la_somma_parziale(self):
        h = storico_quarti()
        s = storico.serie_litri([], h, [])
        primo = datetime.fromisoformat(h[0]["inizio_approx"]).date().isoformat()
        self.assertLessEqual(s["giorni"][primo]["litri"], 100)


class PaginaConStoricoTest(unittest.TestCase):
    def test_i_grafici_si_popolano_dal_passato(self):
        h = storico_quarti()
        live = live_da(h, N - 50, timedelta(minutes=15))
        with tempfile.TemporaryDirectory() as d:
            cartella = Path(d)
            storico.scrivi_csv(cartella / QUARTI_CSV, QUARTI_COLONNE, live)
            ora = datetime(2026, 10, 29, 8, 0)
            senza = web.riepilogo(d, ora)
            self.assertEqual(len(senza["giorni"]), 2)                # solo la registrazione: 12 ore a cavallo di mezzanotte
            storico.scrivi_csv(cartella / "storico_quarti_20261029.csv", storico.QUARTI_COLONNE, h)
            storico.scrivi_csv(cartella / "storico_giorni_20261029.csv", storico.GIORNI_COLONNE, [])
            con = web.riepilogo(d, ora)
        self.assertGreaterEqual(len(con["giorni"]), 29)              # 30 giorni di storico
        self.assertEqual(con["giorni"][-6]["litri"], 100)            # un giorno intero di sette giorni fa
        self.assertEqual(len(con["ore"]), 168)
        settimana = sum(o["litri"] for o in con["ore"])
        self.assertGreaterEqual(settimana, 600)                      # ~7 sere da 100 L
        self.assertEqual(con["media_litri_giorno_7g"], 100)          # prima era la media di un giorno a meta'
        self.assertGreater(settimana, sum(o["litri"] for o in senza["ore"]))


if __name__ == "__main__":
    unittest.main()
