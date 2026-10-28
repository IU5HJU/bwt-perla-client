import asyncio
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
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bwt_perla  # noqa: E402
import web  # noqa: E402
from config import carica_config, salva_config, valida_mac  # noqa: E402
from tracker import Tracker  # noqa: E402

# dati reali dell'apparecchio in uso (Raspberry Pi 3B, 28/10/2026)
ADV_BWT = SimpleNamespace(
    service_uuids=[], rssi=-61, local_name=None,
    manufacturer_data={76: b"\x02\x15BestWaterTechno\x00Y\x00.\x00\xc8"})
BROADCAST = bytes.fromhex("00 00 00 00 f7 09 79 00 b0 04 0e 00 06 01 12 54 00 00 00 00")
MAC = "03:12:00:2E:00:59"


class ConfigTest(unittest.TestCase):
    def test_valida_mac(self):
        self.assertEqual(valida_mac(" 03:12:00:2e:00:59 "), MAC)
        for brutto in ("", None, "03:12:00:2E:00", "ZZ:12:00:2E:00:59", "03-12-00-2E-00-59"):
            with self.assertRaises(ValueError, msg=str(brutto)):
                valida_mac(brutto)

    def test_salva_e_carica(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(carica_config(d), {})
            salva_config(d, mac="03:12:00:2e:00:59")
            self.assertEqual(carica_config(d), {"mac": MAC})
            (Path(d) / "config.json").write_text("non e' json")
            self.assertEqual(carica_config(d), {})      # file rovinato: si riparte


class TrackerDispositivoTest(unittest.TestCase):
    def test_cambio_di_dispositivo_riparte_da_adesso(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Tracker(d)
            self.assertFalse(tr.dispositivo(MAC))          # primo: nessun reset
            tr.state["last_idx"] = 100
            self.assertFalse(tr.dispositivo(MAC))          # stesso: niente
            self.assertEqual(tr.state["last_idx"], 100)
            self.assertTrue(tr.dispositivo("AA:BB:CC:DD:EE:FF"))
            self.assertIsNone(tr.state["last_idx"])
            self.assertEqual(Tracker(d).state["mac"], "AA:BB:CC:DD:EE:FF")   # persistito


class RiconoscimentoTest(unittest.TestCase):
    def test_is_bwt(self):
        self.assertTrue(bwt_perla.is_bwt(ADV_BWT))                       # dato reale
        self.assertTrue(bwt_perla.is_bwt(SimpleNamespace(
            service_uuids=[bwt_perla.U_SERVICE.upper()], manufacturer_data={})))
        altro = SimpleNamespace(service_uuids=[], manufacturer_data={76: b"\x02\x15" + b"\x01" * 21})
        self.assertFalse(bwt_perla.is_bwt(altro))
        self.assertFalse(bwt_perla.is_bwt(SimpleNamespace(service_uuids=[], manufacturer_data={})))

    def test_scansiona_mette_i_bwt_per_primi(self):
        altro = SimpleNamespace(service_uuids=[], rssi=-40, local_name="Cuffie", manufacturer_data={})
        trovati = {"11:22:33:44:55:66": (SimpleNamespace(name=None), altro),
                   MAC.lower(): (SimpleNamespace(name=None), ADV_BWT)}
        with mock.patch.object(bwt_perla.BleakScanner, "discover",
                               new=mock.AsyncMock(return_value=trovati)):
            r = asyncio.run(bwt_perla.scansiona(1))
        self.assertEqual([x["mac"] for x in r], [MAC, "11:22:33:44:55:66"])
        self.assertEqual((r[0]["bwt"], r[1]["bwt"], r[1]["nome"]), (True, False, "Cuffie"))

    def test_find_senza_bwt(self):
        with mock.patch.object(bwt_perla, "scansiona", new=mock.AsyncMock(return_value=[])):
            with self.assertRaises(LookupError):
                asyncio.run(bwt_perla.find(None))
        self.assertEqual(asyncio.run(bwt_perla.find("AA:BB:CC:DD:EE:FF")), "AA:BB:CC:DD:EE:FF")


class FakeClient:
    """BleakClient finto: legge il BROADCAST reale; o fallisce come un
    dispositivo che non e' un addolcitore BWT."""
    def __init__(self, mac, timeout=0, broadcast=BROADCAST, non_bwt=False):
        self.broadcast, self.non_bwt = broadcast, non_bwt

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def start_notify(self, uuid, cb):
        if self.non_bwt:
            raise RuntimeError("characteristic not found")

    async def read_gatt_char(self, uuid):
        return self.broadcast


class VerificaTest(unittest.TestCase):
    def esegui(self, mac=MAC, **kw):
        with mock.patch.object(bwt_perla, "BleakClient", lambda m, timeout=0: FakeClient(m, timeout, **kw)):
            return asyncio.run(bwt_perla.verifica(mac))

    def test_dati_reali(self):
        v = self.esegui()
        self.assertEqual((v["firmware"], v["capacita_kg"], v["sale_per_rigenerazione_g"]),
                         ("A22X V1.18", 14.0, 1200))
        self.assertTrue(v["testato"])
        self.assertFalse(v["allarme_sale"])

    def test_firmware_non_provato(self):
        b = bytearray(BROADCAST); b[13], b[14] = 2, 5
        self.assertFalse(self.esegui(broadcast=bytes(b))["testato"])

    def test_non_e_un_addolcitore(self):
        with self.assertRaisesRegex(RuntimeError, "non riuscita"):
            self.esegui(non_bwt=True)

    def test_risposta_troppo_corta(self):
        with self.assertRaisesRegex(RuntimeError, "non riconosciuta"):
            self.esegui(broadcast=b"\x00\x01")

    def test_mac_non_valido(self):
        with self.assertRaises(ValueError):
            self.esegui(mac="boh")

    def test_bluetooth_assente(self):
        def senza(mac, timeout=0):
            raise FileNotFoundError(2, "No such file or directory")
        with mock.patch.object(bwt_perla, "BleakClient", senza):
            with self.assertRaisesRegex(RuntimeError, "Bluetooth non disponibile"):
                asyncio.run(bwt_perla.verifica(MAC))


class LoggerTest(unittest.TestCase):
    def test_usa_il_mac_della_configurazione(self):
        visti = []

        async def finto_ciclo(mac, tr):
            visti.append(mac)
            return {"timestamp": "t", "litri_nuovi": 0, "litri_totali": 0,
                    "rigenerazioni_totali": 0, "sale_stimato_g": "", "autonomia_giorni": ""}

        with tempfile.TemporaryDirectory() as d:
            salva_config(d, mac=MAC)
            with mock.patch.object(bwt_perla, "ciclo", finto_ciclo):
                asyncio.run(bwt_perla.logger(None, d, 900, once=True, scansione_automatica=False))
            self.assertEqual(visti, [MAC])
            self.assertEqual(Tracker(d).state["mac"], MAC)

    def test_senza_configurazione_non_scansiona_da_solo_in_serve(self):
        with tempfile.TemporaryDirectory() as d:
            scan = mock.AsyncMock()
            with mock.patch.object(bwt_perla, "find", scan):
                with self.assertRaises(SystemExit):
                    asyncio.run(bwt_perla.logger(None, d, 900, once=True, scansione_automatica=False))
            scan.assert_not_called()


class PonteTest(unittest.TestCase):
    def test_scan_e_salvataggio_passano_dal_ciclo_asyncio(self):
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        self.addCleanup(lambda: loop.call_soon_threadsafe(loop.stop))
        lock, evento = asyncio.Lock(), asyncio.Event()
        with tempfile.TemporaryDirectory() as d:
            ponte = bwt_perla.PonteBle(loop, lock, evento, d)
            with mock.patch.object(bwt_perla, "scansiona",
                                   new=mock.AsyncMock(return_value=[{"mac": MAC}])):
                self.assertEqual(ponte.scan(), [{"mac": MAC}])
            ponte.salva_mac(MAC.lower())
            self.assertEqual(carica_config(d)["mac"], MAC)
            # il salvataggio sveglia subito il registratore
            asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(evento.wait(), 2), loop).result(3)


class FintoPonte:
    def __init__(self):
        self.salvati = []

    def scan(self):
        return [{"mac": MAC, "nome": "", "rssi": -61, "bwt": True}]

    def verifica(self, mac):
        if mac.startswith("AA"):
            raise RuntimeError("connessione o lettura non riuscita")
        return {"mac": mac, "firmware": "A22X V1.18", "testato": True}

    def salva_mac(self, mac):
        self.salvati.append(mac)
        return {"mac": mac}


class PaginaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        web.Handler.data_dir = cls.tmp.name
        web.Handler.pin = None
        web.Handler.ble = None
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        web.Handler.ble = None
        web.Handler.pin = None
        cls.tmp.cleanup()

    def post(self, path, corpo, **h):
        req = urllib.request.Request(self.base + path, data=json.dumps(corpo).encode(),
                                     headers={"Content-Type": "application/json", **h}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def riepilogo(self):
        with urllib.request.urlopen(self.base + "/api/riepilogo") as r:
            return json.loads(r.read())

    def test_senza_ponte_scan_e_verifica_non_disponibili_ma_la_config_si_salva(self):
        web.Handler.ble = None
        self.assertEqual(self.post("/api/scan", {})[0], 501)
        self.assertEqual(self.post("/api/verifica", {"mac": MAC})[0], 501)
        self.assertFalse(self.riepilogo()["ble_disponibile"])
        codice, r = self.post("/api/config", {"mac": MAC.lower()})
        self.assertEqual((codice, r["config"]["mac"]), (200, MAC))
        self.assertEqual(self.riepilogo()["config"]["mac"], MAC)

    def test_con_il_ponte(self):
        ponte = web.Handler.ble = FintoPonte()
        self.assertTrue(self.riepilogo()["ble_disponibile"])
        self.assertEqual(self.post("/api/scan", {})[1]["dispositivi"][0]["mac"], MAC)
        self.assertEqual(self.post("/api/verifica", {"mac": MAC})[1]["dispositivo"]["firmware"],
                         "A22X V1.18")
        codice, r = self.post("/api/verifica", {"mac": "AA:BB:CC:DD:EE:FF"})
        self.assertEqual(codice, 502)
        self.assertIn("non riuscita", r["errore"])
        self.assertEqual(self.post("/api/verifica", {"mac": "boh"})[0], 400)
        self.assertEqual(self.post("/api/config", {"mac": MAC})[0], 200)
        self.assertEqual(ponte.salvati, [MAC])
        self.assertEqual(self.post("/api/config", {"mac": "boh"})[0], 400)
        self.assertEqual(self.post("/api/config", {})[0], 400)
        self.assertEqual(self.post("/api/scan", {}, Origin="http://evil.example")[0], 403)

    def test_bluetooth_assente_da_un_messaggio_comprensibile(self):
        class SenzaBluetooth(FintoPonte):
            def scan(self):
                raise FileNotFoundError(2, "No such file or directory")
        web.Handler.ble = SenzaBluetooth()
        codice, r = self.post("/api/scan", {})
        self.assertEqual(codice, 502)
        self.assertIn("Bluetooth non disponibile", r["errore"])

    def test_il_pin_vale_anche_per_scansione_e_configurazione(self):
        web.Handler.ble = FintoPonte()
        web.Handler.pin = "4242"
        try:
            for percorso in ("/api/scan", "/api/verifica", "/api/config"):
                self.assertEqual(self.post(percorso, {"mac": MAC, "pin": "0000"})[0], 403, percorso)
            self.assertEqual(self.post("/api/scan", {"pin": "4242"})[0], 200)
        finally:
            web.Handler.pin = None


if __name__ == "__main__":
    unittest.main()
