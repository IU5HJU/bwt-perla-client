"""Prova di installa.sh con comandi di sistema finti (python3, systemctl, sudo, apt-get,
dpkg, id, curl...) nel PATH: si controlla cosa chiede al sistema e cosa scrive."""
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent

STUB = {
    # python3 finto: -c (controllo versione), --version e -m venv DIR (crea un .venv finto)
    "python3": """#!/bin/sh
case "$1" in
  -c) [ -f "$STUB_DIR/python_vecchio" ] && exit 1; exit 0 ;;
  --version) echo "Python 3.11.2" ;;
  -m) mkdir -p "$3/bin"
      printf '#!/bin/sh\\necho "venv: $*" >> "$STUB_DIR/azioni"\\n[ -f "$STUB_DIR/selftest_ko" ] && [ "$1" = "bwt_perla.py" ] && exit 1\\nexit 0\\n' > "$3/bin/python"
      chmod +x "$3/bin/python"; echo "venv creato" >> "$STUB_DIR/azioni" ;;
esac
""",
    "sudo": '#!/bin/sh\necho "sudo $*" >> "$STUB_DIR/azioni"\nexec "$@"\n',
    "systemctl": """#!/bin/sh
echo "systemctl $*" >> "$STUB_DIR/azioni"
case "$1" in
  cat) grep -qx "$2" "$STUB_DIR/servizi_esistenti" 2>/dev/null ;;
  is-active) shift; [ "$1" = "--quiet" ] && shift
             [ "$1" = "bluetooth" ] && { [ -f "$STUB_DIR/bt_spento" ] && exit 3; exit 0; }
             [ -f "$STUB_DIR/non_parte" ] && exit 3; exit 0 ;;
esac
exit 0
""",
    "dpkg": '#!/bin/sh\ngrep -qx "$2" "$STUB_DIR/pacchetti_mancanti" 2>/dev/null && exit 1; exit 0\n',
    "apt-get": '#!/bin/sh\necho "apt-get $*" >> "$STUB_DIR/azioni"\n',
    "usermod": '#!/bin/sh\necho "usermod $*" >> "$STUB_DIR/azioni"\n',
    "getent": '#!/bin/sh\nexit 0\n',
    "hostname": '#!/bin/sh\n[ "$1" = "-I" ] && echo "192.168.1.50 " || echo "raspberrypi"\n',
    "curl": '#!/bin/sh\n[ -f "$STUB_DIR/pagina_giu" ] && exit 22\necho "$@" >> "$STUB_DIR/url"\necho ok\n',
    "id": """#!/bin/sh
case "$1" in
  -u) [ -f "$STUB_DIR/root" ] && echo 0 || echo 1000 ;;
  -un) echo pi ;;
  -nG) [ -f "$STUB_DIR/senza_gruppo" ] && echo "pi sudo" || echo "pi sudo bluetooth" ;;
esac
""",
}


class InstallaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.dir, self.stub, self.unit = t / "bwt-perla-client", t / "stub", t / "unit"
        (self.dir / "deploy").mkdir(parents=True)
        for f in ("installa.sh", "requirements.txt"):
            shutil.copy(RADICE / f, self.dir / f)
        shutil.copy(RADICE / "deploy" / "bwt-perla.service", self.dir / "deploy")
        (self.dir / "requirements.txt").write_text("")          # niente rete
        self.stub.mkdir()
        self.unit.mkdir()
        for nome, testo in STUB.items():
            p = self.stub / nome
            p.write_text(testo)
            p.chmod(0o755)
        self.socket = []
        prova = socket.socket()
        try:
            prova.bind(("127.0.0.1", 8765))
        except OSError:
            self.skipTest("la porta 8765 e' occupata su questa macchina")
        finally:
            prova.close()

    def tearDown(self):
        for s in self.socket:
            s.close()
        self.tmp.cleanup()

    def occupa(self, porta):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", porta))
        s.listen(1)
        self.socket.append(s)

    def lancia(self, *args, extra_env=None):
        env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "STUB_DIR": str(self.stub),
               "CARTELLA_UNIT": str(self.unit), "PAUSA": "0.05", "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})}
        return subprocess.run(["bash", str(self.dir / "installa.sh"), "--si", *args], cwd=self.dir,
                              env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def azioni(self):
        f = self.stub / "azioni"
        return f.read_text() if f.exists() else ""

    def flag(self, nome, testo=""):
        (self.stub / nome).write_text(testo)

    def unit_testo(self):
        return (self.unit / "bwt-perla.service").read_text()

    # ── casi ────────────────────────────────────────────────────────────────

    def test_installazione_completa(self):
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        u = self.unit_testo()
        self.assertIn("User=pi", u)
        self.assertIn(f"WorkingDirectory={self.dir}", u)
        self.assertIn(f"ExecStart={self.dir}/.venv/bin/python bwt_perla.py serve --port 8765", u)
        self.assertNotIn("User=UTENTE", u)
        self.assertNotIn("/home/UTENTE", u)
        a = self.azioni()
        for atteso in ("venv creato", "venv: -m pip install", "venv: bwt_perla.py selftest",
                       "systemctl daemon-reload", "systemctl enable bwt-perla", "systemctl restart bwt-perla"):
            self.assertIn(atteso, a)
        self.assertNotIn("apt-get", a)                       # niente da installare
        self.assertNotIn("usermod", a)                       # gia' nel gruppo bluetooth
        for atteso in ("http://192.168.1.50:8765/", "http://raspberrypi.local:8765/", "Esperto", "Impostazioni"):
            self.assertIn(atteso, r.stdout)

    def test_pacchetti_mancanti_si_installano(self):
        self.flag("pacchetti_mancanti", "bluez\nrfkill\n")
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        a = self.azioni()
        self.assertIn("apt-get install -y -qq bluez rfkill", a)

    def test_utente_non_nel_gruppo_bluetooth_viene_aggiunto(self):
        self.flag("senza_gruppo")
        self.assertEqual(self.lancia().returncode, 0)
        self.assertIn("usermod -aG bluetooth pi", self.azioni())

    def test_bluetooth_spento_viene_acceso(self):
        self.flag("bt_spento")
        self.assertEqual(self.lancia().returncode, 0)
        self.assertIn("systemctl enable --now bluetooth", self.azioni())

    def test_porta_occupata_sceglie_la_prossima(self):
        self.occupa(8765)
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--port 8766", self.unit_testo())
        self.assertIn(":8766/", r.stdout)

    def test_porta_richiesta(self):
        r = self.lancia("--porta", "8080")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--port 8080", self.unit_testo())

    def test_porta_richiesta_occupata_e_errore(self):
        self.occupa(8081)
        r = self.lancia("--porta", "8081")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gia' usata", r.stderr)
        self.assertFalse((self.unit / "bwt-perla.service").exists())

    def test_porta_non_valida(self):
        for p in ("abc", "80", "70000"):
            self.assertNotEqual(self.lancia("--porta", p).returncode, 0, p)

    def test_reinstallazione_tiene_la_porta_di_prima(self):
        self.assertEqual(self.lancia("--porta", "8080").returncode, 0)
        self.occupa(8765)
        r = self.lancia()                                    # senza --porta
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("--port 8080", self.unit_testo())

    def test_senza_servizio(self):
        r = self.lancia("--senza-servizio")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.unit / "bwt-perla.service").exists())
        self.assertNotIn("systemctl", self.azioni())
        self.assertIn("bwt_perla.py serve --port 8765", r.stdout)

    def test_vecchi_servizi_separati_vengono_fermati(self):
        self.flag("servizi_esistenti", "bwt-perla-log\nbwt-perla-web\n")
        self.assertEqual(self.lancia().returncode, 0)
        a = self.azioni()
        self.assertIn("systemctl disable --now bwt-perla-log", a)
        self.assertIn("systemctl disable --now bwt-perla-web", a)

    def test_servizio_che_non_parte(self):
        self.flag("non_parte")
        r = self.lancia()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("journalctl -u bwt-perla", r.stderr)

    def test_pagina_che_non_risponde(self):
        self.flag("pagina_giu")
        r = self.lancia()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("la pagina non risponde", r.stderr)

    def test_prova_interna_fallita(self):
        self.flag("selftest_ko")
        r = self.lancia()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("prova interna", r.stderr)
        self.assertFalse((self.unit / "bwt-perla.service").exists())

    def test_python_troppo_vecchio(self):
        self.flag("python_vecchio")
        r = self.lancia()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Python 3.9", r.stderr)

    def test_rifiuta_root(self):
        self.flag("root")
        r = self.lancia()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("non lanciarlo come root", r.stderr)

    def test_cartella_incompleta(self):
        (self.dir / "deploy" / "bwt-perla.service").unlink()
        r = self.lancia()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("deploy/bwt-perla.service", r.stderr)

    def test_disinstalla(self):
        self.assertEqual(self.lancia().returncode, 0)
        self.assertTrue((self.unit / "bwt-perla.service").exists())
        r = self.lancia("--disinstalla")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.unit / "bwt-perla.service").exists())
        self.assertIn("systemctl disable --now bwt-perla", self.azioni())
        self.assertTrue((self.dir / "installa.sh").exists())

    def test_opzione_sconosciuta(self):
        r = self.lancia("--boh")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Opzione sconosciuta", r.stderr)

    def test_percorso_con_caratteri_speciali_nel_servizio(self):
        spec = Path(self.tmp.name) / "a&b|c"
        shutil.copytree(self.dir, spec)
        env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "STUB_DIR": str(self.stub),
               "CARTELLA_UNIT": str(self.unit), "PAUSA": "0.05"}
        r = subprocess.run(["bash", str(spec / "installa.sh"), "--si"], cwd=spec, env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"WorkingDirectory={spec}", self.unit_testo())


if __name__ == "__main__":
    unittest.main()
