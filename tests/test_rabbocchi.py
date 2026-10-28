import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web  # noqa: E402
from tracker import (Tracker, analisi_sale, annulla_ultimo_rabbocco,  # noqa: E402
                     carica_rabbocchi, cicli_rabbocco, registra_rabbocco)


def rab(rig, kg_dopo=14, agg=None, liv="sconosciuto"):
    return {"data": "2026-10-28T10:00:00", "kg_dopo": kg_dopo, "kg_aggiunti": agg,
            "livello_prima": liv, "rigenerazioni_totali": rig}


# primo rabbocco a 0 rigenerazioni; secondo dopo 10 rigenerazioni: si sono
# versati 10 kg (consumo reale 1000 g/rigenerazione, residuo 4 kg in riserva)
STORIA = [rab(0), rab(10, agg=10, liv="riserva")]


def stato(rig_totali, rate=0.5, rabbocchi=STORIA):
    return analisi_sale(rabbocchi, rig_totali, 1200, 14000, rate)


class AnalisiTest(unittest.TestCase):
    def test_nessun_rabbocco(self):
        self.assertIsNone(analisi_sale([], 5, 1200, 14000, 0.5))

    def test_ciclo_misurato(self):
        c = cicli_rabbocco(STORIA)
        self.assertEqual(c, [{"rigenerazioni": 10, "consumato_kg": 10,
                              "residuo_kg": 4, "livello_prima": "riserva"}])

    def test_calibrazione_e_stati(self):
        a = stato(10 + 3)             # 3 rigenerazioni dopo il secondo rabbocco
        self.assertEqual(a["g_per_rigenerazione"], 1000)
        self.assertTrue(a["g_calibrato"] and a["riserva_calibrata"])
        self.assertEqual(a["riserva_g"], 4000)
        self.assertEqual(a["residuo_g"], 14000 - 3 * 1000)
        self.assertEqual(a["stato"], "ok")
        self.assertEqual(a["giorni_a_riserva"], 14)   # (11000-4000)/(0.5*1000)
        self.assertEqual(stato(10 + 8)["stato"], "avviso")   # 4 giorni
        self.assertEqual(stato(10 + 10)["stato"], "riserva")
        self.assertEqual(stato(10 + 12)["residuo_g"], 2000)  # mai negativo, mai 0 fittizio prima

    def test_senza_calibrazione_usa_valori_provvisori(self):
        a = stato(2, rabbocchi=[rab(0)])
        self.assertEqual(a["g_per_rigenerazione"], 1200)
        self.assertFalse(a["g_calibrato"] or a["riserva_calibrata"])
        self.assertEqual(a["riserva_g"], 3500)       # 25% di 14 kg
        self.assertEqual(a["residuo_g"], 14000 - 2 * 1200)

    def test_ciclo_incoerente_scartato(self):
        # 12 kg per 3 rigenerazioni = 4000 g, 3,3 volte il nominale: un errore
        # di digitazione, non un consumo reale
        a = analisi_sale([rab(0), rab(3, agg=12, liv="riserva")], 3, 1200, 14000, 0.5)
        self.assertFalse(a["g_calibrato"])
        self.assertEqual(a["cicli_scartati"], 1)
        self.assertEqual(a["g_per_rigenerazione"], 1200)

    def test_ciclo_troppo_corto_scartato(self):
        a = analisi_sale([rab(0), rab(2, agg=2, liv="riserva")], 2, 1200, 14000, 0.5)
        self.assertEqual(a["cicli_scartati"], 1)

    def test_rabbocco_senza_kg_aggiunti_non_calibra(self):
        self.assertEqual(cicli_rabbocco([rab(0), rab(10)]), [])

    def test_senza_frequenza_nessuna_previsione(self):
        a = stato(11, rate=None)
        self.assertIsNone(a["giorni_a_riserva"])
        self.assertEqual(a["stato"], "ok")


class ArchivioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def bc(self, idx=10):
        return {"idx_quarti": idx, "idx_giorni": 1, "sale_g": 0,
                "sale_per_rigenerazione_g": 1200, "capacita_g": 14000}

    def test_migrazione_del_rabbocco_vecchio(self):
        (Path(self.d) / "state.json").write_text(json.dumps({"rabbocco": {
            "data": "2026-10-28T18:16:13", "kg": 14.0,
            "rigenerazioni_al_rabbocco": 0}}))
        lista = carica_rabbocchi(self.d)
        self.assertEqual(lista[0]["kg_dopo"], 14.0)
        self.assertIsNone(lista[0]["kg_aggiunti"])
        self.assertTrue((Path(self.d) / "rabbocchi.json").exists())

    def test_pieno_usa_la_capacita_letta(self):
        Tracker(self.d).applica(datetime(2026, 10, 28, 10), self.bc(), b"\0" * 20, [], [])
        self.assertEqual(registra_rabbocco(self.d)["kg_dopo"], 14.0)

    def test_pieno_senza_capacita_chiede_i_kg(self):
        with self.assertRaises(ValueError):
            registra_rabbocco(self.d)

    def test_validazioni(self):
        for kw in ({"kg_dopo": 0}, {"kg_dopo": 500}, {"kg_dopo": 10, "kg_aggiunti": 12},
                   {"kg_dopo": 10, "livello_prima": "boh"},
                   {"kg_dopo": 10, "kg_aggiunti": -1}):
            with self.assertRaises(ValueError, msg=str(kw)):
                registra_rabbocco(self.d, **kw)
        self.assertEqual(carica_rabbocchi(self.d), [])

    def test_annulla(self):
        registra_rabbocco(self.d, 14)
        registra_rabbocco(self.d, 14, kg_aggiunti=5)
        self.assertEqual(annulla_ultimo_rabbocco(self.d)["kg_aggiunti"], 5)
        self.assertEqual(len(carica_rabbocchi(self.d)), 1)
        annulla_ultimo_rabbocco(self.d)
        with self.assertRaises(ValueError):
            annulla_ultimo_rabbocco(self.d)

    def test_rabbocco_di_un_altro_processo_non_viene_perso(self):
        # il servizio di registrazione tiene il Tracker in memoria e riscrive
        # state.json a ogni ciclo: un rabbocco registrato nel frattempo da CLI
        # o pagina web deve sopravvivere e contare nelle stime
        servizio = Tracker(self.d)
        servizio.applica(datetime(2026, 10, 28, 10), self.bc(10), b"\0" * 20, [], [])
        registra_rabbocco(self.d, 14, now=datetime(2026, 10, 28, 10, 5))   # altro processo
        r = servizio.applica(datetime(2026, 10, 28, 11), self.bc(10), b"\0" * 20, [], [])
        self.assertEqual(len(carica_rabbocchi(self.d)), 1)
        self.assertEqual(r["sale_stimato_g"], 14000)

    def test_rigenerazioni_gia_usate(self):
        Tracker(self.d).applica(datetime(2026, 10, 28, 10), self.bc(), b"\0" * 20, [], [])
        self.assertEqual(registra_rabbocco(self.d, 14, rigen_gia_usate=3)["rigenerazioni_totali"], -3)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        Tracker(cls.tmp.name).applica(
            datetime(2026, 10, 28, 10),
            {"idx_quarti": 1, "idx_giorni": 1, "sale_g": 0,
             "sale_per_rigenerazione_g": 1200, "capacita_g": 14000},
            b"\0" * 20, [], [])
        web.Handler.data_dir = cls.tmp.name
        web.Handler.pin = "4242"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        web.Handler.pin = None
        cls.tmp.cleanup()

    def post(self, path, corpo, tipo="application/json", **headers):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(corpo).encode(),
            headers={"Content-Type": tipo, **headers}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def n(self):
        return len(carica_rabbocchi(self.tmp.name))

    def test_difese_e_registrazione(self):
        ok = {"pieno": True, "kg_aggiunti": "8,5", "livello_prima": "riserva",
              "pin": "4242"}
        self.assertEqual(self.post("/api/rabbocco", ok, tipo="text/plain")[0], 415)
        self.assertEqual(self.post("/api/rabbocco", ok, Origin="http://evil.example")[0], 403)
        self.assertEqual(self.post("/api/rabbocco", {**ok, "pin": "0000"})[0], 403)
        self.assertEqual(self.post("/api/rabbocco", {**ok, "kg_aggiunti": "abc"})[0], 400)
        self.assertEqual(self.post("/api/rabbocco", {**ok, "pieno": False})[0], 400)
        self.assertEqual(self.post("/api/altro", ok)[0], 404)
        self.assertEqual(self.n(), 0)

        codice, r = self.post("/api/rabbocco", ok, Origin=self.base)
        self.assertEqual(codice, 200)
        self.assertEqual((r["rabbocco"]["kg_dopo"], r["rabbocco"]["kg_aggiunti"]), (14.0, 8.5))
        self.assertEqual(self.n(), 1)

        with urllib.request.urlopen(self.base + "/api/riepilogo") as resp:
            d = json.loads(resp.read())
        self.assertTrue(d["pin_richiesto"])
        self.assertEqual(d["analisi_sale"]["residuo_g"], 14000)
        self.assertEqual(len(d["rabbocchi"]), 1)

        self.assertEqual(self.post("/api/rabbocco/annulla", {"pin": "0000"})[0], 403)
        self.assertEqual(self.post("/api/rabbocco/annulla", {"pin": "4242"})[0], 200)
        self.assertEqual(self.n(), 0)
        self.assertEqual(self.post("/api/rabbocco/annulla", {"pin": "4242"})[0], 400)


class ParseTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(web.parse_rabbocco({"pieno": False, "kg_dopo": "12,5"})["kg_dopo"], 12.5)
        self.assertIsNone(web.parse_rabbocco({})["kg_dopo"])
        with self.assertRaises(ValueError):
            web.parse_rabbocco([])


if __name__ == "__main__":
    unittest.main()
