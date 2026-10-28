import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


class StaticiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        web.Handler.data_dir = cls.tmp.name
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_tutti_i_file_ammessi_esistono_e_hanno_il_tipo_giusto(self):
        for percorso, (nome, tipo) in web.STATICI.items():
            self.assertTrue((web.WEB / nome).is_file(), nome)
            codice, h, corpo = self.get(percorso)
            self.assertEqual(codice, 200, percorso)
            self.assertEqual(h["Content-Type"], tipo, percorso)
            self.assertIn("max-age", h["Cache-Control"])
            if tipo == "image/png":
                self.assertTrue(corpo.startswith(PNG), percorso)

    def test_manifest_valido_e_icone_raggiungibili(self):
        m = json.loads(self.get("/manifest.webmanifest")[2])
        self.assertEqual(m["display"], "standalone")
        self.assertTrue({"any", "maskable"} <= {i["purpose"] for i in m["icons"]})
        for icona in m["icons"]:
            self.assertEqual(self.get("/" + icona["src"])[0], 200, icona["src"])
            self.assertIn(icona["src"].replace("icons/", "/icons/"), web.STATICI)

    def test_la_pagina_dichiara_icone_e_manifest(self):
        html = self.get("/")[2].decode()
        for tag in ('rel="manifest"', 'rel="apple-touch-icon"', 'rel="icon"', 'name="theme-color"'):
            self.assertIn(tag, html)

    def test_niente_percorsi_fuori_dall_elenco(self):
        for percorso in ("/icons/../web.py", "/icons/..%2fweb.py", "/web.py", "/icons/inesistente.png",
                         "/../tracker.py", "/icons/"):
            self.assertEqual(self.get(percorso)[0], 404, percorso)

    def test_le_api_restano_senza_cache(self):
        self.assertEqual(self.get("/api/riepilogo")[1]["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
