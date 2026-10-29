import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import storico  # noqa: E402

Q = timedelta(minutes=15)


def righe(n, idx0=2600, salti=(), litri=lambda j: 1):
    """`n` quarti consecutivi dal 07:00; `salti` = {posizione: quarti da aggiungere all'etichetta
    da quella riga in poi}."""
    t0, extra, out = datetime(2026, 10, 29, 7, 0), 0, []
    salti = dict(salti)
    for j in range(n):
        extra += salti.get(j, 0)
        out.append({"inizio_approx": (t0 + Q * (j + extra)).isoformat(timespec="minutes"),
                    "idx": str((idx0 + j) % storico.QUARTI_CIRCOLARI), "litri": str(litri(j)),
                    "rigenerazione": "0", "blackout": "0"})
    return out


def orari(r):
    return [x["inizio_approx"] for x in r]


class RiallineaTest(unittest.TestCase):
    def test_serie_pulita_non_cambia(self):
        r = righe(60)
        self.assertEqual(storico.riallinea_live(r), r)

    def test_buco_isolato_si_chiude(self):
        # un solo quarto etichettato un quarto d'ora dopo, poi di nuovo regolare
        r = righe(80)
        r[20] = {**r[20], "inizio_approx": (datetime.fromisoformat(r[20]["inizio_approx"]) + Q)
                 .isoformat(timespec="minutes")}
        out = storico.riallinea_live(r)
        self.assertEqual(orari(out), orari(righe(80)))

    def test_cambio_di_scarto_persistente_toglie_il_buco(self):
        # il caso reale: 07:00 07:15 07:30 poi 08:00 (manca 07:45) e tutto il resto regolare
        r = righe(90, salti={3: 1})
        out = storico.riallinea_live(r)
        t = [datetime.fromisoformat(x) for x in orari(out)]
        self.assertTrue(all(b - a == Q for a, b in zip(t, t[1:])), orari(out)[:6])

    def test_orari_doppi_non_perdono_litri(self):
        # due letture che danno la stessa etichetta a due quarti diversi: prima uno spariva
        r = righe(90, salti={3: -1})
        senza = {datetime.fromisoformat(x["inizio_approx"]) for x in r}
        self.assertLess(len(senza), 90)                 # il doppione c'e'
        out = storico.riallinea_live(r)
        self.assertEqual(len({x["inizio_approx"] for x in out}), 90)
        self.assertEqual(sum(l for _, l in storico.unisci_quarti(out, [])), 90)

    def test_scarto_grande_resta_com_e(self):
        # registrazione ferma per ore: l'indice avanza piu' dell'orario? qui lo scarto e' di 3 quarti
        r = righe(90, salti={40: 3})
        out = storico.riallinea_live(r)
        self.assertEqual(orari(out)[40:], orari(r)[40:])

    def test_indice_che_riparte_da_zero(self):
        r = righe(80, idx0=2840)                        # attraversa 2879 -> 0
        self.assertEqual(storico.riallinea_live(r), r)
        r[50] = {**r[50], "inizio_approx": (datetime.fromisoformat(r[50]["inizio_approx"]) + Q)
                 .isoformat(timespec="minutes")}
        self.assertEqual(orari(storico.riallinea_live(r)), orari(righe(80, idx0=2840)))

    def test_poche_righe(self):
        r = righe(2)
        self.assertEqual(storico.riallinea_live(r), r)
        self.assertEqual(storico.riallinea_live([]), [])

    def test_unisci_quarti_senza_buchi(self):
        r = righe(90, salti={3: 1})
        out = storico.unisci_quarti(r, [])
        t = [x for x, _ in out]
        self.assertTrue(all(b - a == Q for a, b in zip(t, t[1:])))

    def test_aggancio_con_lo_storico_resta_coerente(self):
        r = righe(90, salti={3: 1})
        sq = [{"indice": str(2560 + j), "inizio_approx": "2026-10-01T00:00", "litri": "5",
               "rigenerazione": "0", "blackout": "0"} for j in range(80)]   # 2560..2639: copre le prime 40 live
        out = storico.unisci_quarti(r, sq)
        t = [x for x, _ in out]
        self.assertTrue(all(b - a == Q for a, b in zip(t, t[1:])))
        self.assertEqual(len(out), 90 + 40)             # 40 quarti piu' vecchi dello storico


if __name__ == "__main__":
    unittest.main()
